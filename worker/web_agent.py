"""
LUMIN Voice Agent
Stable production voice path for luminai.pt and outbound SIP calls.

Pipeline:
WebRTC/SIP -> Deepgram STT -> GPT-5.6 Luna -> selectable neural PT-PT / Piper -> WebRTC/SIP
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, JobProcess, RunContext, TurnHandlingOptions, WorkerOptions, cli, function_tool, inference, tts

from piper_tts import PiperTTS
from chatterbox_tts import ChatterboxRemoteTTS
from kokoro_remote_tts import KokoroRemoteTTS
from openvoice_tts import OpenVoiceRemoteTTS
from piper_catalog_tts import PiperCatalogRemoteTTS
from lead_capture import LeadCapture, lead_capture_instructions

load_dotenv(".env.local")
load_dotenv()

from supabase_client import SupabaseLogger

logger = logging.getLogger("lumin-web-agent")
logger.setLevel(logging.INFO)

_PIPER: PiperTTS | None = None


def get_piper() -> PiperTTS:
    global _PIPER
    if _PIPER is None:
        model_path = os.getenv("PIPER_MODEL", "/app/voices/lumin-ptpt.onnx")
        logger.info("loading local Piper PT-PT voice", extra={"model": model_path})
        _PIPER = PiperTTS(
            model_path,
            length_scale=1.08,
            noise_scale=0.38,
            noise_w_scale=0.43,
            volume=1.00,
        )
        logger.info(
            "local Piper PT-PT voice ready",
            extra={
                "provider": _PIPER.provider,
                "model": _PIPER.model,
                "sample_rate": _PIPER.sample_rate,
            },
        )
    return _PIPER


VOICE_PROFILES = {
    "natural": dict(length_scale=1.08, noise_scale=0.38, noise_w_scale=0.43, volume=1.00),
    "clear": dict(length_scale=1.11, noise_scale=0.30, noise_w_scale=0.36, volume=1.01),
    "commercial": dict(length_scale=1.07, noise_scale=0.40, noise_w_scale=0.45, volume=1.02),
}


def voice_for_profile(base_tts: PiperTTS, voice_id: str) -> PiperTTS:
    settings = VOICE_PROFILES.get(voice_id, VOICE_PROFILES["natural"])
    return base_tts.with_profile(**settings)

def open_source_ptpt_tts(
    base_tts: PiperTTS,
    voice_id: str,
    kokoro_url: str,
) -> tuple[Any, str]:
    """Use Kokoro PT-PT first and fall back to local Piper if it fails."""
    local_piper = voice_for_profile(base_tts, voice_id)
    if not kokoro_url:
        logger.warning("Kokoro URL missing; using local Piper PT-PT")
        return local_piper, "piper"

    kokoro = KokoroRemoteTTS(kokoro_url)
    fallback = tts.FallbackAdapter(
        tts=[kokoro, local_piper],
        # Do not spend time retrying a rate-limited or unavailable primary TTS.
        max_retry_per_tts=0,
        sample_rate=max(kokoro.sample_rate, local_piper.sample_rate),
    )

    def _log_provider_availability(ev: Any) -> None:
        log = logger.info if ev.available else logger.warning
        log(
            "open-source TTS provider availability changed",
            extra={
                "provider": getattr(ev.tts, "provider", "unknown"),
                "model": getattr(ev.tts, "model", "unknown"),
                "available": bool(ev.available),
            },
        )

    fallback.on("tts_availability_changed", _log_provider_availability)
    return fallback, "kokoro-ptpt-with-piper-fallback"


def prewarm(proc: JobProcess) -> None:
    proc.userdata["lumin_tts"] = get_piper()
    logger.info("Piper PT-PT prewarm complete")


def _clean(value: Any, limit: int = 1800) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _voice_id_from_profile(profile: dict[str, Any]) -> str:
    direct = _clean(profile.get("voice"), 120)
    if direct:
        return direct
    tone = _clean(profile.get("tone"), 300)
    match = re.match(r"^\[VOICE:(natural|clear|commercial)\]\s*", tone, flags=re.IGNORECASE)
    return match.group(1).lower() if match else "natural"


def _tone_without_voice_tag(profile: dict[str, Any]) -> str:
    tone = _clean(profile.get("tone"), 300)
    return re.sub(r"^\[VOICE:(?:natural|clear|commercial)\]\s*", "", tone, flags=re.IGNORECASE)


# Keep the Compara+ call short, personal and focused on booking a useful next step.
COMPARA_SCRIPT_VERSION = 7
COMPARA_RESTORED_SCRIPT = {
    "id": "lumin-telecom",
    "name": "Lumin",
    "description": "Lumin é o assistente virtual da Compara+. Conversa de forma breve e natural, percebe o que a pessoa gostaria de melhorar e encaminha o interesse para um consultor.",
    "objective": "Perceber a necessidade principal e, havendo interesse, agendar um contacto com um consultor para comparar opções de internet e televisão.",
    "opening": "Boa tarde, [nome]. Sou o Lumin, assistente virtual da Compara+. O que gostava de melhorar na sua internet?",
    "product": "A Compara+ compara opções residenciais de internet e televisão. Não inventes preços, cobertura, poupanças ou condições. Ouve a necessidade concreta e oferece uma comparação com um consultor, sem compromisso.",
    "offer": "Quando a pessoa mostrar uma necessidade ou interesse, oferece um contacto com um consultor para comparar opções. Não prometas poupança, preço, cobertura ou mudança de operador.",
    "objections": "RECUSA CLARA: agradece e termina sem insistir. SATISFEITO: reconhece e oferece uma comparação sem compromisso uma única vez; se não quiser, termina. PEDIDO PARA NÃO VOLTAR A CONTACTAR: confirma brevemente e termina.",
    "notes": """CONVERSA COMERCIAL DIRETA: 1) Usa apenas o nome recebido da lead ou do campo manual. Nunca procures nomes noutras leads nem perguntes o nome. 2) Não perguntes titularidade, operadora, fidelização, preços, morada, tecnologia, email ou se podes guardar o contacto. Não perguntes sobre gravação ou transcrição. 3) A abertura pergunta o que gostaria de melhorar na internet. Responde ao que a pessoa disser, reconhece a necessidade em poucas palavras e não repitas perguntas já respondidas. 4) Se houver uma necessidade concreta, oferece um contacto com consultor e pergunta o melhor dia e hora. Se a pessoa der logo dia e hora, agenda sem perguntar novamente. Se aceitar mas faltar a hora, pergunta só a hora. 5) Chama schedule_follow_up uma vez, com data/hora ISO 8601 no fuso Europe/Lisbon. Só confirmes a marcação se a ferramenta devolver sucesso. 6) Se não houver interesse, agradece e termina. 7) Fala como numa conversa breve, com respostas de até 15 palavras antes de uma pergunta; sem confirmações automáticas ou discurso de guião.""",
    "tone": "Português de Portugal, próximo, natural e direto. Frases curtas, uma pergunta de cada vez, sem tom de questionário ou insistência."
}


def _campaign_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile or {}
    company = re.sub(r"[^a-z0-9]", "", _clean(profile.get("company"), 120).casefold())
    name = _clean(profile.get("name"), 80).casefold()
    profile_id = _clean(profile.get("id"), 80)
    profile_key = re.sub(r"[^a-z0-9]", "", profile_id.casefold())
    is_compara_telecom = profile_key in {
        "comparatelecomricardo",
        "comparamaisricardonatural",
    } or (
        "compara" in company
        and (profile_key == "lumintelecom" or name.startswith(("ricardo", "lumin")))
    )
    try:
        version = int(profile.get("scriptVersion") or 0)
    except (TypeError, ValueError):
        version = 0
    if is_compara_telecom and version < COMPARA_SCRIPT_VERSION:
        return {**profile, **COMPARA_RESTORED_SCRIPT, "scriptVersion": COMPARA_SCRIPT_VERSION}
    return profile


def build_instructions(
    profile: dict[str, Any] | None = None,
    follow_up_enabled: bool = False,
    current_local_time: str = "",
) -> str:
    profile = _campaign_profile(profile)

    base = """
