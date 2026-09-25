from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
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

LUMIN_PLATFORM_PASSWORD_HASH = os.getenv("LUMIN_PLATFORM_PASSWORD_HASH", "")
LUMIN_PLATFORM_SESSION_SECRET = os.getenv("LUMIN_PLATFORM_SESSION_SECRET", "")
LUMIN_PLATFORM_SESSION_TTL = 60 * 60 * 8

logger = logging.getLogger("lumin-voice-gateway")
logging.basicConfig(level=logging.INFO)

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
_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class OutboundCallRequest(BaseModel):
    phone: str = Field(..., description="Destination in E.164, e.g. +351912345678")
    name: str = Field(default="Cliente", max_length=100)


class PlatformLoginRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=200)


class AgentProfile(BaseModel):
    id: str = Field(default="lumin", max_length=80)
    name: str = Field(default="Lumin", max_length=80)
    company: str = Field(default="LUMIN AI", max_length=120)
    objective: str = Field(default="Conversar e ajudar", max_length=500)
    product: str = Field(default="", max_length=1200)
    offer: str = Field(default="", max_length=1200)
    opening: str = Field(default="", max_length=800)
    objections: str = Field(default="", max_length=1800)
    notes: str = Field(default="", max_length=2200)
    tone: str = Field(default="Natural, profissional e direto", max_length=300)


class PlatformCallRequest(BaseModel):
    phone: str = Field(..., description="Destination in E.164")
    name: str = Field(default="Cliente", max_length=100)
    agent: AgentProfile = Field(default_factory=AgentProfile)


def _require_call_key(x_lumin_key: str | None) -> None:
    if not LUMIN_CALL_API_KEY:
        raise HTTPException(status_code=503, detail="Outbound calling is not configured")
    if not x_lumin_key or not hmac.compare_digest(x_lumin_key, LUMIN_CALL_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid call API key")


def _password_ok(code: str) -> bool:
    if not LUMIN_PLATFORM_PASSWORD_HASH:
        return False
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, LUMIN_PLATFORM_PASSWORD_HASH)


def _create_platform_token() -> str:
    if not LUMIN_PLATFORM_SESSION_SECRET:
        raise HTTPException(status_code=503, detail="Platform session is not configured")
    exp = int(time.time()) + LUMIN_PLATFORM_SESSION_TTL
    nonce = secrets.token_urlsafe(12)
    payload = f"{exp}.{nonce}"
    sig = hmac.new(
        LUMIN_PLATFORM_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{sig}"


def _require_platform_session(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Platform session required")
    token = authorization[7:].strip()
    try:
        exp_text, nonce, sig = token.split(".", 2)
        exp = int(exp_text)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid platform session")

    if exp < int(time.time()):
        raise HTTPException(status_code=401, detail="Platform session expired")

    payload = f"{exp}.{nonce}"
    expected = hmac.new(
        LUMIN_PLATFORM_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid platform session")


def _normalise_phone(phone: str) -> str:
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not _PHONE_RE.fullmatch(phone):
        raise HTTPException(
            status_code=400,
            detail="phone must be in E.164 format, for example +351912345678",
        )
    return phone


def _response_items(message: Any) -> list[Any]:
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


async def _run_outbound_call(
    call_id: str,
    phone: str,
    name: str,
    agent_profile: dict[str, Any] | None = None,
) -> None:
    state = _CALLS[call_id]
    room_name = state["roomName"]

    try:
        state["status"] = "preparing"
        logger.info("call %s preparing", call_id)

        async with api.LiveKitAPI() as lkapi:
            trunk = await _find_lumin_trunk(lkapi)
            trunk_id = getattr(trunk, "sip_trunk_id", "") or getattr(trunk, "id", "")
            if not trunk_id:
                raise RuntimeError("LiveKit returned the outbound trunk without an ID")

            state["trunkId"] = trunk_id
            state["status"] = "ringing"
            logger.info("call %s ringing via trunk %s", call_id, trunk_id)

            common_metadata = {
                "mode": "outbound",
                "callId": call_id,
                "phone": phone,
                "name": name,
            }
            if agent_profile:
                common_metadata["agentProfile"] = agent_profile

            req_kwargs: dict[str, Any] = {
                "sip_trunk_id": trunk_id,
                "sip_call_to": phone,
                "room_name": room_name,
                "participant_identity": f"callee-{call_id}",
                "participant_name": name,
                "participant_metadata": json.dumps(common_metadata),
                "wait_until_answered": True,
            }

            if LUMIN_CALLER_ID:
                req_kwargs["sip_number"] = LUMIN_CALLER_ID

            sip_participant = await lkapi.sip.create_sip_participant(
                CreateSIPParticipantRequest(**req_kwargs),
                timeout=65,
            )

            state["status"] = "answered"
            logger.info("call %s answered", call_id)
            state["sipParticipantId"] = (
                getattr(sip_participant, "participant_id", "")
                or getattr(sip_participant, "participant_identity", "")
            )

            dispatch_metadata = {
                **common_metadata,
                "source": "lumin-call-api",
            }

            dispatch = await lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=LUMIN_AGENT_NAME,
                    room=room_name,
                    metadata=json.dumps(dispatch_metadata),
                )
            )

            state["dispatchId"] = getattr(dispatch, "id", "")
            state["status"] = "connected"
            logger.info("call %s connected", call_id)

    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)[:500]
        logger.exception("call %s failed: %s", call_id, exc)


