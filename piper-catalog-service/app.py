from __future__ import annotations

import asyncio
import audioop
import hashlib
import json
import logging
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx
import onnxruntime
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from piper import PiperVoice, SynthesisConfig
from piper.config import PiperConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lumin-piper-catalog")

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
FILE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
CACHE_DIR = Path(os.getenv("PIPER_CACHE_DIR", "/tmp/lumin-piper-voices"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_SAMPLE_RATE = 24000
MAX_LOADED = max(1, int(os.getenv("PIPER_MAX_LOADED", "4")))
CATALOG_TTL_SECONDS = 60 * 60

app = FastAPI(title="LUMIN Piper Voice Catalog")
# Railway service root: piper-catalog-service

_catalog: dict[str, Any] = {}
_catalog_loaded_at = 0.0
_loaded: "OrderedDict[str, PiperVoice]" = OrderedDict()
_load_lock = asyncio.Lock()


class SynthesisRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1200)
    voice: str = Field(..., min_length=3, max_length=120)
    length_scale: float = Field(default=1.03, ge=0.72, le=1.55)


def _safe_key(value: str) -> str:
    key = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,120}", key):
        raise HTTPException(status_code=400, detail="Invalid voice key")
    return key


async def _catalog_json() -> dict[str, Any]:
    global _catalog, _catalog_loaded_at
    loop = asyncio.get_running_loop()
    now = loop.time()
    if _catalog and now - _catalog_loaded_at < CATALOG_TTL_SECONDS:
        return _catalog

    timeout = httpx.Timeout(connect=8.0, read=20.0, write=8.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(CATALOG_URL)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=502, detail="Piper catalog is unavailable")

    _catalog = data
    _catalog_loaded_at = now
    return data


def _voice_card(key: str, row: dict[str, Any]) -> dict[str, Any]:
    lang = row.get("language") or {}
    files = row.get("files") or {}
    model_path = next((p for p in files if p.endswith(".onnx") and not p.endswith(".onnx.json")), "")
    size_bytes = int((files.get(model_path) or {}).get("size_bytes") or 0) if model_path else 0
    return {
        "key": key,
        "name": row.get("name") or key,
        "languageCode": lang.get("code") or "",
        "language": lang.get("name_english") or lang.get("name_native") or "",
        "country": lang.get("country_english") or "",
        "quality": row.get("quality") or "",
        "speakers": int(row.get("num_speakers") or 1),
        "sizeBytes": size_bytes,
        "engine": "piper",
        "source": "rhasspy/piper-voices",
    }


@app.get("/health")
async def health():
    try:
        catalog = await _catalog_json()
        return {
            "ok": True,
            "service": "LUMIN Piper Catalog",
            "voices": len(catalog),
            "loaded": list(_loaded.keys()),
            "sampleRate": OUTPUT_SAMPLE_RATE,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "sampleRate": OUTPUT_SAMPLE_RATE}


@app.get("/catalog")
async def catalog(
    language: str = Query(default="", max_length=20),
    q: str = Query(default="", max_length=80),
    quality: str = Query(default="", max_length=20),
    limit: int = Query(default=500, ge=1, le=1000),
):
    data = await _catalog_json()
    language = language.strip().lower().replace("-", "_")
    q = q.strip().lower()
    quality = quality.strip().lower()

    out = []
    for key, raw in data.items():
        if not isinstance(raw, dict):
            continue
        card = _voice_card(key, raw)
        lang = card["languageCode"].lower()
        if language and not (lang == language or lang.startswith(language + "_") or lang.split("_")[0] == language):
            continue
        if quality and card["quality"].lower() != quality:
            continue
        haystack = " ".join([
            card["key"], card["name"], card["language"], card["country"], card["quality"]
        ]).lower()
        if q and q not in haystack:
            continue
        out.append(card)

    quality_rank = {"high": 0, "medium": 1, "low": 2, "x_low": 3}
    out.sort(key=lambda v: (
        0 if v["languageCode"].lower() == "pt_pt" else 1,
        quality_rank.get(v["quality"], 9),
        v["languageCode"],
        v["name"],
    ))
    return {"ok": True, "count": len(out[:limit]), "total": len(out), "voices": out[:limit]}