Tu és o Lumin, um assistente virtual de voz de inteligência artificial.

REGRAS GERAIS
- No início da chamada, diz o teu nome e identifica-te brevemente como assistente virtual. Se a pessoa perguntar se és um sistema de inteligência artificial, responde com clareza. Nunca finjas ser humano.
- Fala em português de Portugal, de forma natural, clara e profissional.
- Responde ao que a pessoa acabou de dizer e mantém o contexto.
- Guarda mentalmente os dados já respondidos. Não perguntes outra vez algo que já foi fornecido ou esteja explícito no contexto; aproveita respostas que cubram várias perguntas.
- Faz apenas a pergunta seguinte necessária, uma de cada vez. Se a resposta for ambígua, esclarece uma única vez com palavras diferentes; se continuar incerta, regista-a como desconhecida e avança, salvo se for indispensável para continuar.
- Evita ciclos: não repitas a abertura, explicação, pergunta, resumo ou confirmação. Responde ao que a pessoa disse, sem acrescentar frases de confirmação automáticas.
- Usa respostas muito curtas, normalmente até 15 palavras antes da pergunta, e faz uma pergunta de cada vez.
- Começa pela resposta útil, sem introduções desnecessárias.
- Faz pausas naturais entre ideias. Prefere frases curtas com pontos finais claros, em vez de uma frase longa.
- Não enchas silêncio com texto. Uma resposta direta é melhor do que um parágrafo.
- Se a pessoa disser apenas "sim", "não", "espere", "certo" ou equivalente, reage imediatamente ao significado dessa palavra.
- Se tiveres muita informação, divide-a por várias intervenções em vez de despejar tudo numa resposta.
- Não confirmes automaticamente cada resposta com "certo", "entendo" ou "percebo". Responde ao conteúdo e segue apenas quando houver uma pergunta necessária.
- Fala como numa conversa real: não recites o guião, não repitas a pergunta do cliente e evita frases demasiado perfeitas ou formais.
- Deixa a pessoa terminar a ideia antes de responder.
- Ouve mais do que falas. Se fores interrompido de forma clara, pára e ouve.
- Evita listas, markdown, símbolos e respostas longas.
- Como a resposta vai ser ouvida e não lida, escreve siglas, unidades, números, preços e percentagens de forma fácil de pronunciar quando isso evitar uma leitura robótica. Por exemplo, prefere "quilowatt-hora" a "kWh" numa frase falada.
- Não leias URLs, códigos ou sequências longas de números de uma vez. Se forem realmente necessários, dita-os em blocos curtos e confirma.
- Evita construções brasileiras e fala com ritmo calmo e dicção clara.
- Fala sempre em português de Portugal. Não mistures inglês na frase quando existir uma expressão portuguesa natural.
- Em voz, prefere "contactos" a "leads", "acompanhamento" a "follow-up", "site" a "website", "aplicação" a "app", "fluxo de trabalho" a "workflow" e "assistente virtual" a "chatbot".
- Só mantém em inglês nomes próprios de marcas ou produtos que tenham de ser ditos assim.
- Em chamada telefónica, articula bem cada palavra e evita emendar o fim de uma palavra no início da seguinte.
- Quando disseres marcas, siglas, preços ou condições, abranda ligeiramente e faz uma pausa curta antes e depois.
- Não inventes preços, resultados, condições, clientes ou funcionalidades.
- Se não souberes, diz de forma natural que não queres inventar.
- Nunca peças passwords, códigos bancários, PINs, dados completos de cartões ou credenciais.
- Não pressiones a pessoa. Mantém a identificação feita no início sem a repetires, salvo se a pessoa perguntar, e respeita imediatamente um pedido para terminar a chamada.
""".strip()

    if not profile:
        return base + """

