from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import wave
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from livekit import api
from livekit.agents import inference, utils
from livekit.protocol.sip import CreateSIPParticipantRequest, ListSIPOutboundTrunkRequest
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from twilio.request_validator import RequestValidator

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
LUMIN_AVATAR_URL = os.getenv("LUMIN_AVATAR_URL", "http://lumin-avatar.railway.internal:8080").rstrip("/")
LUMIN_OPENVOICE_URL = os.getenv("LUMIN_OPENVOICE_URL", "http://lumin-openvoice.railway.internal:8080").rstrip("/")
LUMIN_KOKORO_URL = os.getenv("LUMIN_KOKORO_URL", "http://lumin-kokoro-v2.railway.internal:8080").rstrip("/")
LUMIN_CHATTERBOX_URL = os.getenv("LUMIN_CHATTERBOX_URL", "http://lumin-chatterbox-v2.railway.internal:8080").rstrip("/")
LUMIN_PIPER_CATALOG_URL = os.getenv("LUMIN_PIPER_CATALOG_URL", "http://lumin-piper-catalog.railway.internal:8080").rstrip("/")

SD_DIALER_SUPABASE_URL = os.getenv("SD_DIALER_SUPABASE_URL", "").rstrip("/")
SD_DIALER_SUPABASE_ANON_KEY = os.getenv("SD_DIALER_SUPABASE_ANON_KEY", "")
SD_DIALER_SUPABASE_SERVICE_ROLE_KEY = os.getenv("SD_DIALER_SUPABASE_SERVICE_ROLE_KEY", "")
SD_DIALER_SUPABASE_REQUEST_TIMEOUT_SECONDS = 12
SD_DIALER_TEMPORARY_ERROR = "O serviço de dados do SD Dialer está temporariamente lento. Aguarda alguns segundos e tenta novamente."
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_API_KEY_SID = os.getenv("TWILIO_API_KEY_SID", "")
TWILIO_API_KEY_SECRET = os.getenv("TWILIO_API_KEY_SECRET", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
LUMIN_GATEWAY_PUBLIC_URL = (
    os.getenv("LUMIN_GATEWAY_PUBLIC_URL")
    or (f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else "")
).rstrip("/")

logger = logging.getLogger("lumin-voice-gateway")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="LUMIN Voice Gateway")
# Realtime browser agent-profile dispatch enabled

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://luminai.pt",
        "https://www.luminai.pt",
        "https://sd-dialer-2p.vercel.app",
        "https://imaginative-flan-e3a8a5.netlify.app",
        "https://sd-dialer-2p.netlify.app",
        "https://main--imaginative-flan-e3a8a5.netlify.app",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

_CALLS: dict[str, dict[str, Any]] = {}
_CALL_MONITORS: set[asyncio.Task[Any]] = set()
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
    description: str = Field(default="", max_length=900)
    objective: str = Field(default="Conversar e ajudar", max_length=500)
    product: str = Field(default="", max_length=1200)
    offer: str = Field(default="", max_length=1200)
    opening: str = Field(default="", max_length=800)
    objections: str = Field(default="", max_length=1800)
    notes: str = Field(default="", max_length=2200)
    tone: str = Field(default="Natural, profissional e direto", max_length=300)
    voice: str = Field(default="natural", max_length=120)
    voiceEngine: str = Field(default="piper", max_length=40)
    voiceSource: str = Field(default="system", max_length=40)
    voiceAssetPath: str = Field(default="", max_length=700)
    voiceSampleUrl: str = Field(default="", max_length=5000)
    avatarPath: str = Field(default="", max_length=700)


class PlatformCallRequest(BaseModel):
    phone: str = Field(..., description="Destination in E.164")
    name: str = Field(default="Cliente", max_length=100)
    agent: AgentProfile = Field(default_factory=AgentProfile)


class WebTokenRequest(BaseModel):
    agent: AgentProfile | None = None


class VoicePreviewRequest(BaseModel):
    voice: str = Field(default="natural", max_length=120)
    text: str = Field(default="Olá. Sou o teu agente Lumin.", min_length=1, max_length=350)
    voiceSampleUrl: str = Field(default="", max_length=5000)


class AvatarRenderRequest(BaseModel):
    imageUrl: str = Field(..., min_length=12, max_length=6000)
    audioUrl: str = Field(..., min_length=12, max_length=6000)
    preprocess: str = Field(default="crop", max_length=20)
    still: bool = True
    size: int = Field(default=512, ge=256, le=512)


class SDDialerCallRequest(BaseModel):
    lead_id: str | None = Field(default=None, min_length=8, max_length=80)
    phone: str | None = Field(default=None, min_length=3, max_length=40)
    name: str | None = Field(default=None, max_length=120)
    consent_confirmed: bool = False
    agent_id: str = Field(default="lumin-telecom", max_length=80)
    agent: AgentProfile | None = None


class SDDialerCallerIdRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=40)


class SDDialerVoicePreviewRequest(BaseModel):
    voice: str = Field(default="natural", min_length=3, max_length=20)
    voiceEngine: str = Field(default="xai", min_length=3, max_length=20)
    text: str = Field(..., min_length=1, max_length=800)


def _apply_sd_agent_overrides(profile: dict[str, Any], overrides: AgentProfile) -> dict[str, Any]:
    values = overrides.model_dump(exclude_unset=True)
    for field in ("name", "company", "objective", "product", "opening"):
        if field in values:
            profile[field] = str(values[field] or "").strip()

    voice = values.get("voice")
    if voice is not None:
        if voice not in {"natural", "clear", "commercial"}:
            raise HTTPException(status_code=400, detail="Estilo de voz inválido")
        tones = {
            "natural": "Natural, profissional e direto.",
            "clear": "Clara, calma e objetiva.",
            "commercial": "Comercial, enérgica e cordial.",
        }
        profile["voice"] = voice
        profile["tone"] = f"[VOICE:{voice}] {tones[voice]}"

    voice_engine = values.get("voiceEngine")
    if voice_engine is not None:
        if voice_engine not in {"piper", "xai"}:
            raise HTTPException(status_code=400, detail="Tecnologia de voz inválida")
        profile["voiceEngine"] = voice_engine

    return profile


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


def _normalise_sd_phone(phone: str) -> str:
    raw = re.sub(r"[^0-9+]", "", str(phone or ""))
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    elif raw.startswith("351") and not raw.startswith("+"):
        raw = "+" + raw
    elif raw.isdigit() and len(raw) == 9:
        raw = "+351" + raw
    return _normalise_phone(raw)


def _sd_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sessao do SD Dialer em falta")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sessao do SD Dialer invalida")
    return token


def _sd_jwt_subject(token: str) -> str:
    """Read the subject locally; PostgREST verifies the JWT signature on profile lookup."""
    try:
        parts = token.split(".")
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError("Malformed JWT")
        encoded_payload = parts[1]
        payload = base64.urlsafe_b64decode(
            encoded_payload + "=" * (-len(encoded_payload) % 4)
        )
        claims = json.loads(payload.decode("utf-8"))
        user_id = claims.get("sub")
        expires_at = claims.get("exp")
        if (
            not isinstance(user_id, str)
            or not user_id
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or expires_at <= time.time()
        ):
            raise ValueError("Missing or expired JWT claims")
        return user_id
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, base64.binascii.Error):
        raise HTTPException(status_code=401, detail="Sessao do SD Dialer invalida")


