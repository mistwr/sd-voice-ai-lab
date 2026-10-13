"""Consent-aware registration of outbound voice contacts in SD Dialer."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

logger = logging.getLogger("lumin-web-agent.lead-capture")


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _clean(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _clean_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", _clean(value, 120)).strip(" \t\r\n.,;:")
    if not 2 <= len(name) <= 120 or "?" in name:
        return ""
    folded = _fold(name)
    if folded in {
        "sim", "nao", "claro", "pois", "certo", "ok", "okay", "obrigado",
        "obrigada", "nao sei", "prefiro nao", "pode", "contacto", "cliente",
        "lead", "desconhecido",
    }:
        return ""
    if re.search(r"\b(?:quero|preciso|interessado|interessada|obrigado|obrigada|contacto|telefone|empresa)\b", folded):
        return ""
    if len(name.split()) > 5:
        return ""
    return name


def _name_from_answer(value: str) -> str:
    raw = re.sub(r"\s+", " ", value).strip()
    prefix = re.match(
        r"^(?:chamo-me|me chamo|o meu nome (?:é|e)|sou (?:o|a)|é (?:o|a))\s+",
        raw,
        flags=re.IGNORECASE,
    )
    if prefix:
        raw = raw[prefix.end():]
    raw = re.split(r"[,;.!?]", raw, maxsplit=1)[0].strip()
    raw = re.sub(
        r"\s+(?:e\s+)?(?:estou|quero|preciso|trabalho|sou|tenho|gostava|gostaria)\b.*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    return _clean_name(raw)


def _asks_for_name(text: str) -> bool:
    folded = _fold(text)
    return bool(re.search(
        r"\b(?:qual (?:e|é) o seu nome|como se chama|diga-me o seu nome|pode dizer o seu nome|qual e o seu nome completo)\b",
        folded,
    ))


def _asks_for_consent(text: str) -> bool:
    folded = _fold(text)
    return bool(
        re.search(r"\bposso (?:guardar|registar|anotar)\b", folded)
        or re.search(r"\bautoriza (?:que )?(?:guardemos|registemos|anotemos)\b", folded)
        or re.search(r"\bconsente (?:que )?(?:guardemos|registemos|anotemos)\b", folded)
    )


def _is_opt_out(text: str) -> bool:
    folded = _fold(text)
    patterns = (
        r"\bnao me lig(?:ue|uem|ues)\b",
        r"\bnao me contact(?:e|em)\b",
        r"\bnao quero (?:mais )?(?:chamadas?|contactos?)\b",
        r"\bnao voltem a ligar\b",
        r"\bnao quero ser contactad[oa]\b",
        r"\b(?:retirem|removam|apaguem|eliminem) (?:o meu )?(?:contacto|numero|nome|da lista)\b",
        r"\bretirem-me da lista\b",
        r"\bapaguem os meus dados\b",
    )
    return any(re.search(pattern, folded) for pattern in patterns)


def _consent_answer(text: str) -> str | None:
    folded = _fold(text).strip()
    if not folded:
        return None
    if _is_opt_out(folded):
        return "no"
    if re.search(r"\bnao\b|\bnem pensar\b|\bprefiro que nao\b|\bnao autorizo\b|\bnao consinto\b", folded):
        return "no"
    if re.search(r"\b(talvez|nao sei|depende|para que|por que)\b", folded):
        return None
    if folded.startswith(("sim", "autorizo", "aceito", "concordo", "claro", "de acordo", "sem problema", "esta bem", "tudo bem")):
        return "yes"
    if folded.startswith("pode guardar") or folded.startswith("pode registar") or folded.startswith("pode anotar"):
        return "yes"
    if folded in {"pode", "podem", "forca"}:
        return "yes"
    return None


@dataclass
class LeadCapture:
    call_id: str
    phone: str
    name: str = ""
    consent_requested: bool = False
    consented: bool = False
    denied: bool = False
    name_requested: bool = False
    consent_at: datetime | None = None

    @classmethod
    def from_job(cls, metadata: Mapping[str, Any] | None) -> LeadCapture | None:
        if not isinstance(metadata, Mapping):
            return None
        mode = _clean(metadata.get("mode"), 40).lower()
        source = _clean(metadata.get("source"), 80).lower().replace("_", "-")
        if mode != "outbound" or source in {"sd-dialer", "sd dialer"}:
            return None
        call_id = _clean(metadata.get("callId") or metadata.get("call_id"), 128)
        phone = _clean(metadata.get("phone"), 80)
        if not call_id or not phone:
            return None
        return cls(
            call_id=call_id,
            phone=phone,
            name=_clean_name(metadata.get("name")),
        )

    @property
    def ready(self) -> bool:
        return bool(self.consented and not self.denied and self.name and self.phone and self.consent_at)

    def observe(self, role: Any, text: Any) -> None:
        content = _clean(text, 4000)
        if not content:
            return
        role_name = _clean(role, 40).lower()
        if role_name == "assistant":
            if _asks_for_name(content) and not self.name:
                self.name_requested = True
            if _asks_for_consent(content):
                self.consent_requested = True
            return
        if role_name not in {"user", "human"}:
            return

        if _is_opt_out(content):
            self.denied = True
            self.consented = False
            self.consent_at = None
            return

        if self.consent_requested and not self.denied:
            answer = _consent_answer(content)
            if answer == "yes":
                self.consented = True
                self.consent_at = datetime.now(timezone.utc)
            elif answer == "no":
                self.denied = True
                self.consented = False
                self.consent_at = None

        if self.name_requested and not self.name:
            self.name = _name_from_answer(content)
            if self.name:
                self.name_requested = False

    async def register(self) -> bool:
        if not self.ready:
            logger.info("SD Dialer lead capture skipped: no complete consented lead")
            return False

        base_url = os.getenv("SD_DIALER_SUPABASE_URL", "").strip().rstrip("/")
        anon_key = os.getenv("SD_DIALER_SUPABASE_ANON_KEY", "").strip()
        api_key = os.getenv("SD_DIALER_LEAD_API_KEY", "").strip()
        if not base_url or not anon_key or not api_key:
            logger.warning("SD Dialer lead capture is not configured")
            return False

        payload = {
            "p_api_key": api_key,
            "p_call_id": self.call_id,
            "p_name": self.name,
            "p_phone": self.phone,
            "p_consent_to_contact": True,
            "p_consent_at": self.consent_at.isoformat() if self.consent_at else None,
        }
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{base_url}/rest/v1/rpc/lumin_voice_register_lead"

        async with httpx.AsyncClient(timeout=4.0) as client:
            for attempt in range(2):
                try:
                    response = await client.post(endpoint, headers=headers, json=payload)
                except httpx.HTTPError as exc:
                    logger.warning(
                        "SD Dialer lead capture request failed",
                        extra={"error_type": type(exc).__name__, "attempt": attempt + 1},
                    )
                    if attempt == 0:
                        await asyncio.sleep(0.2)
                        continue
                    return False

                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                if not response.is_success:
                    logger.warning(
                        "SD Dialer rejected lead capture",
                        extra={"status_code": response.status_code},
                    )
                    return False

                try:
                    result = response.json()
                except ValueError:
                    logger.warning("SD Dialer lead capture returned invalid JSON")
                    return False
                logger.info(
                    "SD Dialer lead capture completed",
                    extra={
                        "created": bool(result.get("created")) if isinstance(result, dict) else False,
                        "replayed": bool(result.get("replayed")) if isinstance(result, dict) else False,
                    },
                )
                return True

        return False


def lead_capture_instructions() -> str:
    return """

REGISTO DE CONTACTO NO SD DIALER
- Este é um contacto telefónico de saída. Mantém a conversa normal e só propõe acompanhamento se a pessoa mostrar interesse real ou pedir contacto posterior.
- Se ainda não souberes o nome, pergunta como a pessoa se chama. Não inventes nomes, empresas nem dados.
- No fim, pergunta exatamente: "Posso guardar o seu nome e este número no nosso sistema comercial para a equipa lhe dar seguimento por telefone?"
- Só considera autorização uma resposta afirmativa clara a essa pergunta específica. Um "sim" a outra pergunta não conta.
- Se a resposta for ambígua, explica brevemente para que serve e volta a perguntar uma única vez. Se a pessoa recusar ou pedir para não voltar a ser contactada, respeita e não insistas.
- Não peças email, NIF, morada, dados financeiros ou outros dados pessoais para este registo.
- Não digas que o contacto já foi guardado; o registo só acontece depois de a chamada terminar.
"""
