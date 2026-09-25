"""
LUMIN Web Voice Agent
WebRTC voice agent for luminai.pt, no telephone number required.

Voice output uses Piper locally with the Portuguese (Portugal) "tugão"
voice. The browser audio path remains LiveKit/WebRTC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, JobProcess, WorkerOptions, cli, inference

from piper_tts import PiperTTS
from kokoro_ptpt_tts import KokoroPtPTTTS

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("lumin-web-agent")
logger.setLevel(logging.INFO)

_PIPER: PiperTTS | None = None
_KOKORO: KokoroPtPTTTS | None = None


def get_piper() -> PiperTTS:
    global _PIPER
    if _PIPER is None:
        model_path = os.getenv(
            "PIPER_MODEL",
            "/app/voices/pt_PT-tugão-medium.onnx",
        )
        logger.info("loading local Piper PT-PT voice", extra={"model": model_path})
        _PIPER = PiperTTS(model_path, length_scale=0.96)
        logger.info(
            "local Piper voice ready",
            extra={
                "provider": _PIPER.provider,
                "model": _PIPER.model,
                "sample_rate": _PIPER.sample_rate,
            },
        )
    return _PIPER


def get_kokoro() -> KokoroPtPTTTS:
    global _KOKORO
    if _KOKORO is None:
        logger.info("loading Kokoro European-Portuguese voice")
        _KOKORO = KokoroPtPTTTS()
        logger.info(
            "Kokoro PT-PT voice ready",
            extra={
                "provider": _KOKORO.provider,
                "model": _KOKORO.model,
                "sample_rate": _KOKORO.sample_rate,
            },
        )
    return _KOKORO


def prewarm(proc: JobProcess) -> None:
    """Load the heavy PT-PT voice before a visitor starts a call."""
    logger.info("prewarming Kokoro PT-PT voice")
    try:
        proc.userdata["lumin_tts"] = get_kokoro()
        logger.info("Kokoro PT-PT prewarm complete")
    except Exception:
        logger.exception("Kokoro prewarm failed; prewarming Piper fallback")
        proc.userdata["lumin_tts"] = get_piper()


def build_instructions() -> str:
    return """
Tu és o Lumin, o assistente virtual de voz da LUMIN AI.

IDENTIDADE
- Apresenta-te claramente como assistente virtual de inteligência artificial da LUMIN AI.
- Nunca afirmes ser humano.
- Fala em português de Portugal (pt-PT), a menos que o utilizador fale noutra língua.
- O teu tom é natural, inteligente, descontraído, profissional e direto.
- Tens de responder ao que a pessoa acabou de dizer; não mudes de assunto nem sigas um guião cego.
- Mantém o contexto da conversa e usa informação já dita pelo utilizador.
- Se a pessoa quiser apenas conversar ou testar a IA, conversa normalmente sem tentar vender.

FORMA DE FALAR
- Isto é uma conversa de voz, não um texto escrito.
- Usa frases curtas e naturais.
- Faz uma pergunta de cada vez.
- Ouve mais do que falas.
- Se fores interrompido, pára e responde ao que a pessoa acabou de dizer.
- Evita listas longas e linguagem técnica desnecessária.
- Escreve as respostas de forma fácil de pronunciar em voz alta.
- Evita símbolos, markdown e abreviaturas estranhas quando estiveres a falar.

OBJETIVO
- Demonstrar uma conversa de voz natural em tempo real.
- Explicar o que é a LUMIN AI quando perguntarem.
- Perceber o que a pessoa pretende.
- Se for uma empresa, perceber onde perde tempo, como recebe contactos e que tarefas repete.
- Sugerir no máximo uma ou duas aplicações concretas de IA que façam sentido.

SOBRE A LUMIN AI
A LUMIN AI é uma plataforma portuguesa de inteligência artificial e automação para pessoas e empresas.
Pode apoiar criação de websites e aplicações, agentes de IA, atendimento, apoio comercial,
automação, marketing, conteúdos, gestão e qualificação de leads e ferramentas à medida.
Não inventes preços, clientes, resultados ou funcionalidades que não conheças.

TRANSPARÊNCIA
Se não souberes uma resposta, diz de forma natural que não queres inventar e que a equipa pode confirmar.
Nunca peças passwords, códigos bancários, dados de cartões ou outras credenciais.

INÍCIO
Cumprimenta, apresenta-te e pergunta em que podes ajudar.
""".strip()


class LuminAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=build_instructions())

    async def on_enter(self):
        # Fast deterministic greeting: skip the LLM for the first sentence so
        # the visitor hears Lumin as soon as the audio room is ready.
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

    # The voice is preloaded in the idle job process so answering a call does
    # not spend 10–20 seconds loading a 300+ MB model.
    tts_engine = ctx.proc.userdata.get("lumin_tts")
    if tts_engine is None:
        try:
            tts_engine = await asyncio.to_thread(get_kokoro)
        except Exception:
            logger.exception("Kokoro PT-PT failed; falling back to Piper PT-PT")
            tts_engine = await asyncio.to_thread(get_piper)

    session = AgentSession(
        vad=inference.VAD(),
        stt=inference.STT("deepgram/nova-3", language="pt"),
        llm=inference.LLM("openai/gpt-5.6-luna"),
        tts=tts_engine,
    )

    @session.on("conversation_item_added")
    def _log_conversation(ev):
        item = ev.item
        text = getattr(item, "text_content", None) or ""
        if text:
            logger.info(
                "conversation",
                extra={"role": getattr(item, "role", "unknown"), "text": text[:1000]},
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
            initialize_process_timeout=60.0,
            job_memory_warn_mb=1900,
        )
    )
