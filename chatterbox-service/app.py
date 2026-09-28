from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lumin-chatterbox")

app = FastAPI(title="LUMIN Chatterbox Voice Service")
model: ChatterboxMultilingualTTS | None = None
model_error: str = ""
synth_lock = asyncio.Lock()
VOICE_CACHE = Path("/tmp/lumin-voice-cache")
VOICE_CACHE.mkdir(parents=True, exist_ok=True)


class SynthesisRequest(BaseModel):
    text: str
    language_id: str = "pt"
    audio_prompt_url: str | None = None


def choose_device() -> str:
    if os.getenv("CHATTERBOX_DEVICE"):
        return os.environ["CHATTERBOX_DEVICE"]
    return "cuda" if torch.cuda.is_available() else "cpu"


@app.on_event("startup")
async def startup() -> None:
    global model, model_error
    try:
        device = choose_device()
        logger.info("loading Chatterbox Multilingual V3 on %s", device)
        model = await asyncio.to_thread(
            ChatterboxMultilingualTTS.from_pretrained,
            device=device,
            t3_model="v3",
        )
        logger.info("Chatterbox ready; sample_rate=%s", getattr(model, "sr", 24000))
    except Exception as exc:
        model_error = str(exc)
        logger.exception("Chatterbox failed to load")


@app.get("/health")
async def health():
    return {
        "ok": model is not None,
        "model": "chatterbox-multilingual-v3",
        "device": choose_device(),
        "sample_rate": int(getattr(model, "sr", 24000)) if model is not None else 24000,
        "error": model_error or None,
    }


async def reference_wav(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="audio_prompt_url must use https")

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    wav_path = VOICE_CACHE / f"{digest}.wav"
    if wav_path.exists() and wav_path.stat().st_size > 1000:
        return str(wav_path)

    source_path = VOICE_CACHE / f"{digest}.source"
    timeout = httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > 9 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="voice sample too large")
        source_path.write_bytes(response.content)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(source_path),
        "-ac", "1", "-ar", "24000",
        "-t", "35",
        str(wav_path),
    ]
    try:
        await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
    except Exception as exc:
        source_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"could not decode voice sample: {exc}")
    finally:
        source_path.unlink(missing_ok=True)

    return str(wav_path)


@app.post("/synthesize")
async def synthesize(req: SynthesisRequest):
    if model is None:
        raise HTTPException(status_code=503, detail=model_error or "voice model is not ready")

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 900:
        raise HTTPException(status_code=400, detail="text too long")

    allowed_languages = {
        "ar","da","de","el","en","es","fi","fr","he","hi","it","ja","ko",
        "ms","nl","no","pl","pt","ru","sv","sw","tr","zh",
    }
    language_id = req.language_id if req.language_id in allowed_languages else "pt"
    prompt_path = await reference_wav(req.audio_prompt_url) if req.audio_prompt_url else None

    async with synth_lock:
        def run_generation():
            kwargs = {"language_id": language_id}
            if prompt_path:
                kwargs["audio_prompt_path"] = prompt_path
            return model.generate(text, **kwargs)

        wav = await asyncio.to_thread(run_generation)

    audio = wav.detach().cpu().float().numpy()
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype(np.int16).tobytes()
    sr = int(getattr(model, "sr", 24000))

    return Response(
        content=pcm16,
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(sr),
            "X-Channels": "1",
            "Cache-Control": "no-store",
            "X-Lumin-Voice": "custom" if prompt_path else "system",
        },
    )
