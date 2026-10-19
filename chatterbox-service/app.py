from __future__ import annotations

import asyncio
import gc
import hashlib
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from huggingface_hub import hf_hub_download, snapshot_download
from pydantic import BaseModel
from safetensors.torch import load_file

from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals
from chatterbox.models.s3gen import S3Gen
from chatterbox.models.t3 import T3
from chatterbox.models.t3.modules.t3_config import T3Config
from chatterbox.models.tokenizers import MTLTokenizer
from chatterbox.models.voice_encoder import VoiceEncoder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lumin-chatterbox")

app = FastAPI(title="LUMIN PT-PT Chatterbox Voice Service")
model: ChatterboxMultilingualTTS | None = None
model_error: str = ""
synth_lock = asyncio.Lock()
VOICE_CACHE = Path("/tmp/lumin-voice-cache")
VOICE_CACHE.mkdir(parents=True, exist_ok=True)
PTPT_REPO_ID = "ResembleAI/Chatterbox-Multilingual-pt-pt"
BASE_REPO_ID = "ResembleAI/chatterbox"
PTPT_T3_FILE = "t3_pt_pt.safetensors"
PTPT_S3GEN_FILE = "s3gen_v3.pt"
TOKENIZER_FILE = "grapheme_mtl_merged_expanded_v1.json"


class SynthesisRequest(BaseModel):
    text: str
    language_id: str = "pt"
    audio_prompt_url: str | None = None


def choose_device() -> str:
    if os.getenv("CHATTERBOX_DEVICE"):
        return os.environ["CHATTERBOX_DEVICE"]
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_ptpt_chatterbox(device: str) -> ChatterboxMultilingualTTS:
    """Load Resemble's dedicated PT-PT fine-tune with shared Chatterbox assets."""
    token = os.getenv("HF_TOKEN")
    base_dir = Path(
        snapshot_download(
            repo_id=BASE_REPO_ID,
            repo_type="model",
            revision="main",
            allow_patterns=["ve.pt", "conds.pt"],
            token=token,
        )
    )
    t3_path = hf_hub_download(
        repo_id=PTPT_REPO_ID,
        filename=PTPT_T3_FILE,
        repo_type="model",
        revision="main",
        token=token,
    )
    s3gen_path = hf_hub_download(
        repo_id=PTPT_REPO_ID,
        filename=PTPT_S3GEN_FILE,
        repo_type="model",
        revision="main",
        token=token,
    )
    tokenizer_path = hf_hub_download(
        repo_id=PTPT_REPO_ID,
        filename=TOKENIZER_FILE,
        repo_type="model",
        revision="main",
        token=token,
    )

    # Build only the PT-PT checkpoint, avoiding a second generic T3 model in RAM.
    voice_encoder = VoiceEncoder()
    voice_encoder.load_state_dict(
        torch.load(base_dir / "ve.pt", map_location="cpu", weights_only=True)
    )
    voice_encoder.to(device).eval()

    t3 = T3(T3Config.multilingual())
    t3_state = load_file(t3_path)
    if "model" in t3_state:
        t3_state = t3_state["model"][0]
    t3.load_state_dict(t3_state, strict=True)
    del t3_state
    gc.collect()
    t3.to(device).eval()

    s3gen = S3Gen()
    s3gen_state = torch.load(s3gen_path, map_location="cpu", weights_only=True)
    s3gen.load_state_dict(s3gen_state, strict=False)
    del s3gen_state
    gc.collect()
    s3gen.to(device).eval()

    tokenizer = MTLTokenizer(str(tokenizer_path))
    conds_path = base_dir / "conds.pt"
    conds = Conditionals.load(conds_path, map_location="cpu").to(device)

    return ChatterboxMultilingualTTS(
        t3=t3,
        s3gen=s3gen,
        ve=voice_encoder,
        tokenizer=tokenizer,
        device=device,
        conds=conds,
    )


@app.on_event("startup")
async def startup() -> None:
    global model, model_error
    try:
        device = choose_device()
        logger.info("loading dedicated Chatterbox PT-PT V3 on %s", device)
        model = await asyncio.to_thread(load_ptpt_chatterbox, device)
        logger.info(
            "Chatterbox PT-PT ready",
            extra={"model": "chatterbox-multilingual-pt-pt-v3", "sample_rate": model.sr},
        )
    except Exception as exc:
        model_error = str(exc)
        logger.exception("Chatterbox PT-PT failed to load")


@app.get("/health")
async def health():
    body = {
        "ok": model is not None,
        "model": "chatterbox-multilingual-pt-pt-v3",
        "device": choose_device(),
        "sample_rate": int(getattr(model, "sr", 24000)) if model is not None else 24000,
        "error": model_error or None,
    }
    return JSONResponse(body, status_code=200 if model is not None else 503)


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
    if synth_lock.locked():
        # Reject saturation immediately; the caller switches to local Piper.
        raise HTTPException(status_code=429, detail="Chatterbox is busy; use local fallback")

    allowed_languages = {
        "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko",
        "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh",
    }
    language_id = req.language_id if req.language_id in allowed_languages else "pt"
    prompt_path = await reference_wav(req.audio_prompt_url) if req.audio_prompt_url else None

    started = time.perf_counter()
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
    if not pcm16:
        raise HTTPException(status_code=500, detail="Chatterbox generated empty audio")
    sr = int(getattr(model, "sr", 24000))
    logger.info(
        "Chatterbox speech generated",
        extra={
            "characters": len(text),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "audio_seconds": round(len(pcm16) / (sr * 2), 2),
            "custom_voice": bool(prompt_path),
        },
    )

    return Response(
        content=pcm16,
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(sr),
            "X-Channels": "1",
            "Cache-Control": "no-store",
            "X-Lumin-Voice": "pt-pt-custom" if prompt_path else "pt-pt-system",
        },
    )
