"""
LUMIN Qwen Realtime worker.

Separate experimental speech-to-speech path. It never replaces the stable
Deepgram -> Luna -> Piper worker. A self-hosted vLLM-Omni endpoint is required.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("lumin-qwen-realtime")
logger.setLevel(logging.INFO)

QWEN_BASE_URL = os.getenv("QWEN_REALTIME_BASE_URL", "").rstrip("/")
QWEN_API_KEY = os.getenv("QWEN_REALTIME_API_KEY", "local")
QWEN_MODEL = os.getenv(
    "QWEN_REALTIME_MODEL",
    "Qwen/Qwen3-Omni-30B-A3B-Instruct",
)

VOICE_MAP = {
    "qwen-ethan": "Ethan",
    "qwen-chelsie": "Chelsie",
    "qwen-aiden": "Aiden",
}


def _clean(value: Any, limit: int = 1800) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _voice_from_profile(profile: dict[str, Any]) -> str:
    tone = _clean(profile.get("tone"), 300)
    match = re.match(
        r"^\[VOICE:(qwen-ethan|qwen-chelsie|qwen-aiden)\]\s*",
        tone,
        flags=re.IGNORECASE,
    )
    return VOICE_MAP.get(match.group(1).lower(), "Ethan") if match else "Ethan"


def _tone_without_voice_tag(profile: dict[str, Any]) -> str:
    tone = _clean(profile.get("tone"), 300)
    return re.sub(
        r"^\[VOICE:(?:qwen-ethan|qwen-chelsie|qwen-aiden)\]\s*",
        "",
        tone,
        flags=re.IGNORECASE,
    )


def build_instructions(profile: dict[str, Any] | None = None) -> str:
    profile = profile or {}

    base = """
Tu és o Lumin, um assistente virtual de voz de inteligência artificial.

REGRAS DE CONVERSA
- Identifica-te claramente como assistente virtual de inteligência artificial; nunca finjas ser humano.
- Fala em português de Portugal.
- Conversa como numa chamada real: respostas curtas, naturais e diretas.
- Ouve enquanto a pessoa fala e aceita interrupções sem insistir em terminar a tua frase.
- Não despejes informação. Normalmente responde em uma ou duas frases e faz uma pergunta de cada vez.
- Evita linguagem de guião, listas, markdown, símbolos e frases demasiado formais.
- Usa confirmações curtas quando fizer sentido, mas não repitas sempre "Percebo".
- Não inventes preços, condições, dados do contrato, resultados ou funcionalidades.
- Nunca peças passwords, PINs, dados completos de cartões ou credenciais.
- Se a pessoa pedir para terminar ou para não voltar a ser contactada, respeita imediatamente.
""".strip()

    if not profile:
        return base + """

CONTEXTO
Representas a LUMIN AI, uma plataforma portuguesa de inteligência artificial e automação.
""".rstrip()

    sections = [
        ("NOME DO AGENTE", _clean(profile.get("name"), 80)),
        ("EMPRESA / MARCA", _clean(profile.get("company"), 120)),
        ("OBJETIVO", _clean(profile.get("objective"), 500)),
        ("PRODUTO / SERVIÇO", _clean(profile.get("product"), 1200)),
        ("OFERTA / CONDIÇÕES", _clean(profile.get("offer"), 1200)),
        ("ABERTURA", _clean(profile.get("opening"), 800)),
        ("OBJEÇÕES", _clean(profile.get("objections"), 1800)),
        ("NOTAS / REGRAS", _clean(profile.get("notes"), 2200)),
        ("TOM", _tone_without_voice_tag(profile)),
    ]
    context = "\n".join(f"{title}:\n{text}" for title, text in sections if text)
    return base + """

CONTEXTO COMERCIAL AUTORIZADO
Usa a informação abaixo como contexto. Se um dado não estiver aqui, não inventes.

""" + context


class QwenRealtimeAgent(Agent):
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        super().__init__(instructions=build_instructions(profile))


async def entrypoint(ctx: JobContext):
    if not QWEN_BASE_URL:
        raise RuntimeError(
            "QWEN_REALTIME_BASE_URL is not configured; refusing to start a Qwen call"
        )

    metadata: dict[str, Any] = {}
    try:
        metadata = json.loads(ctx.job.metadata or "{}")
    except Exception:
        pass

    profile = metadata.get("agentProfile") or {}
    if not isinstance(profile, dict):
        profile = {}

    voice = _voice_from_profile(profile)

    logger.info(
        "starting Qwen realtime session",
        extra={
            "room": ctx.room.name,
            "model": QWEN_MODEL,
            "voice": voice,
            "profile": profile.get("id", ""),
        },
    )

    # vLLM-Omni exposes an OpenAI-Realtime-compatible /v1/realtime endpoint.
    # The OpenAI plugin constructs <base_url>/realtime?model=..., so the env
    # should be an HTTP(S) base ending in /v1, e.g. https://gpu.example/v1.
    realtime = openai.realtime.RealtimeModel(
        model=QWEN_MODEL,
        voice=voice,
        base_url=QWEN_BASE_URL,
        api_key=QWEN_API_KEY,
        modalities=["audio"],
    )

    session = AgentSession(llm=realtime)

    await session.start(
        agent=QwenRealtimeAgent(profile),
        room=ctx.room,
    )
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="lumin-qwen",
            num_idle_processes=1,
            initialize_process_timeout=30.0,
            job_memory_warn_mb=700,
        )
    )