CONTEXTO
Representas a LUMIN AI, uma plataforma portuguesa de inteligência artificial e automação.
Podes explicar soluções de inteligência artificial, atendimento, automação, sites, aplicações, vendas,
qualificação de contactos e ferramentas à medida.
""".rstrip()

    sections = [
        ("NOME DO AGENTE", _clean(profile.get("name"), 80)),
        ("EMPRESA / MARCA", _clean(profile.get("company"), 120)),
        ("DESCRIÇÃO / PERSONALIDADE", _clean(profile.get("description"), 900)),
        ("OBJETIVO DA CHAMADA", _clean(profile.get("objective"), 500)),
        ("PRODUTO / SERVIÇO", _clean(profile.get("product"), 1200)),
        ("OFERTA / CONDIÇÕES AUTORIZADAS", _clean(profile.get("offer"), 1200)),
        ("ABERTURA PRETENDIDA", _clean(profile.get("opening"), 800)),
        ("OBJEÇÕES E RESPOSTAS AUTORIZADAS", _clean(profile.get("objections"), 1800)),
        ("NOTAS DA EMPRESA", _clean(profile.get("notes"), 2200)),
        ("TOM", _tone_without_voice_tag(profile)),
    ]
    context = "\n".join(f"{title}:\n{text}" for title, text in sections if text)

    sales_mode = ""
    profile_id = _clean(profile.get("id"), 80).lower()
    company_name = _clean(profile.get("company"), 120).lower()
    if profile_id == "lumin-comercial" or "lumin ai" in company_name:
        sales_mode = """

