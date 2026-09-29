from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import soundfile as sf
import torch
from flask import Flask, Response, jsonify, request
from openvoice.api import ToneColorConverter
from piper import PiperVoice, SynthesisConfig

OPENVOICE_DIR = Path(os.getenv("OPENVOICE_DIR", "/opt/OpenVoice"))
CONVERTER_DIR = Path(os.getenv("OPENVOICE_CONVERTER_DIR", "/opt/OpenVoice/checkpoints_v2/converter"))
PIPER_MODEL = Path(os.getenv("PIPER_MODEL", "/app/voices/lumin-ptpt.onnx"))
CACHE = Path("/tmp/lumin-openvoice-cache")
CACHE.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
lock = threading.Lock()
device = "cuda:0" if torch.cuda.is_available() else "cpu"

converter: ToneColorConverter | None = None
piper_voice: PiperVoice | None = None
source_se = None
target_cache: dict[str, torch.Tensor] = {}


def _safe_https(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _piper_to_wav(text: str, out_path: Path, profile: str = "natural") -> None:
    if piper_voice is None:
        raise RuntimeError("Piper voice not ready")
    profiles = {
        "natural": dict(length_scale=1.08, noise_scale=0.38, noise_w_scale=0.43, volume=1.00),
        "clear": dict(length_scale=1.11, noise_scale=0.30, noise_w_scale=0.36, volume=1.01),
        "commercial": dict(length_scale=1.07, noise_scale=0.40, noise_w_scale=0.45, volume=1.02),
    }
    settings = profiles.get(profile, profiles["natural"])
    syn = SynthesisConfig(
        length_scale=settings["length_scale"],
        noise_scale=settings["noise_scale"],
        noise_w_scale=settings["noise_w_scale"],
        normalize_audio=True,
        volume=settings["volume"],
    )
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(piper_voice.config.sample_rate)
        for chunk in piper_voice.synthesize(text, syn_config=syn):
            wf.writeframes(chunk.audio_int16_bytes)


def _download_reference(url: str) -> Path:
    if not _safe_https(url):
        raise ValueError("voice sample URL must use https")
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    wav_path = CACHE / f"{key}.wav"
    if wav_path.exists() and wav_path.stat().st_size > 1000:
        return wav_path
    source = CACHE / f"{key}.source"
    req = urllib.request.Request(url, headers={"User-Agent":"LuminOpenVoice/1.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=35) as resp, source.open("wb") as out:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 9 * 1024 * 1024:
                raise ValueError("voice sample too large")
            out.write(chunk)
    subprocess.run([
        "ffmpeg","-y","-loglevel","error","-i",str(source),
        "-ac","1","-ar","22050","-t","30",str(wav_path),
    ],check=True,capture_output=True)
    source.unlink(missing_ok=True)
    return wav_path


def _target_embedding(url: str):
    global target_cache
    if converter is None:
        raise RuntimeError("OpenVoice converter not ready")
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cached = target_cache.get(key)
    if cached is not None:
        return cached
    ref = _download_reference(url)
    emb = converter.extract_se([str(ref)])
    target_cache[key] = emb
    if len(target_cache) > 32:
        target_cache.pop(next(iter(target_cache)))
    return emb


def init_models() -> None:
    global converter, piper_voice, source_se
    converter = ToneColorConverter(
        str(CONVERTER_DIR / "config.json"),
        device=device,
        enable_watermark=False,
    )
    converter.load_ckpt(str(CONVERTER_DIR / "checkpoint.pth"))
    piper_voice = PiperVoice.load(str(PIPER_MODEL))

    source_ref = CACHE / "piper-source.wav"
    _piper_to_wav(
        "Olá. Sou o Lumin. Esta é uma amostra de voz em português de Portugal.",
        source_ref,
        "natural",
    )
    source_se = converter.extract_se([str(source_ref)])


init_error = ""
try:
    init_models()
except Exception as exc:
    init_error = str(exc)
    app.logger.exception("OpenVoice startup failed")


@app.get("/health")
def health():
    ready = converter is not None and piper_voice is not None and source_se is not None
    body = {
        "ok": ready,
        "engine": "OpenVoice V2 + Piper PT-PT",
        "device": device,
        "error": init_error or None,
    }
    return jsonify(body), (200 if ready else 503)


@app.post("/synthesize")
def synthesize():
    if converter is None or piper_voice is None or source_se is None:
        return jsonify({"error": init_error or "voice model is not ready"}), 503

    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    prompt_url = str(body.get("audio_prompt_url") or "").strip()
    voice_profile = str(body.get("voice_profile") or "natural").strip().lower()
    if voice_profile not in {"natural","clear","commercial"}:
        voice_profile = "natural"
    if not text:
        return jsonify({"error":"text is required"}),400
    if len(text) > 700:
        return jsonify({"error":"text too long"}),400
    if prompt_url and not _safe_https(prompt_url):
        return jsonify({"error":"audio_prompt_url must use https"}),400

    with lock:
        try:
            with tempfile.TemporaryDirectory(prefix="lumin-openvoice-") as td:
                root = Path(td)
                src = root / "source.wav"
                _piper_to_wav(text, src, voice_profile)
                if prompt_url:
                    tgt_se = _target_embedding(prompt_url)
                    out = root / "cloned.wav"
                    converter.convert(
                        audio_src_path=str(src),
                        src_se=source_se,
                        tgt_se=tgt_se,
                        output_path=str(out),
                        message="@Lumin",
                    )
                    final_path = out
                    voice_header = "openvoice-clone"
                else:
                    final_path = src
                    voice_header = f"piper-{voice_profile}"
                audio, sr = sf.read(str(final_path), dtype="float32", always_2d=False)
                audio = np.asarray(audio, dtype=np.float32).reshape(-1)
                audio = np.clip(audio, -1.0, 1.0)
                pcm16 = (audio * 32767.0).astype(np.int16).tobytes()
            return Response(
                pcm16,
                mimetype="audio/pcm",
                headers={
                    "X-Sample-Rate":str(sr),
                    "X-Channels":"1",
                    "X-Lumin-Voice":voice_header,
                    "Cache-Control":"no-store",
                },
            )
        except Exception as exc:
            app.logger.exception("OpenVoice synthesis failed")
            return jsonify({"error":str(exc)[:1200]}),500


if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8080")),threaded=True)