async def _download_file(client: httpx.AsyncClient, remote_path: str, meta: dict[str, Any], target: Path):
    if target.exists() and target.stat().st_size > 1000:
        expected = str(meta.get("md5_digest") or "").lower()
        if not expected:
            return
        digest = await asyncio.to_thread(lambda: hashlib.md5(target.read_bytes()).hexdigest())
        if digest == expected:
            return
        target.unlink(missing_ok=True)

    url = f"{FILE_BASE}/{remote_path}"
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as fh:
            async for chunk in response.aiter_bytes(1024 * 1024):
                fh.write(chunk)

    expected = str(meta.get("md5_digest") or "").lower()
    if expected:
        digest = await asyncio.to_thread(lambda: hashlib.md5(target.read_bytes()).hexdigest())
        if digest != expected:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail="Voice download checksum mismatch")


async def _load_voice(key: str) -> tuple[PiperVoice, int]:
    key = _safe_key(key)
    if key in _loaded:
        voice = _loaded.pop(key)
        _loaded[key] = voice
        return voice, int(voice.config.sample_rate)

    async with _load_lock:
        if key in _loaded:
            voice = _loaded.pop(key)
            _loaded[key] = voice
            return voice, int(voice.config.sample_rate)

        catalog = await _catalog_json()
        raw = catalog.get(key)
        if not isinstance(raw, dict):
            raise HTTPException(status_code=404, detail="Voice not found in Piper catalog")

        files = raw.get("files") or {}
        model_remote = next((p for p in files if p.endswith(".onnx") and not p.endswith(".onnx.json")), "")
        config_remote = next((p for p in files if p.endswith(".onnx.json")), "")
        if not model_remote or not config_remote:
            raise HTTPException(status_code=502, detail="Voice files are incomplete")

        voice_dir = CACHE_DIR / key
        voice_dir.mkdir(parents=True, exist_ok=True)
        model_path = voice_dir / "voice.onnx"
        config_path = voice_dir / "voice.onnx.json"

        timeout = httpx.Timeout(connect=10.0, read=120.0, write=15.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            await _download_file(client, model_remote, files.get(model_remote) or {}, model_path)
            await _download_file(client, config_remote, files.get(config_remote) or {}, config_path)

        config_dict = json.loads(config_path.read_text(encoding="utf-8"))
        sess_options = onnxruntime.SessionOptions()
        sess_options.intra_op_num_threads = max(1, int(os.getenv("PIPER_ORT_THREADS", "2")))
        sess_options.inter_op_num_threads = 1
        sess_options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        voice = PiperVoice(
            config=PiperConfig.from_dict(config_dict),
            session=onnxruntime.InferenceSession(
                str(model_path),
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            ),
        )
        _loaded[key] = voice
        while len(_loaded) > MAX_LOADED:
            evicted, _ = _loaded.popitem(last=False)
            logger.info("evicted cached Piper voice %s", evicted)

        logger.info("loaded Piper catalog voice %s at %s Hz", key, voice.config.sample_rate)
        return voice, int(voice.config.sample_rate)


def _clean_text(text: str) -> str:
    text = re.sub(r"[*_#\x60~]+", " ", text)
    text = text.replace("—", ", ").replace("–", ", ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


@app.post("/synthesize")
async def synthesize(payload: SynthesisRequest):
    voice, native_rate = await _load_voice(payload.voice)
    text = _clean_text(payload.text)
    config = SynthesisConfig(
        length_scale=payload.length_scale,
        noise_scale=0.38,
        noise_w_scale=0.43,
        normalize_audio=True,
        volume=1.0,
    )

    def run() -> bytes:
        chunks = list(voice.synthesize(text, syn_config=config))
        pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        if native_rate != OUTPUT_SAMPLE_RATE:
            pcm, _ = audioop.ratecv(pcm, 2, 1, native_rate, OUTPUT_SAMPLE_RATE, None)
        return pcm

    pcm16 = await asyncio.to_thread(run)
    return Response(
        content=pcm16,
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(OUTPUT_SAMPLE_RATE),
            "X-Channels": "1",
            "X-Lumin-Voice": payload.voice,
            "Cache-Control": "no-store",
        },
    )
