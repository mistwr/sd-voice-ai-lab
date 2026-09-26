"""
LUMIN Voice Agent
Stable production voice path for luminai.pt and outbound SIP calls.

Pipeline:
WebRTC/SIP -> Deepgram STT -> GPT-5.6 Luna -> Piper PT-PT -> WebRTC/SIP
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, JobProcess, WorkerOptions, cli, inference

from piper_tts import PiperTTS

load_dotenv(".env.local")
load_dotenv()

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

def prewarm(proc: JobProcess) -> None:
    proc.userdata["lumin_tts"] = get_piper()
    logger.info("Piper PT-PT prewarm complete")


def _clean(value: Any, limit: int = 1800) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _voice_id_from_profile(profile: dict[str, Any]) -> str:
    direct = _clean(profile.get("voice"), 40)
    if direct:
        return direct
    tone = _clean(profile.get("tone"), 300)
    match = re.match(r"^\[VOICE:(natural|clear|commercial)\]\s*", tone, flags=re.IGNORECASE)
    return match.group(1).lower() if match else "natural"


def _tone_without_voice_tag(profile: dict[str, Any]) -> str:
    tone = _clean(profile.get("tone"), 300)
    return re.sub(r"^\[VOICE:(?:natural|clear|commercial)\]\s*", "", tone, flags=re.IGNORECASE)


def build_instructions(profile: dict[str, Any] | None = None) -> str:
    profile = profile or {}

    base = """
Tu és o Lumin, um assistente virtual de voz de inteligência artificial.

REGRAS GERAIS
- Identifica-te claramente como assistente virtual de inteligência artificial. Nunca finjas ser humano.
- Fala em português de Portugal, de forma natural, clara e profissional.
- Responde ao que a pessoa acabou de dizer e mantém o contexto.
- Usa respostas curtas, normalmente uma ou duas frases, e faz uma pergunta de cada vez.
- Em conversa normal, tenta não passar de cerca de vinte e cinco palavras antes de devolver a vez à pessoa.
- Faz pausas naturais entre ideias. Prefere frases curtas com pontos finais claros, em vez de uma frase longa.
- Se tiveres muita informação, divide-a por várias intervenções em vez de despejar tudo numa resposta.
- Depois de a pessoa responder, quando soar natural usa uma confirmação muito curta como frase independente, por exemplo "Certo.", "Entendo.", "Faz sentido." ou "Percebo.". Varia e não comeces todas as respostas da mesma maneira.
- Fala como numa conversa real: não recites o guião, não repitas a pergunta do cliente e evita frases demasiado perfeitas ou formais.
- Deixa a pessoa terminar a ideia antes de responder.
- Ouve mais do que falas. Se fores interrompido de forma clara, pára e ouve.
- Evita listas, markdown, símbolos e respostas longas.
- Evita construções brasileiras e fala com ritmo calmo e dicção clara.
- Fala sempre em português de Portugal. Não mistures inglês na frase quando existir uma expressão portuguesa natural.
- Em voz, prefere "contactos" a "leads", "acompanhamento" a "follow-up", "site" a "website", "aplicação" a "app", "fluxo de trabalho" a "workflow" e "assistente virtual" a "chatbot".
- Só mantém em inglês nomes próprios de marcas ou produtos que tenham de ser ditos assim.
- Em chamada telefónica, articula bem cada palavra e evita emendar o fim de uma palavra no início da seguinte.
- Quando disseres marcas, siglas, preços ou condições, abranda ligeiramente e faz uma pausa curta antes e depois.
- Não inventes preços, resultados, condições, clientes ou funcionalidades.
- Se não souberes, diz de forma natural que não queres inventar.
- Nunca peças passwords, códigos bancários, PINs, dados completos de cartões ou credenciais.
- Não pressiones a pessoa, não escondas que és IA e respeita imediatamente um pedido para terminar a chamada.
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

    return base + sales_mode + """

CONTEXTO DESTA EMPRESA
Usa apenas a informação abaixo como contexto comercial autorizado.
Se algum dado não estiver aqui ou não tiveres a certeza, não inventes.

""" + context


def build_greeting(profile: dict[str, Any] | None, customer_name: str = "") -> str:
    profile = profile or {}
    opening = _clean(profile.get("opening"), 800)
    agent_name = _clean(profile.get("name"), 80) or "Lumin"
    company = _clean(profile.get("company"), 120)

    if opening:
        # Keep disclosure even when the user supplied their own opening.
        return f"Olá. Sou o {agent_name}, assistente virtual de IA. {opening}"

    who = f" da {company}" if company else ""
    name_part = f", {customer_name}" if customer_name else ""
    return (
        f"Olá{name_part}. Sou o {agent_name}, assistente virtual de IA{who}. "
        "Posso falar consigo por um momento?"
    )


class LuminAgent(Agent):
    def __init__(self, profile: dict[str, Any] | None = None, customer_name: str = "") -> None:
        self._profile = profile or {}
        self._customer_name = customer_name
        super().__init__(instructions=build_instructions(self._profile))

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
    tts_engine = voice_for_profile(base_tts, voice_id)

    logger.info("voice profile selected", extra={"voice_profile": voice_id})

    session = AgentSession(
        vad=inference.VAD(),
        stt=inference.STT("deepgram/nova-3", language="pt"),
        llm=inference.LLM("openai/gpt-5.6-luna"),
        tts=tts_engine,
        preemptive_generation=True,
        min_endpointing_delay=0.34,
        max_endpointing_delay=0.90,
        # Telephone lines contain clicks, breaths and background speech.
        # Require a clearer interruption so Lumin does not stop mid-sentence.
        min_interruption_duration=0.65,
        min_interruption_words=2,
        resume_false_interruption=True,
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

    @session.on("conversation_item_added")
    def _log_conversation(ev):
        item = ev.item
        text_value = getattr(item, "text_content", None) or ""
        if text_value:
            logger.info(
                "conversation",
                extra={
                    "role": getattr(item, "role", "unknown"),
                    "text": text_value[:1000],
                },
            )

    await session.start(
        agent=LuminAgent(
            profile if isinstance(profile, dict) else {},
            customer_name=customer_name,
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
