from __future__ import annotations

import asyncio
import logging

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from tts_eu_pt import TTS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kokoro-ptpt-service")

app = FastAPI(title="LUMIN Kokoro PT-PT TTS")
engine: TTS | None = None
synth_lock = asyncio.Lock()


class SynthesisRequest(BaseModel):
    text: str


@app.on_event("startup")
async def startup() -> None:
    global engine
    logger.info("loading Kokoro European Portuguese voice")
    engine = await asyncio.to_thread(TTS)
    # Warm inference once so the first real visitor does not pay graph/model warm-up.
    await asyncio.to_thread(engine.say, "Olá. Sou o Lumin.")
    logger.info("Kokoro PT-PT service ready")


@app.get("/health")
async def health():
    return {"ok": engine is not None, "voice": "kokoro-eu-pt", "sample_rate": 24000}


@app.post("/synthesize")
async def synthesize(req: SynthesisRequest):
    if engine is None:
        raise HTTPException(status_code=503, detail="voice not ready")

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 1600:
        raise HTTPException(status_code=400, detail="text too long")

    # This CPU service intentionally serializes synthesis. Voice agents normally
    # synthesize one utterance at a time and this prevents CPU thrash.
    async with synth_lock:
        wav = await asyncio.to_thread(engine.say, text)

    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype(np.int16).tobytes()

    return Response(
        content=pcm16,
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": "24000",
            "X-Channels": "1",
            "Cache-Control": "no-store",
        },
    )