MODO COMERCIAL LUMIN AI
O teu objetivo não é despejar funcionalidades; é descobrir uma necessidade real e ligar essa necessidade a uma solução LUMIN AI.

Segue esta lógica de venda consultiva:
1. GANHA PERMISSÃO: sê breve e cria curiosidade. Não faças um monólogo inicial.
2. DESCOBRE: faz uma pergunta de cada vez sobre como a empresa recebe clientes, responde a contactos, perde tempo, faz acompanhamento ou executa tarefas repetitivas.
3. APROFUNDA: quando surgir um problema, pergunta pelo impacto prático. Exemplo: demora a responder, contactos sem seguimento, demasiado trabalho manual, dificuldade em criar conteúdo ou atender fora de horas.
4. LIGA O PROBLEMA À SOLUÇÃO: apresenta apenas a funcionalidade LUMIN que resolve o problema identificado. Fala primeiro do resultado para o cliente e só depois da tecnologia.
5. CRIA VALOR SEM INVENTAR: usa benefícios concretos como responder mais depressa, automatizar tarefas, centralizar trabalho ou qualificar contactos, mas nunca prometas valores de poupança, faturação ou resultados que não estejam no contexto autorizado.
6. TESTA INTERESSE: usa perguntas curtas como "Se conseguisse automatizar essa parte, faria sentido ver como funciona?" ou "Isso resolveria uma dor que tem hoje?"
7. TRATA OBJEÇÕES: primeiro reconhece, depois faz uma pergunta para perceber a razão real, responde apenas ao ponto levantado e volta a testar interesse. Não discutas com o cliente.
8. FECHA O PRÓXIMO PASSO: quando houver interesse, confirma de forma direta que a pessoa quer uma demonstração/proposta ou contacto humano. Não digas que transferiste ou agendaste se essa integração não existir.
9. Mantém o controlo da conversa com perguntas, mas sem pressão enganosa, urgência falsa ou insistência após uma recusa clara.
"""

    result = base + sales_mode + """

CONTEXTO DESTA EMPRESA
Usa apenas a informação abaixo como contexto comercial autorizado.
Se algum dado não estiver aqui ou não tiveres a certeza, não inventes.

""" + context
    if follow_up_enabled:
        result += f"""

