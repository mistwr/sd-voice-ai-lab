from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_file

SADTALKER_DIR = Path(os.getenv("SADTALKER_DIR", "/opt/SadTalker"))
JOBS_DIR = Path(os.getenv("LUMIN_AVATAR_JOBS_DIR", "/tmp/lumin-avatar-jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
jobs: dict[str, dict] = {}
render_lock = threading.Lock()


def _safe_remote(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _download(url: str, path: Path, max_bytes: int) -> None:
    if not _safe_remote(url):
        raise ValueError("Only https asset URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "LuminAvatar/1.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=45) as response, path.open("wb") as output:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Input asset is too large")
            output.write(chunk)


def _render(job_id: str, payload: dict) -> None:
    job = jobs[job_id]
    root = JOBS_DIR / job_id
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "avatar.png"
    audio_src = root / "audio-input"
    audio_wav = root / "speech.wav"
    result_dir = root / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    try:
        job["status"] = "downloading"
        _download(payload["image_url"], image_path, 9 * 1024 * 1024)
        _download(payload["audio_url"], audio_src, 24 * 1024 * 1024)

        job["status"] = "preparing"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(audio_src),
                "-ac", "1", "-ar", "16000",
                "-t", "45",
                str(audio_wav),
            ],
            check=True,
            capture_output=True,
        )

        if not SADTALKER_DIR.exists():
            raise RuntimeError("SadTalker runtime is not installed")

        preprocess = payload.get("preprocess", "crop")
        if preprocess not in {"crop", "resize", "full", "extcrop", "extfull"}:
            preprocess = "crop"
        size = "512" if int(payload.get("size", 512)) >= 512 else "256"

        command = [
            sys.executable,
            str(SADTALKER_DIR / "inference.py"),
            "--driven_audio", str(audio_wav),
            "--source_image", str(image_path),
            "--result_dir", str(result_dir),
            "--preprocess", preprocess,
            "--size", size,
        ]
        if bool(payload.get("still", True)):
            command.append("--still")

        job["status"] = "rendering"
        job["startedRenderAt"] = time.time()
        with render_lock:
            proc = subprocess.run(
                command,
                cwd=str(SADTALKER_DIR),
                capture_output=True,
                text=True,
                timeout=20 * 60,
            )

        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "avatar render failed")[-4000:])

        videos = sorted(result_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not videos:
            raise RuntimeError("Avatar engine finished without an MP4 result")

        final_path = root / "avatar.mp4"
        shutil.copy2(videos[0], final_path)
        job.update({"status": "ready", "video": str(final_path), "finishedAt": time.time()})
    except Exception as exc:
        job.update({"status": "failed", "error": str(exc)[:4000], "finishedAt": time.time()})


@app.get("/health")
def health():
    return jsonify({
        "ok": SADTALKER_DIR.exists(),
        "engine": "SadTalker",
        "mode": "self-hosted",
        "queue": sum(1 for job in jobs.values() if job.get("status") not in {"ready", "failed"}),
    })


@app.post("/jobs")
def create_job():
    payload = request.get_json(silent=True) or {}
    image_url = str(payload.get("image_url") or "")
    audio_url = str(payload.get("audio_url") or "")
    if not _safe_remote(image_url) or not _safe_remote(audio_url):
        return jsonify({"error": "image_url and audio_url must use https"}), 400

    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "createdAt": time.time(),
        "engine": "SadTalker",
    }
    worker = threading.Thread(
        target=_render,
        args=(job_id, {
            "image_url": image_url,
            "audio_url": audio_url,
            "preprocess": str(payload.get("preprocess") or "crop"),
            "still": bool(payload.get("still", True)),
            "size": int(payload.get("size") or 512),
        }),
        daemon=True,
    )
    worker.start()
    return jsonify(jobs[job_id])


@app.get("/jobs/<job_id>")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown avatar job"}), 404
    return jsonify({k: v for k, v in job.items() if k != "video"})


@app.get("/jobs/<job_id>/video")
def get_video(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown avatar job"}), 404
    if job.get("status") != "ready" or not job.get("video"):
        return jsonify({"error": f"Avatar job is {job.get('status')}"}), 409
    return send_file(
        job["video"],
        mimetype="video/mp4",
        as_attachment=False,
        download_name=f"lumin-avatar-{job_id}.mp4",
        max_age=300,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threaded=True)