def _queue_call(phone: str, name: str, agent_profile: dict[str, Any] | None = None):
    global _LAST_CALL_AT

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
        "name": name,
        "roomName": room_name,
        "createdAt": time.time(),
        "agent": agent_profile or {},
    }
    asyncio.create_task(_run_outbound_call(call_id, phone, name, agent_profile))
    return _CALLS[call_id]


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "lumin-voice-gateway",
        "outboundConfigured": bool(LUMIN_CALL_API_KEY),
        "platformConfigured": bool(
            LUMIN_PLATFORM_PASSWORD_HASH and LUMIN_PLATFORM_SESSION_SECRET
        ),
    }


@app.post("/token")
async def create_token():
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="LiveKit is not configured")

    room_name = f"lumin-web-{uuid.uuid4().hex[:12]}"
    identity = f"visitor-{uuid.uuid4().hex[:10]}"

    metadata = json.dumps({"mode": "web", "source": "luminai.pt"})

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

    return {"serverUrl": LIVEKIT_URL, "token": token, "roomName": room_name}


@app.post("/api/call")
async def start_outbound_call(
    payload: OutboundCallRequest,
    x_lumin_key: str | None = Header(default=None, alias="X-Lumin-Key"),
):
    _require_call_key(x_lumin_key)
    phone = _normalise_phone(payload.phone)
    state = _queue_call(phone, payload.name)
    return {
        "ok": True,
        "callId": state["callId"],
        "status": state["status"],
        "roomName": state["roomName"],
    }


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


@app.post("/api/platform/login")
async def platform_login(payload: PlatformLoginRequest):
    if not _password_ok(payload.code):
        raise HTTPException(status_code=401, detail="Código de acesso inválido")
    return {
        "ok": True,
        "token": _create_platform_token(),
        "expiresIn": LUMIN_PLATFORM_SESSION_TTL,
    }


@app.post("/api/platform/call")
async def platform_call(
    payload: PlatformCallRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _require_platform_session(authorization)
    phone = _normalise_phone(payload.phone)
    profile = payload.agent.model_dump()
    state = _queue_call(phone, payload.name, profile)
    return {
        "ok": True,
        "callId": state["callId"],
        "status": state["status"],
        "roomName": state["roomName"],
    }


@app.get("/api/platform/call/{call_id}")
async def platform_call_status(
    call_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _require_platform_session(authorization)
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
<body><main><h1>LUMIN AI</h1><p class="muted">Voice Gateway online</p><p class="ok">● operacional</p><p class="muted">WebRTC + outbound SIP gateway + Voice Studio</p></main></body>
</html>"""
