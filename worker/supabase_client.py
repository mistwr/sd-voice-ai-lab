"""
Cliente Supabase usado apenas pelo Voice Worker (Python).

Usa a SERVICE ROLE KEY — que ignora RLS — porque o worker corre num
ambiente de confiança (container próprio), nunca no frontend/browser.
Todas as escritas ficam sempre com o company_id correto vindo do
dispatch (dial info), nunca hardcoded.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("sd-voice-ai-lab.supabase")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_HEADERS = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


class SupabaseLogger:
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY em falta nas variáveis de ambiente do worker.")
        self._client = httpx.AsyncClient(base_url=SUPABASE_URL, headers=_HEADERS, timeout=10)

    async def is_stopped(self, company_id: str) -> bool:
        try:
            r = await self._client.get("/rest/v1/voice_settings", params={"company_id": f"eq.{company_id}", "select": "stop_calls"})
            r.raise_for_status()
            rows = r.json()
            return bool(rows and rows[0].get("stop_calls"))
        except Exception:
            logger.exception("Falha a verificar STOP CALLS — a assumir false por defeito")
            return False

    async def update_call(self, call_id: str, **fields):
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            r = await self._client.patch("/rest/v1/voice_calls", params={"id": f"eq.{call_id}"}, json=fields)
            r.raise_for_status()
        except Exception:
            logger.exception("Falha a atualizar voice_calls id=%s", call_id)

    async def log_event(self, call_id: str, event_type: str, payload: dict):
        try:
            r = await self._client.get("/rest/v1/voice_calls", params={"id": f"eq.{call_id}", "select": "company_id"})
            r.raise_for_status()
            rows = r.json()
            company_id = rows[0]["company_id"] if rows else None
            r = await self._client.post("/rest/v1/voice_call_events", json={"call_id": call_id, "company_id": company_id, "event_type": event_type, "payload": payload})
            r.raise_for_status()
        except Exception:
            logger.exception("Falha a registar evento %s para call_id=%s", event_type, call_id)

    async def log_transcript(self, call_id: str, speaker: str, text: str, is_interruption: bool = False):
        try:
            r = await self._client.get("/rest/v1/voice_calls", params={"id": f"eq.{call_id}", "select": "company_id"})
            r.raise_for_status()
            rows = r.json()
            company_id = rows[0]["company_id"] if rows else None
            r = await self._client.post("/rest/v1/voice_call_transcripts", json={"call_id": call_id, "company_id": company_id, "speaker": speaker, "text": text, "is_interruption": is_interruption})
            r.raise_for_status()
        except Exception:
            logger.exception("Falha a registar transcrição para call_id=%s", call_id)

    async def record_opt_out(self, company_id: str, phone_number: str, call_id: str, reason: str):
        try:
            r = await self._client.post(
                "/rest/v1/voice_opt_outs",
                json={"company_id": company_id, "phone_number": phone_number, "call_id": call_id, "reason": reason},
                headers={**_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            r.raise_for_status()
        except Exception:
            logger.exception("Falha a registar opt-out para %s", phone_number)

    async def summarize_call(self, call_id: str, transcript: list[dict]) -> str:
        if not transcript:
            return "Sem transcrição disponível."
        agent_lines = [t["text"] for t in transcript if t["speaker"] == "agent"]
        client_lines = [t["text"] for t in transcript if t["speaker"] == "client"]
        return (
            f"{len(transcript)} intervenções. "
            f"Última resposta do cliente: \"{client_lines[-1] if client_lines else '—'}\". "
            f"Último ponto da agente: \"{agent_lines[-1] if agent_lines else '—'}\"."
        )
