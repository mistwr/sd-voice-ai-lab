from __future__ import annotations

import json
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from livekit import api

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

app = FastAPI(title="LUMIN Voice Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://luminai.pt",
        "https://www.luminai.pt",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True, "service": "lumin-voice-gateway"}


@app.post("/token")
async def create_token():
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="LiveKit is not configured")

    room_name = f"lumin-web-{uuid.uuid4().hex[:12]}"
    identity = f"visitor-{uuid.uuid4().hex[:10]}"

    metadata = json.dumps(
        {
            "mode": "web",
            "source": "luminai.pt",
        }
    )

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Visitante LUMIN")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name="lumin-web",
                        metadata=metadata,
                    )
                ]
            )
        )
        .to_jwt()
    )

    return {
        "serverUrl": LIVEKIT_URL,
        "token": token,
        "roomName": room_name,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!doctype html>
<html lang="pt-PT">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LUMIN Voice Gateway</title>
<style>
body{font-family:system-ui;background:#050505;color:#fff;min-height:100vh;display:grid;place-items:center;margin:0}
main{width:min(520px,92vw);text-align:center;padding:36px;border:1px solid #33270f;border-radius:28px;background:linear-gradient(180deg,#0c0b09,#050505)}
h1{color:#ffdda0;margin:0 0 8px;font-size:38px}.muted{color:#9e978b}.ok{color:#79e79d}
</style>
</head>
<body><main><h1>LUMIN AI</h1><p class="muted">Voice Gateway online</p><p class="ok">● operacional</p><p class="muted">O botão de voz é servido em luminai.pt/voz/</p></main></body>
</html>"""