MARCAÇÃO NA AGENDA DO SD DIALER
Hora local atual em Lisboa: {current_local_time}.
Se a pessoa aceitar claramente o contacto de um consultor, pergunta apenas pelo melhor dia e hora.
Quando a pessoa indicar uma data e hora específicas, chama schedule_follow_up uma única vez com scheduled_at em ISO 8601 e fuso Europe/Lisbon.
Não perguntes se pode guardar dados ou voltar a ligar; a autorização já está registada.
Só afirmes que a chamada ficou marcada se a ferramenta confirmar sucesso. Se faltar apenas a hora, pergunta só a hora.
"""
    return result


def _add_customer_name_to_opening(opening: str, customer_name: str) -> str:
    if not customer_name or customer_name.casefold() in opening.casefold():
        return opening

    greeting = re.match(
        r"^\s*(Olá|Bom dia|Boa tarde|Boa noite)(?:[!,.]?\s*)",
        opening,
        flags=re.IGNORECASE,
    )
    if not greeting:
        return f"Olá, {customer_name}. {opening}".strip()

    rest = opening[greeting.end():].lstrip()
    if rest:
        rest = rest[0].upper() + rest[1:]
    return f"{greeting.group(1)}, {customer_name}. {rest}".strip()


def build_greeting(profile: dict[str, Any] | None, customer_name: str = "") -> str:
    profile = _campaign_profile(profile)
    opening = _clean(profile.get("opening"), 800)
    agent_name = _clean(profile.get("name"), 80) or "Lumin"
    company = _clean(profile.get("company"), 120)
    customer_name = _clean(customer_name, 100)
    has_customer_name = bool(customer_name and customer_name.casefold() not in {"cliente", "contacto manual"})
    name_part = f", {customer_name}" if has_customer_name else ""
    name_placeholder = re.compile(r"\[(?:nome|name)\]", flags=re.IGNORECASE)
    company_key = re.sub(r"[^a-z0-9]", "", company.casefold())

    if "compara" in company_key and opening:
        has_name_placeholder = bool(name_placeholder.search(opening))
        replacement_name = customer_name if has_customer_name else ""
        opening = name_placeholder.sub(lambda _: replacement_name, opening)
        opening = re.sub(r",\s*\.", ".", opening)
        opening = re.sub(r"\s{2,}", " ", opening).strip()
        if has_customer_name and not has_name_placeholder:
            opening = _add_customer_name_to_opening(opening, customer_name)
        # The campaign opening already introduces Lumin; do not prepend a
        # second introduction or replace the campaign wording.
        if "assistente virtual" in opening.casefold():
            return opening

    if opening:
        opening = name_placeholder.sub(
            lambda _: customer_name if has_customer_name else "",
            opening,
        )
        opening = re.sub(r"\s{2,}", " ", opening).strip()
        greeting_name = name_part
        if has_customer_name and customer_name.casefold() in opening.casefold():
            greeting_name = ""
        who = f" da {company}" if company else ""
        return f"Olá{greeting_name}. Sou o {agent_name}, assistente virtual de IA{who}. {opening}"

    who = f" da {company}" if company else ""
    return (
        f"Olá{name_part}. Sou o {agent_name}, assistente virtual de IA{who}. "
        "Posso falar consigo por um momento?"
    )


class LuminAgent(Agent):
    def __init__(
        self,
        profile: dict[str, Any] | None = None,
        customer_name: str = "",
        capture_phone_lead: bool = False,
        follow_up_logger: SupabaseLogger | None = None,
        follow_up_call_id: str = "",
        follow_up_lead_id: str = "",
        follow_up_company_id: str = "",
        follow_up_requested_by: str = "",
    ) -> None:
        self._profile = profile or {}
        self._customer_name = customer_name
        self._follow_up_logger = follow_up_logger
        self._follow_up_call_id = follow_up_call_id
        self._follow_up_lead_id = follow_up_lead_id
        self._follow_up_company_id = follow_up_company_id
        self._follow_up_requested_by = follow_up_requested_by
        self._follow_up_lock = asyncio.Lock()
        self._follow_up_confirmation = ""

        follow_up_enabled = bool(
            follow_up_logger
            and follow_up_call_id
            and follow_up_lead_id
            and follow_up_company_id
            and follow_up_requested_by
        )
        current_local_time = datetime.now(ZoneInfo("Europe/Lisbon")).isoformat(timespec="minutes")
        instructions = build_instructions(
            self._profile,
            follow_up_enabled=follow_up_enabled,
            current_local_time=current_local_time,
        )
        if capture_phone_lead:
            instructions += lead_capture_instructions()
        super().__init__(instructions=instructions)
        if not follow_up_enabled:
            self.update_tools([])

    @function_tool()
    async def schedule_follow_up(self, context: RunContext, scheduled_at: str) -> str:
        """Marca uma chamada de seguimento na agenda do SD Dialer.

        Args:
            scheduled_at: Data e hora acordadas com a pessoa, em ISO 8601 com fuso Europe/Lisbon.
        """
        if not self._follow_up_logger:
            return "Não foi possível confirmar a marcação na agenda. Não digas que ficou marcada."

        raw_datetime = _clean(scheduled_at, 80)
        try:
            parsed = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Lisbon"))
            local_datetime = parsed.astimezone(ZoneInfo("Europe/Lisbon"))
            utc_datetime = local_datetime.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return "Data ou hora inválida. Pede apenas que a pessoa esclareça a data ou hora."

        now = datetime.now(timezone.utc)
        if utc_datetime <= now + timedelta(minutes=2):
            return "A data/hora já passou ou é demasiado próxima para garantir a chamada. Não digas que ficou marcada; pede outro horário."
        if utc_datetime > now + timedelta(days=180):
            return "A data indicada está demasiado distante para confirmar. Pede um dia mais próximo."

        async with self._follow_up_lock:
            if self._follow_up_confirmation:
                return self._follow_up_confirmation
            created = await self._follow_up_logger.create_follow_up(
                call_id=self._follow_up_call_id,
                lead_id=self._follow_up_lead_id,
                partner_id=self._follow_up_requested_by,
                company_id=self._follow_up_company_id,
                scheduled_at=local_datetime.isoformat(timespec="seconds"),
                notes="Contacto solicitado durante chamada do assistente virtual.",
            )
            if not created:
                return "Não consegui confirmar a marcação na agenda. Não digas que ficou marcada."

            weekdays = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
            months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
            self._follow_up_confirmation = (
                f"Marcação confirmada para {weekdays[local_datetime.weekday()]}, "
                f"{local_datetime.day} de {months[local_datetime.month - 1]}, "
                f"às {local_datetime:%H:%M}, hora de Lisboa."
            )
            return self._follow_up_confirmation

    async def on_enter(self):
        await self.session.say(
            build_greeting(self._profile, self._customer_name),
            allow_interruptions=True,
        )

async def entrypoint(ctx: JobContext):
    metadata: dict[str, Any] = {}
    try:
        metadata = json.loads(ctx.job.metadata or "{}")
    except Exception:
        pass

    profile = metadata.get("agentProfile") or {}
    customer_name = _clean(metadata.get("name"), 100)
    source = _clean(metadata.get("source"), 80).lower().replace("_", "-")
    is_sd_dialer_call = source in {"sd-dialer", "sd-dialer-manual"}
    call_id = _clean(metadata.get("callId") or metadata.get("call_id"), 80)
    company_id = _clean(metadata.get("companyId") or metadata.get("company_id"), 80)
    requested_by = _clean(metadata.get("requestedBy") or metadata.get("requested_by"), 80)
    lead_capture = LeadCapture.from_job(metadata)
    voice_call_logger: SupabaseLogger | None = None
    call_started_at = datetime.now(timezone.utc)

    if is_sd_dialer_call and call_id and company_id:
        try:
            candidate_logger = SupabaseLogger()
            created = await candidate_logger.create_voice_call(
                call_id=call_id,
                company_id=company_id,
                phone_number=_clean(metadata.get("phone"), 80),
                client_name=customer_name,
                created_by=requested_by or None,
                lead_id=_clean(metadata.get("leadId") or metadata.get("lead_id"), 80) or None,
                livekit_room=ctx.room.name,
                source=source,
            )
            if created:
                voice_call_logger = candidate_logger
                await voice_call_logger.cleanup_expired_transcripts(company_id)
            else:
                await candidate_logger.close()
        except Exception as exc:
            logger.warning(
                "SD Dialer call history unavailable",
                extra={"error_type": type(exc).__name__},
            )

    logger.info(
        "voice session started",
        extra={
            "room": ctx.room.name,
            "source": metadata.get("source", "unknown"),
            "mode": metadata.get("mode", "unknown"),
            "agent_profile_id": profile.get("id", "") if isinstance(profile, dict) else "",
        },
    )

    base_tts = ctx.proc.userdata.get("lumin_tts") or get_piper()
    voice_id = _voice_id_from_profile(profile) if isinstance(profile, dict) else "natural"
    voice_engine = _clean(profile.get("voiceEngine"), 40).lower() if isinstance(profile, dict) else ""
    voice_source = _clean(profile.get("voiceSource"), 40).lower() if isinstance(profile, dict) else ""
    voice_sample_url = _clean(profile.get("voiceSampleUrl"), 4000) if isinstance(profile, dict) else ""
    chatterbox_url = os.getenv("CHATTERBOX_TTS_URL", "").strip()
    openvoice_url = os.getenv("OPENVOICE_TTS_URL", "").strip()
    kokoro_url = os.getenv("KOKORO_TTS_URL", "").strip()
    piper_catalog_url = os.getenv("PIPER_CATALOG_TTS_URL", "").strip()
    wants_custom = voice_id == "custom" or voice_source == "custom"
    wants_chatterbox = voice_engine == "chatterbox" or voice_id == "neural"
    wants_neural_ptpt = voice_engine in {"xai", "neural-ptpt"}
    wants_kokoro = voice_engine == "kokoro" or voice_id == "kokoro"
    wants_open_source = voice_engine in {"open-source", "opensource", "oss"}
    wants_catalog = voice_engine == "piper-catalog" or voice_id.startswith("piper:")
    catalog_key = voice_id.split(":", 1)[1] if voice_id.startswith("piper:") else voice_id

    livekit_tts_provider = os.getenv("LUMIN_TTS_PROVIDER", "opensource").strip().lower()
    use_livekit_xai = livekit_tts_provider in {"xai", "livekit-xai"}

    if wants_neural_ptpt and use_livekit_xai:
        # Keep the metered xAI route available as an explicit operator override.
        voices = {"natural": "sal", "clear": "rex", "commercial": "leo"}
        tts_engine = inference.TTS(
            model="xai/tts-1",
            voice=voices.get(voice_id, "rex"),
            language="pt-PT",
        )
        selected_engine = "xai-neural-ptpt"
    elif wants_neural_ptpt or wants_kokoro or wants_open_source:
        # Existing xAI voice profiles now use an open-source PT-PT model by default.
        # This avoids the xAI/LiveKit 429 path and keeps local Piper as an automatic fallback.
        tts_engine, selected_engine = open_source_ptpt_tts(
            base_tts,
            voice_id,
            kokoro_url,
        )
        if wants_neural_ptpt:
            logger.info(
                "xAI voice profile routed to open-source PT-PT TTS",
                extra={"voice_profile": voice_id, "provider": selected_engine},
            )
    elif wants_catalog and piper_catalog_url and catalog_key:
        tts_engine = PiperCatalogRemoteTTS(
            piper_catalog_url,
            voice_key=catalog_key,
        )
        selected_engine = f"piper-catalog:{catalog_key}"
    elif wants_custom and openvoice_url and voice_sample_url:
        tts_engine = OpenVoiceRemoteTTS(
            openvoice_url,
            audio_prompt_url=voice_sample_url,
            fallback_url=chatterbox_url,
        )
        selected_engine = "openvoice-custom"
    elif wants_custom and chatterbox_url and voice_sample_url:
        logger.warning("OpenVoice unavailable; using Chatterbox custom voice fallback")
        tts_engine = ChatterboxRemoteTTS(
            chatterbox_url,
            audio_prompt_url=voice_sample_url,
            language_id="pt",
        )
        selected_engine = "chatterbox-custom"
    elif wants_chatterbox and chatterbox_url:
        tts_engine = ChatterboxRemoteTTS(
            chatterbox_url,
            audio_prompt_url="",
            language_id="pt",
        )
        selected_engine = "chatterbox-system"
    else:
        if wants_catalog and not piper_catalog_url:
            logger.warning("Piper catalog voice requested but PIPER_CATALOG_TTS_URL is missing; falling back to default Piper")
        if wants_custom and not voice_sample_url:
            logger.warning("custom voice requested without a signed sample URL; falling back to Piper")
        if wants_chatterbox and not chatterbox_url:
            logger.warning("Chatterbox requested but CHATTERBOX_TTS_URL is missing; falling back to Piper")
        tts_engine = voice_for_profile(base_tts, voice_id)
        selected_engine = "piper"

    logger.info(
        "voice profile selected",
        extra={
            "voice_profile": voice_id,
            "voice_engine": selected_engine,
            "voice_source": voice_source or "system",
        },
    )

    # Natural turn-taking: detect the end of a Portuguese sentence instead of
    # treating every short pause as a finished turn. Real barge-ins such as
    # "não", "espere" or "sim" must still stop the agent quickly.
    fast_tts = selected_engine == "piper" or selected_engine.startswith("piper-catalog:")
    turn_handling = TurnHandlingOptions(
        turn_detection=inference.TurnDetector(),
        endpointing={
            "mode": "dynamic",
            "min_delay": 0.30,
            "max_delay": 0.85,
            "alpha": 0.72,
        },
        interruption={
            "enabled": True,
            "mode": "adaptive",
            "min_duration": 0.30,
            "min_words": 1,
            "false_interruption_timeout": 1.20,
            "resume_false_interruption": True,
        },
        preemptive_generation={
            "enabled": True,
            # Piper is cheap enough to synthesize speculatively. For neural
            # cloning engines keep only LLM pre-generation to avoid wasting
            # expensive TTS when the transcript changes.
            "preemptive_tts": fast_tts,
            "max_speech_duration": 7.0,
            "max_retries": 2,
        },
    )

    session = AgentSession(
        vad=inference.VAD(),
        stt=inference.STT("deepgram/nova-3", language="pt"),
        llm=inference.LLM("openai/gpt-5.6-luna"),
        tts=tts_engine,
        turn_handling=turn_handling,
        # A tiny breath between adjacent agent utterances sounds much more
        # natural than stitching them together with no gap.
        min_consecutive_speech_delay=0.18,
    )

    @session.on("metrics_collected")
    def _log_voice_metrics(ev):
        m = ev.metrics
        values = {
            "metric_type": m.__class__.__name__,
            "speech_id": getattr(m, "speech_id", None),
            "label": getattr(m, "label", None),
            "eou_delay": getattr(m, "end_of_utterance_delay", None),
            "transcription_delay": getattr(m, "transcription_delay", None),
            "ttft": getattr(m, "ttft", None),
            "ttfb": getattr(m, "ttfb", None),
            "duration": getattr(m, "duration", None),
            "audio_duration": getattr(m, "audio_duration", None),
            "cancelled": getattr(m, "cancelled", None),
        }
        logger.info(
            "voice metric",
            extra={k: v for k, v in values.items() if v is not None},
        )

    transcript_tasks: set[asyncio.Task[Any]] = set()
    transcript_lock = asyncio.Lock()

    async def _persist_sd_dialer_transcript(role: Any, text_value: str) -> None:
        if voice_call_logger is None or not call_id or not company_id:
            return

        async with transcript_lock:
            role_name = _clean(role, 40).lower()
            speaker = (
                "client" if role_name in {"user", "human"}
                else "agent" if role_name in {"assistant", "agent"}
                else ""
            )
            if speaker:
                await voice_call_logger.log_transcript(
                    call_id,
                    speaker,
                    text_value,
                    company_id=company_id,
                )

    @session.on("conversation_item_added")
    def _log_conversation(ev):
        item = ev.item
        text_value = _clean(getattr(item, "text_content", None) or "", 4000)
        role = getattr(item, "role", "unknown")
        if text_value:
            if lead_capture is not None:
                lead_capture.observe(role, text_value)
            if is_sd_dialer_call and voice_call_logger is not None:
                task = asyncio.create_task(_persist_sd_dialer_transcript(role, text_value))
                transcript_tasks.add(task)
                task.add_done_callback(transcript_tasks.discard)
            # Keep operational diagnostics without retaining customer transcripts in logs.
            logger.info(
                "conversation_item",
                extra={"role": role, "text_length": len(text_value)},
            )

    async def _finalize_call() -> None:
        if transcript_tasks:
            await asyncio.gather(*tuple(transcript_tasks), return_exceptions=True)
        if lead_capture is not None:
            await lead_capture.register()
        if voice_call_logger is not None:
            ended_at = datetime.now(timezone.utc)
            duration_seconds = max(0, int((ended_at - call_started_at).total_seconds()))
            await voice_call_logger.update_call(
                call_id,
                status="answered",
                ended_at=ended_at.isoformat(),
                duration_seconds=duration_seconds,
            )
            await voice_call_logger.close()

    ctx.add_shutdown_callback(_finalize_call)

    await session.start(
        agent=LuminAgent(
            profile if isinstance(profile, dict) else {},
            customer_name=customer_name,
            capture_phone_lead=lead_capture is not None,
            follow_up_logger=voice_call_logger if is_sd_dialer_call else None,
            follow_up_call_id=call_id if is_sd_dialer_call else "",
            follow_up_lead_id=_clean(metadata.get("leadId") or metadata.get("lead_id"), 80),
            follow_up_company_id=company_id if is_sd_dialer_call else "",
            follow_up_requested_by=requested_by if is_sd_dialer_call else "",
        ),
        room=ctx.room,
    )
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="lumin-web",
            prewarm_fnc=prewarm,
            num_idle_processes=1,
            initialize_process_timeout=30.0,
            job_memory_warn_mb=900,
        )
    )
