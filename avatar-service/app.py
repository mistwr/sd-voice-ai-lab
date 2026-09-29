from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

SADTALKER_DIR = Path(os.getenv("SADTALKER_DIR", "/opt/SadTalker"))
JOBS_DIR = Path(os.getenv("LUMIN_AVATAR_JOBS_DIR", "/tmp/lumin-avatar-jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LUMIN Avatar Engine")
jobs: dict[str, dict] = {}
render_lock = asyncio.Lock()


class RenderRequest(BaseModel):
    image_url: HttpUrl
    audio_url: HttpUrl
    preprocess: str = "crop"
    still: bool = True
    size: int = 512


def _safe_remote(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Only https asset URLs are allowed")


async def _download(url: str, path: Path, max_bytes: int) -> None:
    _safe_remote(url)
    timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
    total = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with path.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 256):
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail="Input asset is too large")
                    output.write(chunk)


async def _render(job_id: str, payload: RenderRequest) -> None:
    job = jobs[job_id]
    root = JOBS_DIR / job_id
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "avatar-input"
    audio_src = root / "audio-input"
    audio_wav = root / "speech.wav"
    result_dir = root / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    try:
        job["status"] = "downloading"
        await asyncio.gather(
            _download(str(payload.image_url), image_path, 9 * 1024 * 1024),
            _download(str(payload.audio_url), audio_src, 24 * 1024 * 1024),
        )

        job["status"] = "preparing"
        await asyncio.to_thread(
            subprocess.run,
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

        command = [
            sys.executable,
            str(SADTALKER_DIR / "inference.py"),
            "--driven_audio", str(audio_wav),
            "--source_image", str(image_path),
            "--result_dir", str(result_dir),
            "--preprocess", payload.preprocess if payload.preprocess in {"crop","resize","full","extcrop","extfull"} else "crop",
            "--size", "512" if payload.size >= 512 else "256",
        ]
        if payload.still:
            command.append("--still")

        job["status"] = "rendering"
        job["startedRenderAt"] = time.time()
        async with render_lock:
            proc = await asyncio.to_thread(
                subprocess.run,
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
        job.update({
            "status": "ready",
            "video": str(final_path),
            "finishedAt": time.time(),
        })
    except Exception as exc:
        job.update({
            "status": "failed",
            "error": str(exc)[:4000],
            "finishedAt": time.time(),
        })


@app.get("/health")
async def health():
    return {
        "ok": SADTALKER_DIR.exists(),
        "engine": "SadTalker",
        "mode": "self-hosted",
        "queue": sum(1 for job in jobs.values() if job.get("status") not in {"ready","failed"}),
    }


@app.post("/jobs")
async def create_job(payload: RenderRequest):
    _safe_remote(str(payload.image_url))
    _safe_remote(str(payload.audio_url))
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "createdAt": time.time(),
        "engine": "SadTalker",
    }
    asyncio.create_task(_render(job_id, payload))
    return jobs[job_id]


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown avatar job")
    return {k:v for k,v in job.items() if k != "video"}


@app.get("/jobs/{job_id}/video")
async def get_video(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown avatar job")
    if job.get("status") != "ready" or not job.get("video"):
        raise HTTPException(status_code=409, detail=f"Avatar job is {job.get('status')}")
    return FileResponse(
        job["video"],
        media_type="video/mp4",
        filename=f"lumin-avatar-{job_id}.mp4",
        headers={"Cache-Control":"private, max-age=300"},
    )
