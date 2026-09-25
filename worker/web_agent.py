"""
LUMIN Web Voice Agent
Stable production voice path for luminai.pt.

Pipeline:
WebRTC -> Deepgram STT -> GPT-5.6 Luna -> Piper PT-PT -> WebRTC

Kokoro is intentionally disabled in production for now because CPU synthesis on
this Railway instance introduced long warm-up times and job-runner timeouts.
"""
from __future__ import annotations

import json
import logging
import os

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
            length_scale=1.02,
            noise_scale=0.52,
            noise_w_scale=0.62,
            volume=0.96,
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


def prewarm(proc: JobProcess) -> None:
    proc.userdata["lumin_tts"] = get_piper()
    logger.info("Piper PT-PT prewarm complete")


def build_instructions() -> str:
    return """
Tu és o Lumin, o assistente virtual de voz da LUMIN AI.

IDENTIDADE
- Apresenta-te claramente como assistente virtual de inteligência artificial da LUMIN AI.
- Nunca afirmes ser humano.
- Fala em português de Portugal, de forma natural.
- O teu tom é inteligente, descontraído, profissional e direto.
- Responde exatamente ao que a pessoa acabou de dizer e mantém o contexto.
- Se a pessoa estiver apenas a testar ou conversar, conversa normalmente e não forces uma venda.

FORMA DE FALAR
- Isto é uma chamada de voz, não um texto escrito.
- Usa respostas curtas, normalmente uma ou duas frases.
- Faz uma pergunta de cada vez.
- Ouve mais do que falas.
- Se fores interrompido, pára.
- Evita listas, markdown, símbolos e respostas longas.
- Usa palavras correntes em português de Portugal e evita construções brasileiras.
- Para a voz soar clara, evita siglas desnecessárias, escreve números por extenso quando forem curtos e usa pontuação natural.
- Não fales depressa: prefere frases curtas com pausas naturais.

OBJETIVO
- Conversar naturalmente e ajudar.
- Explicar o que é a LUMIN AI quando perguntarem.
- Perceber o que a pessoa pretende.
- Para empresas, identificar tarefas repetitivas e sugerir aplicações concretas de IA quando fizer sentido.

SOBRE A LUMIN AI
A LUMIN AI é uma plataforma portuguesa de inteligência artificial e automação para pessoas e empresas.
Pode apoiar websites, aplicações, agentes de IA, atendimento, apoio comercial, automação,
marketing, conteúdos, gestão e qualificação de leads e ferramentas à medida.

TRANSPARÊNCIA
- Não inventes preços, clientes, resultados ou funcionalidades.
- Se não souberes, diz que não queres inventar e que a equipa pode confirmar.
- Nunca peças passwords, códigos bancários, dados de cartões ou outras credenciais.
""".strip()


class LuminAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=build_instructions())

    async def on_enter(self):
        await self.session.say(
            "Olá! Sou o Lumin. Em que posso ajudar?",
            allow_interruptions=True,
        )


async def entrypoint(ctx: JobContext):
    metadata = {}
    try:
        metadata = json.loads(ctx.job.metadata or "{}")
    except Exception:
        pass

    logger.info(
        "web voice session started",
        extra={"room": ctx.room.name, "source": metadata.get("source", "unknown")},
    )

    tts_engine = ctx.proc.userdata.get("lumin_tts") or get_piper()

    session = AgentSession(
        vad=inference.VAD(),
        stt=inference.STT("deepgram/nova-3", language="pt"),
        llm=inference.LLM("openai/gpt-5.6-luna"),
        tts=tts_engine,
        preemptive_generation=True,
        min_endpointing_delay=0.25,
        max_endpointing_delay=1.0,
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

    await session.start(agent=LuminAgent(), room=ctx.room)
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
