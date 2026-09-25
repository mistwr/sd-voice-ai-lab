"""
LUMIN Web Voice Agent
WebRTC voice agent for luminai.pt, no telephone number required.

Voice output uses Piper locally with the Portuguese (Portugal) "tugão"
voice. The browser audio path remains LiveKit/WebRTC.
"""
from __future__ import annotations

import json
import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, inference

from piper_tts import PiperTTS

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("lumin-web-agent")
logger.setLevel(logging.INFO)

_PIPER: PiperTTS | None = None


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


def build_instructions() -> str:
    return """
Tu és o Lumin, o assistente virtual de voz da LUMIN AI.

IDENTIDADE
- Apresenta-te claramente como assistente virtual de inteligência artificial da LUMIN AI.
- Nunca afirmes ser humano.
- Fala em português de Portugal (pt-PT), a menos que o utilizador fale noutra língua.
- O teu tom é natural, inteligente, descontraído, profissional e direto.

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
        self.session.generate_reply(
            instructions=(
                "Cumprimenta em português de Portugal. Diz que és o Lumin, "
                "assistente virtual da LUMIN AI, e que esta conversa é por voz "
                "em tempo real. Termina com uma única pergunta curta."
            )
        )


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    metadata = {}
    try:
        metadata = json.loads(ctx.job.metadata or "{}")
    except Exception:
        pass

    logger.info(
        "web voice session started",
        extra={"room": ctx.room.name, "source": metadata.get("source", "unknown")},
    )

    session = AgentSession(
        vad=inference.VAD(),
        stt=inference.STT("deepgram/nova-3", language="pt"),
        llm=inference.LLM("openai/gpt-4.1-mini"),
        tts=get_piper(),
    )

    await session.start(agent=LuminAgent(), room=ctx.room)
    await session.wait_for_end()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="lumin-web"))
