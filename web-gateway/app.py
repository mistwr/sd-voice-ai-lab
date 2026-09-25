from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest, ListSIPOutboundTrunkRequest
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

LUMIN_CALL_API_KEY = os.getenv("LUMIN_CALL_API_KEY", "")
LUMIN_OUTBOUND_TRUNK_NAME = os.getenv(
    "LUMIN_OUTBOUND_TRUNK_NAME", "Lumin Twilio Outbound"
)
LUMIN_CALLER_ID = os.getenv("LUMIN_CALLER_ID", "")
LUMIN_AGENT_NAME = os.getenv("LUMIN_AGENT_NAME", "lumin-web")

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

_CALLS: dict[str, dict[str, Any]] = {}
_LAST_CALL_AT = 0.0
_TEST_CALL_USED = False
_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class OutboundCallRequest(BaseModel):
    phone: str = Field(..., description="Destination in E.164, e.g. +351912345678")
    name: str = Field(default="Cliente", max_length=100)


def _require_call_key(x_lumin_key: str | None) -> None:
    if not LUMIN_CALL_API_KEY:
        raise HTTPException(status_code=503, detail="Outbound calling is not configured")
    if not x_lumin_key or not hmac.compare_digest(x_lumin_key, LUMIN_CALL_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid call API key")


def _response_items(message: Any) -> list[Any]:
    """Handle LiveKit protocol response field names across SDK versions."""
    for field in ("items", "trunks", "sip_outbound_trunks"):
        value = getattr(message, field, None)
        if value:
            return list(value)
    return []


async def _find_lumin_trunk(lkapi: api.LiveKitAPI):
    response = await lkapi.sip.list_outbound_trunk(ListSIPOutboundTrunkRequest())
    trunks = _response_items(response)
    for trunk in trunks:
        if getattr(trunk, "name", "") == LUMIN_OUTBOUND_TRUNK_NAME:
            return trunk

    # Fallback for older trunk created before the naming convention.
    for trunk in trunks:
        address = (
            getattr(trunk, "address", "")
            or getattr(trunk, "hostname", "")
            or getattr(trunk, "sip_uri", "")
        )
        if "lumin-ai-pt.pstn.twilio.com" in address:
            return trunk

    raise RuntimeError(
        f"Outbound trunk '{LUMIN_OUTBOUND_TRUNK_NAME}' was not found in LiveKit"
    )


async def _run_outbound_call(call_id: str, phone: str, name: str) -> None:
    state = _CALLS[call_id]
    room_name = state["roomName"]

    try:
        state["status"] = "preparing"

        async with api.LiveKitAPI() as lkapi:
            trunk = await _find_lumin_trunk(lkapi)
            trunk_id = getattr(trunk, "sip_trunk_id", "") or getattr(trunk, "id", "")
            if not trunk_id:
                raise RuntimeError("LiveKit returned the outbound trunk without an ID")

            state["trunkId"] = trunk_id
            state["status"] = "ringing"

            req_kwargs: dict[str, Any] = {
                "sip_trunk_id": trunk_id,
                "sip_call_to": phone,
                "room_name": room_name,
                "participant_identity": f"callee-{call_id}",
                "participant_name": name,
                "participant_metadata": json.dumps(
                    {"mode": "outbound", "callId": call_id, "phone": phone, "name": name}
                ),
                "wait_until_answered": True,
            }

            # Explicitly select the verified Portuguese caller ID when configured.
            if LUMIN_CALLER_ID:
                req_kwargs["sip_number"] = LUMIN_CALLER_ID

            sip_participant = await lkapi.sip.create_sip_participant(
                CreateSIPParticipantRequest(**req_kwargs),
                timeout=65,
            )

            state["status"] = "answered"
            state["sipParticipantId"] = (
                getattr(sip_participant, "participant_id", "")
                or getattr(sip_participant, "participant_identity", "")
            )

            # Dispatch only after the callee has answered. This prevents Lumin from
            # saying his greeting into an empty room while the phone is still ringing.
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=LUMIN_AGENT_NAME,
                    room=room_name,
                    metadata=json.dumps(
                        {
                            "mode": "outbound",
                            "source": "lumin-call-api",
                            "callId": call_id,
                            "phone": phone,
                            "name": name,
                        }
                    ),
                )
            )

            state["dispatchId"] = getattr(dispatch, "id", "")
            state["status"] = "connected"

    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)[:500]


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "lumin-voice-gateway",
        "outboundConfigured": bool(LUMIN_CALL_API_KEY),
    }


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
                        agent_name=LUMIN_AGENT_NAME,
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


@app.post("/api/call")
async def start_outbound_call(
    payload: OutboundCallRequest,
    x_lumin_key: str | None = Header(default=None, alias="X-Lumin-Key"),
):
    global _LAST_CALL_AT

    _require_call_key(x_lumin_key)

    phone = payload.phone.strip().replace(" ", "")
    if not _PHONE_RE.fullmatch(phone):
        raise HTTPException(
            status_code=400,
            detail="phone must be in E.164 format, for example +351912345678",
        )

    # The Twilio trunk currently allows one new call setup per second.
    now = time.monotonic()
    if now - _LAST_CALL_AT < 1.2:
        raise HTTPException(status_code=429, detail="Wait a moment before starting another call")
    _LAST_CALL_AT = now

    call_id = uuid.uuid4().hex[:12]
    room_name = f"lumin-call-{call_id}"
    _CALLS[call_id] = {
        "callId": call_id,
        "status": "queued",
        "phone": phone,
        "name": payload.name,
        "roomName": room_name,
        "createdAt": time.time(),
    }

    asyncio.create_task(_run_outbound_call(call_id, phone, payload.name))

    return {
        "ok": True,
        "callId": call_id,
        "status": "queued",
        "roomName": room_name,
    }


@app.post("/api/test-call-once")
async def test_call_once():
    global _TEST_CALL_USED
    if _TEST_CALL_USED:
        raise HTTPException(status_code=410, detail="Test call already used")
    _TEST_CALL_USED = True

    call_id = uuid.uuid4().hex[:12]
    room_name = f"lumin-call-{call_id}"
    phone = "+351923343490"
    name = "Teste Lumin"
    _CALLS[call_id] = {
        "callId": call_id,
        "status": "queued",
        "phone": phone,
        "name": name,
        "roomName": room_name,
        "createdAt": time.time(),
        "oneShotTest": True,
    }
    asyncio.create_task(_run_outbound_call(call_id, phone, name))
    return {"ok": True, "callId": call_id, "status": "queued", "roomName": room_name}


@app.get("/api/call/{call_id}")
async def outbound_call_status(
    call_id: str,
    x_lumin_key: str | None = Header(default=None, alias="X-Lumin-Key"),
):
    _require_call_key(x_lumin_key)
    state = _CALLS.get(call_id)
    if not state:
        raise HTTPException(status_code=404, detail="Unknown call ID")
    return state


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
<body><main><h1>LUMIN AI</h1><p class="muted">Voice Gateway online</p><p class="ok">● operacional</p><p class="muted">WebRTC + outbound SIP gateway</p></main></body>
</html>"""