async def _sd_request_with_retry(
    request_fn: Any,
    *,
    method: str,
    path: str,
    retry_safe: bool | None = None,
) -> Any:
    request_method = method.upper()
    safe_to_retry = (
        request_method in {"GET", "HEAD"}
        if retry_safe is None
        else retry_safe
    )
    max_attempts = 2 if safe_to_retry else 1

    for attempt in range(max_attempts):
        try:
            return await asyncio.to_thread(request_fn)
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 503, 504} or attempt + 1 >= max_attempts:
                raise
            logger.warning(
                "Retrying SD Dialer Supabase %s %s after HTTP %s",
                request_method,
                path,
                exc.code,
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt + 1 >= max_attempts:
                raise
            logger.warning(
                "Retrying SD Dialer Supabase %s %s after %s",
                request_method,
                path,
                type(exc).__name__,
            )

        # One short backoff avoids immediately adding load during a transient
        # Supabase timeout while still keeping the dialer responsive.
        await asyncio.sleep(0.5)

    raise RuntimeError("Supabase retry loop exited unexpectedly")


async def _sd_request_json(
    path: str,
    token: str,
    params: dict[str, str] | None = None,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    prefer: str | None = None,
    retry_safe: bool | None = None,
) -> Any:
    if not (SD_DIALER_SUPABASE_URL and SD_DIALER_SUPABASE_ANON_KEY):
        raise HTTPException(status_code=503, detail="Integracao SD Dialer nao configurada")

    query = urllib.parse.urlencode(params or {})
    url = f"{SD_DIALER_SUPABASE_URL}{path}"
    if query:
        url += "?" + query

    request_method = method.upper()
    headers = {
        "apikey": SD_DIALER_SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    request_body = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        request_body = json.dumps(body).encode("utf-8")
    if prefer:
        headers["Prefer"] = prefer

    def do_request():
        req = urllib.request.Request(
            url,
            data=request_body,
            headers=headers,
            method=request_method,
        )
        with urllib.request.urlopen(
            req,
            timeout=SD_DIALER_SUPABASE_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None

    try:
        return await _sd_request_with_retry(
            do_request,
            method=request_method,
            path=path,
            retry_safe=retry_safe,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(status_code=401, detail="Sessao do SD Dialer invalida")
        if exc.code in (408, 429, 503, 504):
            logger.warning(
                "SD Dialer Supabase returned HTTP %s for %s %s",
                exc.code,
                request_method,
                path,
            )
            raise HTTPException(status_code=503, detail=SD_DIALER_TEMPORARY_ERROR)
        logger.warning(
            "SD Dialer Supabase request failed: %s %s %s",
            request_method,
            path,
            exc.code,
        )
        raise HTTPException(status_code=502, detail="Falha ao validar dados do SD Dialer")
    except HTTPException:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        logger.warning(
            "SD Dialer Supabase network error: %s %s (%s)",
            request_method,
            path,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail=SD_DIALER_TEMPORARY_ERROR)
    except Exception as exc:
        logger.warning(
            "SD Dialer Supabase request error: %s %s (%s)",
            request_method,
            path,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Falha ao validar dados do SD Dialer")


async def _sd_admin_request_json(
    path: str,
    params: dict[str, str] | None = None,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    prefer: str | None = None,
    retry_safe: bool | None = None,
) -> Any:
    if not (SD_DIALER_SUPABASE_URL and SD_DIALER_SUPABASE_SERVICE_ROLE_KEY):
        raise HTTPException(status_code=503, detail="Armazenamento seguro de caller ID não configurado")

    query = urllib.parse.urlencode(params or {})
    url = f"{SD_DIALER_SUPABASE_URL}{path}"
    if query:
        url += "?" + query

    request_method = method.upper()
    headers = {
        "apikey": SD_DIALER_SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SD_DIALER_SUPABASE_SERVICE_ROLE_KEY}",
        "Accept": "application/json",
    }
    request_body = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        request_body = json.dumps(body).encode("utf-8")
    if prefer:
        headers["Prefer"] = prefer

    def do_request():
        http_request = urllib.request.Request(
            url,
            data=request_body,
            headers=headers,
            method=request_method,
        )
        with urllib.request.urlopen(
            http_request,
            timeout=SD_DIALER_SUPABASE_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None

    try:
        return await _sd_request_with_retry(
            do_request,
            method=request_method,
            path=path,
            retry_safe=retry_safe,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise HTTPException(
                status_code=409,
                detail="Existe uma verificação em curso ou este número já está associado a outra conta.",
            )
        if exc.code in (408, 429, 503, 504):
            logger.warning(
                "SD Dialer caller-ID storage returned HTTP %s for %s %s",
                exc.code,
                request_method,
                path,
            )
            raise HTTPException(status_code=503, detail=SD_DIALER_TEMPORARY_ERROR)
        logger.warning(
            "SD Dialer caller-ID storage failed: %s %s %s",
            request_method,
            path,
            exc.code,
        )
        raise HTTPException(status_code=502, detail="Falha ao guardar a configuração do número")
    except HTTPException:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        logger.warning(
            "SD Dialer caller-ID storage network error: %s %s (%s)",
            request_method,
            path,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail=SD_DIALER_TEMPORARY_ERROR)
    except Exception as exc:
        logger.warning(
            "SD Dialer caller-ID storage request failed: %s %s (%s)",
            request_method,
            path,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Falha ao guardar a configuração do número")


def _twilio_api_request(path: str, form: dict[str, str]) -> dict[str, Any]:
    if not (
        TWILIO_ACCOUNT_SID
        and TWILIO_API_KEY_SID
        and TWILIO_API_KEY_SECRET
    ):
        raise HTTPException(status_code=503, detail="Integração de Caller ID da Twilio não configurada")

    url = f"https://api.twilio.com{path}"
    request_body = urllib.parse.urlencode(form).encode("utf-8")
    credentials = base64.b64encode(
        f"{TWILIO_API_KEY_SID}:{TWILIO_API_KEY_SECRET}".encode("utf-8")
    ).decode("ascii")
    http_request = urllib.request.Request(
        url,
        data=request_body,
        method="POST",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(http_request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        # Avoid logging Twilio's response body: it may contain the submitted phone number.
        logger.warning("Twilio caller-ID verification failed with HTTP %s", exc.code)
        raise HTTPException(
            status_code=502,
            detail="A Twilio não conseguiu iniciar a verificação. Confirma o número e tenta novamente.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Twilio caller-ID API request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Não foi possível contactar a Twilio para verificar o número.",
        )



def _twilio_api_delete(path: str) -> None:
    if not (TWILIO_ACCOUNT_SID and TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET):
        raise HTTPException(status_code=503, detail="Integração de Caller ID da Twilio não configurada")

    url = f"https://api.twilio.com{path}"
    credentials = base64.b64encode(
        f"{TWILIO_API_KEY_SID}:{TWILIO_API_KEY_SECRET}".encode("utf-8")
    ).decode("ascii")
    http_request = urllib.request.Request(
        url,
        method="DELETE",
        headers={
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(http_request, timeout=20) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        # A missing SID is already in the requested final state.
        if exc.code == 404:
            return
        # Do not log Twilio's response body; it can contain account data.
        logger.warning("Twilio caller-ID removal failed with HTTP %s", exc.code)
        raise HTTPException(
            status_code=502,
            detail="A Twilio recusou a remoção. Confirma que a API key tem permissão Delete para Voice > Outgoing Caller IDs.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Twilio caller-ID removal request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Não foi possível contactar a Twilio para remover o número.",
        )


def _sd_caller_id_verification_configured() -> bool:
    return bool(
        SD_DIALER_SUPABASE_URL
        and SD_DIALER_SUPABASE_SERVICE_ROLE_KEY
        and TWILIO_ACCOUNT_SID
        and TWILIO_API_KEY_SID
        and TWILIO_API_KEY_SECRET
        and TWILIO_AUTH_TOKEN
        and LUMIN_GATEWAY_PUBLIC_URL.startswith("https://")
    )


def _caller_id_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "phoneNumber": row.get("phone_number"),
        "status": row.get("verification_status"),
        "isActive": bool(row.get("is_active")),
        "requestedAt": row.get("requested_at"),
        "verifiedAt": row.get("verified_at"),
        "validationCode": (
            row.get("validation_code")
            if row.get("verification_status") == "pending"
            else None
        ),
    }


async def _sd_caller_id_rows(
    user_id: str,
    company_id: str,
    *,
    verification_status: str | None = None,
    is_active: bool | None = None,
    limit: int = 1,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params = {
        "user_id": f"eq.{user_id}",
        "company_id": f"eq.{company_id}",
        "select": "id,phone_number,verification_status,is_active,validation_code,requested_at,verified_at,twilio_outgoing_caller_id_sid",
        "order": "requested_at.desc,id.desc",
        "limit": str(limit),
    }
    if verification_status:
        params["verification_status"] = f"eq.{verification_status}"
    if is_active is not None:
        params["is_active"] = f"eq.{str(is_active).lower()}"
    if offset:
        params["offset"] = str(offset)

    rows = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        params,
    )
    return rows if isinstance(rows, list) else []


async def _sd_verified_caller_id_rows(user_id: str, company_id: str) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    page_size = 100
    offset = 0
    while True:
        rows = await _sd_caller_id_rows(
            user_id,
            company_id,
            verification_status="verified",
            limit=page_size,
            offset=offset,
        )
        verified.extend(rows)
        if len(rows) < page_size:
            return verified
        offset += len(rows)


async def _sd_caller_id_status(user_id: str, company_id: str) -> dict[str, Any]:
    if not (SD_DIALER_SUPABASE_URL and SD_DIALER_SUPABASE_SERVICE_ROLE_KEY):
        return {
            "active": None,
            "pending": None,
            "failed": None,
            "numbers": [],
            "verificationAvailable": False,
            "fallbackAvailable": bool(LUMIN_CALLER_ID),
            "fallbackPhoneNumber": LUMIN_CALLER_ID or None,
        }

    pending_rows, failed_rows, verified_rows = await asyncio.gather(
        _sd_caller_id_rows(
            user_id, company_id, verification_status="pending"
        ),
        _sd_caller_id_rows(
            user_id, company_id, verification_status="failed"
        ),
        _sd_verified_caller_id_rows(user_id, company_id),
    )
    active = next((row for row in verified_rows if row.get("is_active")), None)
    pending = pending_rows[0] if pending_rows else None
    failed = failed_rows[0] if failed_rows else None
    active_requested_at = str((active or {}).get("requested_at") or "")
    if (
        failed
        and active_requested_at
        and str(failed.get("requested_at") or "") <= active_requested_at
    ):
        failed = None

    return {
        "active": _caller_id_summary(active),
        "pending": _caller_id_summary(pending),
        "failed": _caller_id_summary(failed),
        "numbers": [_caller_id_summary(row) for row in verified_rows],
        "verificationAvailable": _sd_caller_id_verification_configured(),
        "fallbackAvailable": bool(LUMIN_CALLER_ID),
        "fallbackPhoneNumber": LUMIN_CALLER_ID or None,
    }

def _caller_id_verification_is_stale(requested_at: str | None) -> bool:
    try:
        value = datetime.fromisoformat(str(requested_at or "").replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - value).total_seconds() > 10 * 60
    except (TypeError, ValueError):
        return True


async def _require_sd_context(authorization: str | None) -> tuple[str, dict[str, Any], str]:
    token = _sd_bearer_token(authorization)
    # The protected PostgREST request below verifies this JWT's signature and expiry.
    # Decode only its subject here to avoid a separate, flaky Auth /user round-trip.
    user_id = _sd_jwt_subject(token)

    rows = await _sd_request_json(
        "/rest/v1/usuarios",
        token,
        {
            "id": f"eq.{user_id}",
            "select": "id,role,company_id,full_name",
            "limit": "1",
        },
    )
    profile = rows[0] if isinstance(rows, list) and rows else None
    if not profile or not profile.get("company_id"):
        raise HTTPException(status_code=403, detail="Perfil do SD Dialer sem empresa")
    if profile.get("role") not in {"parceiro", "admin", "supervisor", "super_admin"}:
        raise HTTPException(status_code=403, detail="Perfil sem permissao para chamadas IA")
    return user_id, profile, token


def _sd_agent_profile(agent_id: str, company_name: str) -> dict[str, Any]:
    company = company_name or "SD Dialer"

    if agent_id == "lumin-energia":
        return {
            "id": "lumin-energia",
            "name": "Lumin Energia",
            "company": company,
            "objective": "Qualificar o interesse do contacto em rever o contrato de energia e confirmar se quer receber uma comparacao ou contacto humano.",
            "product": "Consultoria e comparacao de energia. Perceber fornecedor atual, satisfacao, valor aproximado e ultima revisao do contrato. Nao prometer poupanca sem analisar dados.",
            "offer": "Comparar as condicoes atuais sem obrigacao de mudanca. Nao inventar precos, descontos, comercializadores ou garantias.",
            "opening": "Estou a ligar para perceber se faria sentido rever as condicoes atuais da sua energia. Posso fazer-lhe duas perguntas rapidas?",
            "objections": "Se estiver satisfeito, reconhecer e perguntar quando reviu o contrato pela ultima vez. Se nao tiver tempo, ser breve e aceitar outro momento. Se nao quiser ser contactado, terminar imediatamente.",
            "notes": "Faz uma pergunta de cada vez. Se existir interesse, confirma que quer receber uma comparacao ou ser contactado por uma pessoa da equipa. Nunca digas que transferiste ou agendaste se isso nao aconteceu.",
            "tone": "[VOICE:commercial] Humano, claro, comercial e respeitador.",
        }

    if agent_id == "lumin-ai":
        return {
            "id": "lumin-comercial",
            "name": "Lumin Comercial",
            "company": "LUMIN AI",
            "objective": "Descobrir uma necessidade real do negocio, apresentar a solucao LUMIN AI relevante e confirmar interesse numa demonstracao, proposta ou contacto humano.",
            "product": "LUMIN AI: assistentes de voz, atendimento, qualificacao de contactos, automacao comercial, sites, aplicacoes, conteudos e fluxos de trabalho.",
            "offer": "Nao inventar precos, descontos ou resultados. A proposta comercial e confirmada pela equipa humana.",
            "opening": "Queria fazer-lhe uma pergunta rapida sobre o seu negocio. Onde e que hoje perde mais tempo com clientes ou tarefas repetitivas?",
            "objections": "Se nao tiver tempo, pedir apenas vinte segundos. Se ja usar IA, perguntar o que continua manual. Se disser que e caro, perceber primeiro a necessidade antes de falar de proposta.",
            "notes": "Venda consultiva: descobrir problema, impacto, apresentar apenas a solucao relevante, testar interesse e fechar o proximo passo. Sem urgencia falsa ou pressao enganosa.",
            "tone": "[VOICE:commercial] Humano, confiante, comercial e proximo.",
        }

    if agent_id != "lumin-telecom":
        raise HTTPException(status_code=400, detail="Agente IA desconhecido")

    return {
        "id": "lumin-telecom",
        "scriptVersion": 8,
        "name": "Lumin",
        "voice": "natural",
        "voiceEngine": "xai",
        "company": "Compara+",
        "description": "Agente virtual da Compara+ para comparar pacotes de telecomunicações residenciais: internet, televisão, telefone fixo e telemóveis.",
        "objective": "Perceber como está composto o pacote de telecomunicações e o que a pessoa quer melhorar; apresentar uma oferta autorizada e conduzir à adesão quando possível ou marcar um contacto com consultor.",
        "product": "A Compara+ ajuda a comparar internet fixa ou fibra, televisão, telefone fixo e tarifários ou linhas móveis, em separado ou em pacotes integrados. A comparação pode considerar os serviços e o custo do pacote em conjunto. O consultor confirma cobertura, preços e condições quando não estejam definidos no perfil. Nunca inventes dados.",
        "offer": "Apresenta os preços, campanhas e condições configurados no perfil central do agente, quando existirem. Se não houver oferta concreta ou a cobertura não estiver validada, não inventes: explica que um consultor confirma os dados. Quando a oferta corresponder à necessidade, faz uma pergunta direta para avançar.",
        "opening": "Boa tarde, [nome]. Sou o Lumin, agente virtual da Compara+. Ajudamos a comparar internet, televisão, telefone fixo e tarifários móveis, em separado ou em pacote. O que gostava de melhorar no que tem atualmente?",
        "objections": "CUSTO: se o preço for importante e ainda desconhecido, pergunta apenas o valor aproximado do pacote completo e relaciona-o com a oferta configurada. INTERNET, TV OU MÓVEL: responde sobre o serviço referido. DÚVIDAS: esclarece o ponto concreto antes de avançar. SATISFEITO OU RECUSA: oferece uma comparação sem compromisso no máximo uma vez; perante recusa clara, agradece e termina sem insistir. PEDIDO PARA NÃO VOLTAR A CONTACTAR: respeita e termina.",
        "notes": """VENDA CONSULTIVA DE TELECOMUNICAÇÕES
1) Usa o nome e os dados do contexto desta chamada, incluindo o campo manual. Nunca procures dados noutras leads nem perguntes o nome.
2) O âmbito é o pacote completo: internet fixa/fibra, televisão, telefone fixo, tarifários móveis e quantidade de linhas móveis. Não reduzas a conversa à internet.
3) Aproveita tudo o que já estiver no contexto ou que a pessoa já tenha dito. Não repitas perguntas. Pergunta pela operadora apenas se ainda for desconhecida e necessária para comparar.
4) Qualifica naturalmente, com no máximo três perguntas relevantes e uma de cada vez: que serviços estão incluídos; o que quer melhorar primeiro (custo, internet, TV ou telemóveis); e, se o custo for a prioridade, quanto paga aproximadamente pelo pacote. Pergunta quantas linhas móveis só se isso for relevante. Não faças um questionário.
5) Não perguntes sobre fidelização, titularidade, autorização para guardar o contacto, gravação ou transcrição. Não peças email, NIF ou morada nesta chamada. As leads já estão consentidas.
6) Entre perguntas, dá informação útil: explica que a comparação pode analisar o pacote completo e quais serviços podem ser considerados. Apresenta apenas ofertas, preços e condições existentes no perfil central; nunca inventes poupanças, cobertura, campanhas ou disponibilidade.
7) Quando identificares uma necessidade, resume-a numa frase natural e liga-a à opção configurada. Não fiques só a recolher respostas. Se a oferta concreta servir, pergunta diretamente se quer avançar para a adesão. Se for necessária validação ou formalização por uma pessoa, propõe logo um contacto com consultor para tratar disso.
8) Se aceitar o contacto de um consultor, pergunta o melhor dia e hora. Se indicar ambos de uma vez, chama schedule_follow_up uma única vez com data/hora ISO 8601 no fuso Europe/Lisbon. Se faltar apenas a hora, pergunta só a hora. Confirma a marcação apenas se a ferramenta devolver sucesso. Nunca digas que a adesão foi concluída se não foi.
9) Se houver uma objeção hesitante, responde ao ponto e faz uma única tentativa clara de avançar. Se a pessoa recusar claramente, agradece e termina. Não insistas.
10) Fala com naturalidade, em uma ou duas frases úteis de cada vez. Uma pergunta por turno. Não sejas telegráfico, não repitas confirmações e não recites este guião."""
    }


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
    caller_id: str | None = None,
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
                "source": state.get("source", "lumin-call-api"),
                "leadId": state.get("leadId"),
                "companyId": state.get("companyId"),
                "requestedBy": state.get("sdUserId"),
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

            selected_caller_id = caller_id or LUMIN_CALLER_ID
            if selected_caller_id:
                req_kwargs["sip_number"] = selected_caller_id

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
                "source": state.get("source", "lumin-call-api"),
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
            state["connectedAt"] = time.time()
            logger.info("call %s connected", call_id)
            monitor = asyncio.create_task(_monitor_call_end(call_id, room_name))
            _CALL_MONITORS.add(monitor)
            monitor.add_done_callback(_CALL_MONITORS.discard)

    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)[:500]
        state["endedAt"] = time.time()
        state["durationSeconds"] = 0
        logger.exception("call %s failed: %s", call_id, exc)


async def _monitor_call_end(call_id: str, room_name: str) -> None:
    callee_identity = f"callee-{call_id}"
    missing_checks = 0

    while True:
        await asyncio.sleep(3)
        state = _CALLS.get(call_id)
        if not state or state.get("status") != "connected":
            return

        try:
            async with api.LiveKitAPI() as lkapi:
                response = await lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
            participants = getattr(response, "participants", []) or []
            callee_present = any(
                getattr(participant, "identity", "") == callee_identity
                for participant in participants
            )
        except Exception as exc:
            logger.warning("call %s end check failed: %s", call_id, str(exc)[:200])
            continue

        if callee_present:
            missing_checks = 0
            continue

        missing_checks += 1
        if missing_checks < 2:
            continue

        ended_at = time.time()
        connected_at = float(state.get("connectedAt") or ended_at)
        state["status"] = "ended"
        state["endedAt"] = ended_at
        state["durationSeconds"] = max(0, int(ended_at - connected_at))
        state["endReason"] = "callee_disconnected"
        logger.info("call %s ended after callee disconnected", call_id)
        return


def _queue_call(
    phone: str,
    name: str,
    agent_profile: dict[str, Any] | None = None,
    source: str = "lumin-call-api",
    call_context: dict[str, Any] | None = None,
    caller_id: str | None = None,
):
    global _LAST_CALL_AT

    now = time.monotonic()
    if now - _LAST_CALL_AT < 1.2:
        raise HTTPException(status_code=429, detail="Wait a moment before starting another call")
    _LAST_CALL_AT = now

    call_id = str(uuid.uuid4())
    room_name = f"lumin-call-{call_id}"
    _CALLS[call_id] = {
        "callId": call_id,
        "status": "queued",
        "phone": phone,
        "name": name,
        "roomName": room_name,
        "createdAt": time.time(),
        "agent": agent_profile or {},
        "source": source,
        **(call_context or {}),
    }
    asyncio.create_task(_run_outbound_call(call_id, phone, name, agent_profile, caller_id))
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
        "sdDialerConfigured": bool(
            SD_DIALER_SUPABASE_URL and SD_DIALER_SUPABASE_ANON_KEY
        ),
        "sdDialerCallerIdConfigured": _sd_caller_id_verification_configured(),
    }


@app.post("/token")
async def create_token(payload: WebTokenRequest | None = None):
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="LiveKit is not configured")

    room_name = f"lumin-web-{uuid.uuid4().hex[:12]}"
    identity = f"visitor-{uuid.uuid4().hex[:10]}"

    dispatch_metadata: dict[str, Any] = {"mode": "web", "source": "luminai.pt"}
    if payload and payload.agent:
        dispatch_metadata["agentProfile"] = payload.agent.model_dump()
    metadata = json.dumps(dispatch_metadata)

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
        "agent": dispatch_metadata.get("agentProfile", {}),
    }


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


async def _sd_active_vodafone_script(token: str, company_id: str) -> dict[str, Any] | None:
    rows = await _sd_request_json(
        "/rest/v1/roteiros_venda",
        token,
        {
            "company_id": f"eq.{company_id}",
            "segmento": "eq.VODAFONE",
            "ativo": "eq.true",
            "select": "id,titulo,conteudo,ordem",
            "order": "ordem.asc,updated_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if isinstance(rows, list) and rows else None


def _apply_sd_vodafone_script(
    profile: dict[str, Any],
    script: dict[str, Any],
) -> dict[str, Any]:
    content = str(script.get("conteudo") or "").strip()
    opening_match = re.search(
        r"(?is)Abertura\s*:\s*(.+?)(?=\n\s*1\.\s*ª\s*Pergunta\b)",
        content,
    )
    if not opening_match:
        logger.warning("Active Vodafone script has no parseable opening")
        return profile

    opening = opening_match.group(1).strip().strip('"“” ')
    first_question_match = re.search(
        r'(?im)^\s*1\.\s*ª\s*Pergunta[^\n]*\n\s*["“]?([^"”\n]+)',
        content,
    )
    if first_question_match:
        first_question = first_question_match.group(1).strip().strip('"“” ')
        if first_question and first_question.casefold() not in opening.casefold():
            opening = f"{opening.rstrip()} {first_question}".strip()

    # The caller name always comes from the selected/manual lead context.
    opening = re.sub(
        r"\bRicardo(?:\s+José)?(?:\s+Santos)?\b",
        "[nome]",
        opening,
        flags=re.IGNORECASE,
    )
    if "[nome]" not in opening.casefold():
        opening = re.sub(
            r"^(Boa tarde)(?:,?\s*)",
            r"\1, estou a falar com o Sr. [nome]? ",
            opening,
            count=1,
            flags=re.IGNORECASE,
        )

    script_title = str(script.get("titulo") or "Vodafone").strip()[:120]
    profile.update({
        "scriptVersion": 10,
        "campaignSegment": "VODAFONE",
        "description": "Agente de voz da Compara+ a testar o guião Vodafone.",
        "objective": "Seguir o guião Vodafone ativo do Super Admin, qualificar o pacote atual e encaminhar o contacto para um especialista.",
        "offer": "Proposta Vodafone sem compromisso. Não inventes preços, cobertura, poupanças nem condições que não estejam configuradas.",
        "opening": opening[:800],
        "notes": (
            f"GUIÃO ATIVO DO SUPER ADMIN — {script_title}\n"
            f"{content}\n\n"
            "Segue as perguntas do guião pela ordem, uma de cada vez, sem repetir respostas já dadas. "
            "A pergunta sobre operadora e valor já foi feita no fim da abertura: aguarda a resposta e não a repitas. "
            "No contexto dessa pergunta, interpreta uma resposta com som semelhante a MEO, incluindo transcrições como meu, mei ou mel, como MEO; não peças confirmação só por essa variação. "
            "Usa apenas o nome da lead selecionada ou do campo manual; nunca uses o nome escrito no exemplo. "
            "Não perguntes por consentimento para gravar, transcrever ou guardar contacto. "
            "Se a pessoa aceitar o contacto de um especialista e não indicar um dia, pergunta só o dia preferido; "
            "depois usa a agenda de follow-up apenas com uma data/hora acordada e confirma só após sucesso."
        )[:2200],
    })
    return profile


@app.get("/api/sd-dialer/caller-id")
async def sd_dialer_caller_id(
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    user_id, profile, _ = await _require_sd_context(authorization)
    return await _sd_caller_id_status(user_id, str(profile["company_id"]))



@app.delete("/api/sd-dialer/caller-id/{caller_id_id}")
async def sd_dialer_delete_caller_id(
    caller_id_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    try:
        caller_id_uuid = str(uuid.UUID(caller_id_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Número associado não encontrado")

    user_id, profile, _ = await _require_sd_context(authorization)
    company_id = str(profile["company_id"])
    rows = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {
            "id": f"eq.{caller_id_uuid}",
            "user_id": f"eq.{user_id}",
            "company_id": f"eq.{company_id}",
            "verification_status": "eq.verified",
            "select": "id,phone_number,is_active,twilio_outgoing_caller_id_sid",
            "limit": "1",
        },
    )
    caller_id = rows[0] if isinstance(rows, list) and rows else None
    if not caller_id:
        raise HTTPException(status_code=404, detail="Número associado não encontrado")

    twilio_sid = str(caller_id.get("twilio_outgoing_caller_id_sid") or "").strip()
    fallback_phone = ""
    if LUMIN_CALLER_ID:
        try:
            fallback_phone = _normalise_sd_phone(LUMIN_CALLER_ID)
        except HTTPException:
            fallback_phone = ""
    preserve_twilio_caller_id = bool(
        fallback_phone
        and _normalise_sd_phone(str(caller_id.get("phone_number") or "")) == fallback_phone
    )
    if not twilio_sid and not preserve_twilio_caller_id:
        raise HTTPException(status_code=409, detail="Este número não tem um registo Twilio que possa ser removido automaticamente.")

    was_active = bool(caller_id.get("is_active"))
    if was_active:
        await _sd_admin_request_json(
            "/rest/v1/sd_dialer_caller_ids",
            {
                "id": f"eq.{caller_id_uuid}",
                "user_id": f"eq.{user_id}",
                "company_id": f"eq.{company_id}",
                "verification_status": "eq.verified",
            },
            method="PATCH",
            body={"is_active": False},
            prefer="return=minimal",
        )

    if not preserve_twilio_caller_id:
        account_path = (
            f"/2010-04-01/Accounts/{urllib.parse.quote(TWILIO_ACCOUNT_SID, safe='')}"
            f"/OutgoingCallerIds/{urllib.parse.quote(twilio_sid, safe='')}.json"
        )
        try:
            await asyncio.to_thread(_twilio_api_delete, account_path)
        except HTTPException:
            if was_active:
                try:
                    await _sd_admin_request_json(
                        "/rest/v1/sd_dialer_caller_ids",
                        {
                            "id": f"eq.{caller_id_uuid}",
                            "user_id": f"eq.{user_id}",
                            "company_id": f"eq.{company_id}",
                            "verification_status": "eq.verified",
                        },
                        method="PATCH",
                        body={"is_active": True},
                        prefer="return=minimal",
                    )
                except Exception as restore_error:
                    logger.error("Caller-ID activation restore failed: %s", type(restore_error).__name__)
            raise

    await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {
            "id": f"eq.{caller_id_uuid}",
            "user_id": f"eq.{user_id}",
            "company_id": f"eq.{company_id}",
            "verification_status": "eq.verified",
        },
        method="DELETE",
        prefer="return=minimal",
    )
    status = await _sd_caller_id_status(user_id, company_id)
    if preserve_twilio_caller_id:
        status["twilioCallerIdRetained"] = True
    return status


@app.post("/api/sd-dialer/caller-id/verify")
async def sd_dialer_verify_caller_id(
    payload: SDDialerCallerIdRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    user_id, profile, _ = await _require_sd_context(authorization)
    if not _sd_caller_id_verification_configured():
        raise HTTPException(status_code=503, detail="A verificação de Caller ID ainda não está configurada")

    phone = _normalise_sd_phone(payload.phone)
    company_id = str(profile["company_id"])

    # A verified/pending number can belong to only one SD Dialer user.
    matches = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {
            "phone_number": f"eq.{phone}",
            "verification_status": "in.(pending,verified)",
            "select": "id,user_id,company_id,phone_number,verification_status,is_active,validation_code,requested_at,verified_at,twilio_outgoing_caller_id_sid",
            "order": "requested_at.desc",
            "limit": "1",
        },
    )
    existing = matches[0] if isinstance(matches, list) and matches else None
    if existing and str(existing.get("user_id")) != user_id:
        raise HTTPException(
            status_code=409,
            detail="Este número já está associado a outro utilizador.",
        )
    if existing and existing.get("verification_status") == "verified":
        twilio_sid = str(existing.get("twilio_outgoing_caller_id_sid") or "")
        if not twilio_sid:
            raise HTTPException(status_code=409, detail="Este número precisa de ser verificado novamente.")
        await _sd_admin_request_json(
            "/rest/v1/rpc/activate_sd_dialer_caller_id",
            method="POST",
            body={"p_verification_id": existing["id"], "p_twilio_sid": twilio_sid},
        )
        return await _sd_caller_id_status(user_id, company_id)
    if existing and existing.get("verification_status") == "pending":
        if _caller_id_verification_is_stale(existing.get("requested_at")):
            await _sd_admin_request_json(
                "/rest/v1/sd_dialer_caller_ids",
                {"id": f"eq.{existing['id']}", "verification_status": "eq.pending"},
                method="PATCH",
                body={"verification_status": "failed", "validation_code": None},
                prefer="return=minimal",
            )
        else:
            return await _sd_caller_id_status(user_id, company_id)

    user_pending = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {
            "user_id": f"eq.{user_id}",
            "verification_status": "eq.pending",
            "select": "id,phone_number,requested_at",
            "limit": "1",
        },
    )
    if isinstance(user_pending, list) and user_pending:
        pending_row = user_pending[0]
        if _caller_id_verification_is_stale(pending_row.get("requested_at")):
            await _sd_admin_request_json(
                "/rest/v1/sd_dialer_caller_ids",
                {"id": f"eq.{pending_row['id']}", "verification_status": "eq.pending"},
                method="PATCH",
                body={"verification_status": "failed", "validation_code": None},
                prefer="return=minimal",
            )
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Conclui primeiro a verificação em curso para {pending_row.get('phone_number')}.",
            )

    one_hour_ago = datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat()
    recent = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {
            "user_id": f"eq.{user_id}",
            "requested_at": f"gte.{one_hour_ago}",
            "select": "id",
            "limit": "3",
        },
    )
    if isinstance(recent, list) and len(recent) >= 3:
        raise HTTPException(
            status_code=429,
            detail="Atingiste o limite de verificações. Tenta novamente dentro de uma hora.",
        )

    created = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        method="POST",
        body={
            "user_id": user_id,
            "company_id": company_id,
            "phone_number": phone,
            "verification_status": "pending",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        },
        prefer="return=representation",
    )
    verification = created[0] if isinstance(created, list) and created else None
    if not verification or not verification.get("id"):
        raise HTTPException(status_code=502, detail="Não foi possível preparar a verificação.")

    callback_url = (
        f"{LUMIN_GATEWAY_PUBLIC_URL}/api/sd-dialer/caller-id/"
        f"twilio-status/{verification['id']}"
    )
    account_path = (
        f"/2010-04-01/Accounts/{urllib.parse.quote(TWILIO_ACCOUNT_SID, safe='')}"
        "/OutgoingCallerIds.json"
    )
    try:
        twilio_result = await asyncio.to_thread(
            _twilio_api_request,
            account_path,
            {
                "PhoneNumber": phone,
                "FriendlyName": f"SD Dialer {user_id[:8]}",
                "StatusCallback": callback_url,
                "StatusCallbackMethod": "POST",
            },
        )
    except HTTPException:
        await _sd_admin_request_json(
            "/rest/v1/sd_dialer_caller_ids",
            {"id": f"eq.{verification['id']}", "verification_status": "eq.pending"},
            method="PATCH",
            body={"verification_status": "failed", "validation_code": None},
            prefer="return=minimal",
        )
        raise

    validation_code = str(
        twilio_result.get("ValidationCode") or twilio_result.get("validation_code") or ""
    ).strip()
    if not re.fullmatch(r"\d{4,8}", validation_code):
        await _sd_admin_request_json(
            "/rest/v1/sd_dialer_caller_ids",
            {"id": f"eq.{verification['id']}", "verification_status": "eq.pending"},
            method="PATCH",
            body={"verification_status": "failed", "validation_code": None},
            prefer="return=minimal",
        )
        raise HTTPException(status_code=502, detail="A Twilio não devolveu um código de verificação válido.")
    await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {
            "id": f"eq.{verification['id']}",
            "verification_status": "eq.pending",
        },
        method="PATCH",
        body={
            "validation_code": validation_code,
            "twilio_call_sid": twilio_result.get("CallSid") or twilio_result.get("call_sid"),
        },
        prefer="return=minimal",
    )

    return await _sd_caller_id_status(user_id, company_id)


@app.post("/api/sd-dialer/caller-id/twilio-status/{verification_id}")
async def sd_dialer_twilio_caller_id_status(
    verification_id: str,
    request: Request,
):
    if not (TWILIO_AUTH_TOKEN and SD_DIALER_SUPABASE_SERVICE_ROLE_KEY and LUMIN_GATEWAY_PUBLIC_URL.startswith("https://")):
        raise HTTPException(status_code=503, detail="Callback de Caller ID não configurado")
    try:
        uuid.UUID(verification_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Verificação desconhecida")

    raw_body = await request.body()
    decoded_body = raw_body.decode("utf-8", errors="replace")
    params = {
        key: values[-1] if values else ""
        for key, values in urllib.parse.parse_qs(
            decoded_body,
            keep_blank_values=True,
        ).items()
    }
    signature = request.headers.get("X-Twilio-Signature", "")
    callback_url = (
        f"{LUMIN_GATEWAY_PUBLIC_URL}/api/sd-dialer/caller-id/"
        f"twilio-status/{verification_id}"
    )
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    if not signature or not validator.validate(callback_url, params, signature):
        raise HTTPException(status_code=403, detail="Assinatura Twilio inválida")
    if params.get("AccountSid") != TWILIO_ACCOUNT_SID:
        raise HTTPException(status_code=403, detail="Conta Twilio inválida")

    rows = await _sd_admin_request_json(
        "/rest/v1/sd_dialer_caller_ids",
        {"id": f"eq.{verification_id}", "select": "id,verification_status", "limit": "1"},
    )
    if not isinstance(rows, list) or not rows:
        return Response(status_code=204)

    verification_status = str(params.get("VerificationStatus") or "").lower()
    if verification_status == "success":
        twilio_sid = str(params.get("OutgoingCallerIdSid") or "").strip()
        if twilio_sid:
            await _sd_admin_request_json(
                "/rest/v1/rpc/activate_sd_dialer_caller_id",
                method="POST",
                body={"p_verification_id": verification_id, "p_twilio_sid": twilio_sid},
            )
        else:
            await _sd_admin_request_json(
                "/rest/v1/sd_dialer_caller_ids",
                {"id": f"eq.{verification_id}", "verification_status": "eq.pending"},
                method="PATCH",
                body={"verification_status": "failed", "validation_code": None},
                prefer="return=minimal",
            )
    elif verification_status == "failed":
        await _sd_admin_request_json(
            "/rest/v1/sd_dialer_caller_ids",
            {"id": f"eq.{verification_id}", "verification_status": "eq.pending"},
            method="PATCH",
            body={"verification_status": "failed", "validation_code": None},
            prefer="return=minimal",
        )
    return Response(status_code=204)


@app.post("/api/sd-dialer/call")
async def sd_dialer_call(
    payload: SDDialerCallRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    user_id, profile, token = await _require_sd_context(authorization)
    is_manual = bool(payload.phone)
    has_lead = bool(payload.lead_id)
    if is_manual == has_lead:
        raise HTTPException(status_code=400, detail="Escolhe uma lead ou introduz um número manual")

    caller_id = LUMIN_CALLER_ID
    if SD_DIALER_SUPABASE_URL and SD_DIALER_SUPABASE_SERVICE_ROLE_KEY:
        caller_rows = await _sd_admin_request_json(
            "/rest/v1/sd_dialer_caller_ids",
            {
                "user_id": f"eq.{user_id}",
                "company_id": f"eq.{profile['company_id']}",
                "verification_status": "eq.verified",
                "is_active": "eq.true",
                "select": "phone_number",
                "limit": "1",
            },
        )
        user_caller_id = str(caller_rows[0].get("phone_number") or "") if caller_rows else ""
        if user_caller_id:
            caller_id = user_caller_id
    if not caller_id:
        raise HTTPException(
            status_code=409,
            detail="Não existe um número de saída configurado. Verifica um número pessoal ou pede ao administrador para configurar o número da empresa.",
        )

    lead = None
    lead_id: str | None = None
    lead_name = ""

    if has_lead:
        leads = await _sd_request_json(
            "/rest/v1/rpc/get_voice_lead_for_call",
            token,
            {"p_lead_id": str(payload.lead_id)},
            retry_safe=True,
        )
        lead = leads[0] if isinstance(leads, list) and leads else None
        if not lead:
            raise HTTPException(status_code=404, detail="Lead nao encontrada ou fora da empresa")
        if str(lead.get("company_id") or "") != str(profile.get("company_id") or ""):
            raise HTTPException(status_code=403, detail="Lead pertence a outra empresa")
        lead_id = str(lead.get("id") or "")
        lead_name = str(lead.get("nome") or "Cliente")
        phone = _normalise_sd_phone(str(lead.get("telefone") or ""))
    else:
        if not payload.consent_confirmed:
            raise HTTPException(status_code=400, detail="Confirma que tens autorizacao para contactar este numero")
        lead_name = str(payload.name or "").strip()
        if not lead_name:
            raise HTTPException(status_code=400, detail="Indica o nome da lead manual para a chamada e a agenda")
        phone = _normalise_sd_phone(str(payload.phone or ""))

        # Manual calls get their own manual lead, so a phone match can never
        # silently replace the name entered by the operator with another lead's name.
        created_leads = await _sd_request_json(
            "/rest/v1/leads",
            token,
            {"select": "id,company_id,nome"},
            method="POST",
            body={
                "company_id": str(profile["company_id"]),
                "assigned_to": user_id,
                "nome": lead_name,
                "telefone": phone,
                "status": "novo",
                "origem": "manual",
            },
            prefer="return=representation",
        )
        lead = created_leads[0] if isinstance(created_leads, list) and created_leads else None
        lead_id = str((lead or {}).get("id") or "")
        if not lead_id:
            raise HTTPException(status_code=502, detail="Nao foi possivel criar a lead manual")

    if lead and lead.get("status") in {
        "nao_interessado",
        "desligado",
        "numero_errado",
        "numero_nao_atribuido",
        "pertence_outra_pessoa",
    }:
        raise HTTPException(status_code=409, detail="Lead bloqueada para nova chamada")

    company_request = _sd_request_json(
        "/rest/v1/companies",
        token,
        {
            "id": f"eq.{profile['company_id']}",
            "select": "id,name",
            "limit": "1",
        },
    )
    active_script = None
    if payload.agent_id == "lumin-telecom":
        companies, active_script = await asyncio.gather(
            company_request,
            _sd_active_vodafone_script(
                token,
                str(profile.get("company_id") or ""),
            ),
        )
    else:
        companies = await company_request

    company_name = (
        str(companies[0].get("name") or "SD Dialer")
        if isinstance(companies, list) and companies
        else "SD Dialer"
    )

    agent_profile = _sd_agent_profile(payload.agent_id, company_name)
    if payload.agent is not None:
        agent_profile = _apply_sd_agent_overrides(agent_profile, payload.agent)
    if active_script:
        agent_profile = _apply_sd_vodafone_script(agent_profile, active_script)

    state = _queue_call(
        phone,
        lead_name,
        agent_profile,
        source="sd-dialer-manual" if is_manual else "sd-dialer",
        call_context={
            "sdUserId": user_id,
            "companyId": str(profile["company_id"]),
            "leadId": lead_id,
            "manualNumber": is_manual,
            "manualConsentConfirmed": is_manual,
        },
        caller_id=caller_id,
    )
    return {
        "ok": True,
        "callId": state["callId"],
        "status": state["status"],
        "roomName": state["roomName"],
        "manual": is_manual,
        "lead": {"id": lead_id, "nome": lead_name},
    }

@app.get("/api/sd-dialer/call/{call_id}")
async def sd_dialer_call_status(
    call_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    user_id, profile, _ = await _require_sd_context(authorization)
    state = _CALLS.get(call_id)
    if not state:
        raise HTTPException(status_code=404, detail="Chamada desconhecida")

    owner = state.get("sdUserId")
    if owner and owner != user_id and profile.get("role") not in {"admin", "supervisor", "super_admin"}:
        raise HTTPException(status_code=403, detail="Sem permissao para esta chamada")

    return {
        "callId": state.get("callId"),
        "status": state.get("status"),
        "roomName": state.get("roomName"),
        "leadId": state.get("leadId"),
        "createdAt": state.get("createdAt"),
        "error": state.get("error"),
    }


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


def _voice_preview_bytes(base_url: str, payload: dict[str, Any], timeout: int = 90) -> tuple[bytes, dict[str, str]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/synthesize",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            return response.read(), headers
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=detail[:1200] or "Voice preview error")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Voice engine unavailable: {exc}")



@app.post("/api/sd-dialer/voice-preview")
async def sd_dialer_voice_preview(
    payload: SDDialerVoicePreviewRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    await _require_sd_context(authorization)
    voice = payload.voice.strip().lower()
    engine = payload.voiceEngine.strip().lower()
    text = payload.text.strip()

    if voice not in {"natural", "clear", "commercial"}:
        raise HTTPException(status_code=400, detail="Estilo de voz inválido")
    if engine not in {"piper", "xai"}:
        raise HTTPException(status_code=400, detail="Tecnologia de voz inválida")

    sample_rate = 24000
    channels = 1
    media_type = "audio/pcm"

    use_livekit_xai = (
        engine == "xai"
        and os.getenv("LUMIN_TTS_PROVIDER", "opensource").strip().lower()
        in {"xai", "livekit-xai"}
    )
    if use_livekit_xai:
        voices = {"natural": "sal", "clear": "rex", "commercial": "leo"}
        tts_engine = inference.TTS(
            model="xai/tts-1",
            voice=voices[voice],
            language="pt-PT",
        )
        pcm_parts: list[bytes] = []
        try:
            async with utils.http_context.open():
                try:
                    async with tts_engine.synthesize(text) as stream:
                        async for item in stream:
                            frame = item.frame
                            sample_rate = frame.sample_rate
                            channels = frame.num_channels
                            pcm_parts.append(bytes(frame.data))
                finally:
                    await tts_engine.aclose()
        except Exception:
            logger.exception("SD Dialer voice preview failed")
            raise HTTPException(status_code=502, detail="Não foi possível gerar a amostra de voz.")
        audio = b"".join(pcm_parts)
    else:
        profiles = {
            "natural": {"length_scale": 1.08, "noise_scale": 0.38, "noise_w_scale": 0.43, "volume": 1.00},
            "clear": {"length_scale": 1.11, "noise_scale": 0.30, "noise_w_scale": 0.36, "volume": 1.01},
            "commercial": {"length_scale": 1.07, "noise_scale": 0.40, "noise_w_scale": 0.45, "volume": 1.02},
        }
        piper_payload = {
            "text": text,
            "voice": "pt_PT-tugão-medium",
            **profiles[voice],
        }
        if engine == "xai":
            try:
                audio, headers = await asyncio.to_thread(
                    _voice_preview_bytes,
                    LUMIN_KOKORO_URL,
                    {"text": text},
                    8,
                )
            except HTTPException as exc:
                logger.warning(
                    "Kokoro voice preview failed; falling back to Piper",
                    extra={"status_code": exc.status_code},
                )
                if not LUMIN_PIPER_CATALOG_URL:
                    raise HTTPException(status_code=503, detail="O motor Piper não está configurado.")
                audio, headers = await asyncio.to_thread(
                    _voice_preview_bytes,
                    LUMIN_PIPER_CATALOG_URL,
                    piper_payload,
                    120,
                )
        else:
            if not LUMIN_PIPER_CATALOG_URL:
                raise HTTPException(status_code=503, detail="O motor Piper não está configurado.")
            audio, headers = await asyncio.to_thread(
                _voice_preview_bytes,
                LUMIN_PIPER_CATALOG_URL,
                piper_payload,
                120,
            )
        try:
            sample_rate = int(headers.get("x-sample-rate", "24000"))
        except Exception:
            sample_rate = 24000
        try:
            channels = max(1, int(headers.get("x-channels", "1")))
        except Exception:
            channels = 1
        media_type = headers.get("content-type", "audio/pcm")

    if not audio:
        raise HTTPException(status_code=502, detail="O motor de voz devolveu uma amostra vazia.")
    if "audio/pcm" in media_type:
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio)
        audio = wav_buffer.getvalue()
        media_type = "audio/wav"

    return Response(
        content=audio,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/platform/voice-catalog")
async def platform_voice_catalog(
    authorization: str | None = Header(default=None, alias="Authorization"),
    language: str = "",
    q: str = "",
    quality: str = "",
    limit: int = 500,
):
    _require_platform_session(authorization)
    params = urllib.parse.urlencode({
        "language": str(language or "")[:20],
        "q": str(q or "")[:80],
        "quality": str(quality or "")[:20],
        "limit": max(1, min(int(limit or 500), 1000)),
    })
    request = urllib.request.Request(
        f"{LUMIN_PIPER_CATALOG_URL}/catalog?{params}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=detail[:1200] or "Voice catalog error")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Voice catalog unavailable: {exc}")


@app.post("/api/platform/voice-preview")
async def platform_voice_preview(
    payload: VoicePreviewRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _require_platform_session(authorization)
    voice = payload.voice.strip().lower()
    text = payload.text.strip()

    if voice in {"natural", "clear", "commercial"}:
        base_url = LUMIN_OPENVOICE_URL
        body = {"text": text, "voice_profile": voice}
        timeout = 45
    elif voice == "custom":
        if not payload.voiceSampleUrl.startswith("https://"):
            raise HTTPException(status_code=400, detail="Amostra de voz em falta")
        base_url = LUMIN_OPENVOICE_URL
        body = {"text": text, "audio_prompt_url": payload.voiceSampleUrl, "voice_profile": "natural"}
        timeout = 75
    elif voice == "kokoro":
        base_url = LUMIN_KOKORO_URL
        body = {"text": text}
        timeout = 180
    elif voice == "neural":
        base_url = LUMIN_CHATTERBOX_URL
        body = {"text": text, "language_id": "pt"}
        timeout = 180
    elif voice.startswith("piper:"):
        voice_key = voice.split(":", 1)[1].strip()
        if not re.fullmatch(r"[\w.-]{3,120}", voice_key, flags=re.UNICODE):
            raise HTTPException(status_code=400, detail="Voz Piper inválida")
        base_url = LUMIN_PIPER_CATALOG_URL
        body = {"text": text, "voice": voice_key}
        timeout = 120
    else:
        raise HTTPException(status_code=400, detail="Voz desconhecida")

    audio, headers = await asyncio.to_thread(_voice_preview_bytes, base_url, body, timeout)
    try:
        sample_rate = int(headers.get("x-sample-rate", "24000"))
    except Exception:
        sample_rate = 24000
    channels = 1
    try:
        channels = max(1, int(headers.get("x-channels", "1")))
    except Exception:
        channels = 1

    if "audio/pcm" in headers.get("content-type", "audio/pcm"):
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio)
        audio = wav_buffer.getvalue()
        media_type = "audio/wav"
    else:
        media_type = headers.get("content-type", "audio/wav")

    return Response(
        content=audio,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Sample-Rate": str(sample_rate),
            "X-Channels": str(channels),
            "X-Lumin-Voice": headers.get("x-lumin-voice", voice),
        },
    )


def _avatar_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{LUMIN_AVATAR_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=detail[:1200] or "Avatar engine error")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Avatar engine unavailable: {exc}")


@app.post("/api/platform/avatar")
async def platform_avatar_create(
    payload: AvatarRenderRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _require_platform_session(authorization)
    return await asyncio.to_thread(
        _avatar_json,
        "POST",
        "/jobs",
        {
            "image_url": payload.imageUrl,
            "audio_url": payload.audioUrl,
            "preprocess": payload.preprocess,
            "still": payload.still,
            "size": payload.size,
        },
    )


@app.get("/api/platform/avatar/{job_id}")
async def platform_avatar_status(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _require_platform_session(authorization)
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail="Invalid avatar job ID")
    return await asyncio.to_thread(_avatar_json, "GET", f"/jobs/{job_id}", None)


@app.get("/api/platform/avatar/{job_id}/video")
async def platform_avatar_video(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _require_platform_session(authorization)
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail="Invalid avatar job ID")

    def fetch_video() -> bytes:
        request = urllib.request.Request(f"{LUMIN_AVATAR_URL}/jobs/{job_id}/video", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise HTTPException(status_code=exc.code, detail=detail[:1200] or "Avatar video unavailable")

    video = await asyncio.to_thread(fetch_video)
    return Response(
        content=video,
        media_type="video/mp4",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="lumin-avatar-{job_id}.mp4"',
        },
    )


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

# voice-studio-env-refresh

# realtime-avatar-profile-dispatch-v2
