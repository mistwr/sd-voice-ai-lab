from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.request_validator import RequestValidator


logger = logging.getLogger("lumin-twilio-relay-test")
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_PT_PT_VOICE = "TsZfI8Nbn2Xd7ArC76n9"
_WELCOME = (
    "Este é um teste da voz e do reconhecimento da Twilio. "
    "Diga: Sou cliente MEO, pago vinte euros e prefiro amanhã às sete da manhã. "
    "No fim, pode dizer terminar teste."
)


def _clean(value: Any, limit: int = 1200) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _fold(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", plain)).strip()


def _echo_reply(voice_prompt: str) -> str | None:
    """Return a Portuguese confirmation without storing the transcribed text."""
    prompt = _clean(voice_prompt, 600)
    if not prompt:
        return "Não consegui perceber. Pode repetir mais devagar, por favor?"
    folded = _fold(prompt)
    if folded in {"fim", "terminar", "terminar teste", "acabar teste", "desligar"}:
        return None
    recognised = prompt.rstrip(" .?!")
    return f"Percebi: {recognised}. Está correto? Se não, diga a frase novamente."


class TwilioRelayTestBridge:
    """Allowlisted echo test for Twilio ConversationRelay; no LLM or transcript storage."""

    def __init__(self, calls: dict[str, dict[str, Any]]):
        self.calls = calls
        self.transport = os.getenv("LUMIN_VOICE_TRANSPORT", "livekit").strip().lower()
        self.public_url = (
            os.getenv("LUMIN_GATEWAY_PUBLIC_URL")
            or (
                f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()}"
                if os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
                else ""
            )
        ).rstrip("/")
        self.test_phone = self._normalise_phone(os.getenv("LUMIN_TWILIO_TEST_PHONE", ""))
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.api_key_sid = os.getenv("TWILIO_API_KEY_SID", "").strip()
        self.api_key_secret = os.getenv("TWILIO_API_KEY_SECRET", "").strip()
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.stream_secret = os.getenv("LUMIN_TWILIO_RELAY_TEST_SECRET", "")
        self.supabase_url = os.getenv("SD_DIALER_SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SD_DIALER_SUPABASE_SERVICE_ROLE_KEY", "")
        self._router = APIRouter()
        self._register_routes()

    @staticmethod
    def _normalise_phone(value: str) -> str:
        phone = re.sub(r"[\s().-]", "", str(value or ""))
        return phone if _E164_RE.fullmatch(phone) else ""

    @property
    def enabled(self) -> bool:
        return self.transport == "twilio-relay-test"

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.test_phone
            and self.public_url.startswith("https://")
            and self.account_sid
            and self.api_key_sid
            and self.api_key_secret
            and self.auth_token
            and len(self.stream_secret.encode("utf-8")) >= 32
            and self.supabase_url
            and self.supabase_key
        )

    @property
    def router(self) -> APIRouter:
        return self._router

    def should_handle(self, source: str, agent_profile: dict[str, Any] | None) -> bool:
        profile_id = _clean((agent_profile or {}).get("id"), 80).casefold()
        return (
            self.enabled
            and source in {"sd-dialer", "sd-dialer-manual"}
            and profile_id == "lumin-telecom"
        )

    def require_configured(self) -> None:
        if not self.configured:
            raise HTTPException(
                status_code=503,
                detail=(
                    "O teste ConversationRelay não está configurado. Confirma o número de teste, "
                    "Twilio, URL pública, segredo do WebSocket e acesso ao histórico do SD Dialer."
                ),
            )

    def assert_test_phone(self, phone: str) -> None:
        normalized = self._normalise_phone(phone)
        if not self.test_phone or normalized != self.test_phone:
            raise HTTPException(
                status_code=403,
                detail="O teste ConversationRelay só pode ligar para o número de teste configurado.",
            )

    def _register_routes(self) -> None:
        @self._router.post("/api/twilio-relay-test/twiml/{call_id}")
        async def twiml(call_id: str, request: Request):
            self.require_configured()
            state = self.calls.get(call_id)
            if not state:
                raise HTTPException(status_code=404, detail="Chamada desconhecida")
            await self._verify_http_request(request)
            form = await request.form()
            state["twilioCallSid"] = _clean(form.get("CallSid"), 80)
            token = self._stream_token(call_id)
            body = self._build_twiml(call_id, token)
            return Response(
                content=f'<?xml version="1.0" encoding="UTF-8"?>{body}',
                media_type="application/xml",
            )

        @self._router.post("/api/twilio-relay-test/status/{call_id}")
        async def status(call_id: str, request: Request):
            self.require_configured()
            await self._verify_http_request(request)
            state = self.calls.get(call_id)
            if not state:
                return Response(status_code=204)
            form = await request.form()
            twilio_status = _clean(form.get("CallStatus"), 40).lower()
            state["twilioCallSid"] = _clean(form.get("CallSid"), 80) or state.get("twilioCallSid")
            if twilio_status == "ringing":
                if state.get("status") not in {"connected", "ended", "failed"}:
                    state["status"] = "ringing"
                await self._update_call_row(state, status="ringing")
            elif twilio_status == "in-progress":
                state["status"] = "connected"
                state.setdefault("connectedAt", time.time())
                await self._update_call_row(
                    state,
                    status="answered",
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
            elif twilio_status in {"completed", "busy", "failed", "no-answer", "canceled"}:
                duration_text = _clean(form.get("CallDuration"), 20)
                try:
                    duration = max(0, int(duration_text))
                except ValueError:
                    connected_at = float(state.get("connectedAt") or 0)
                    duration = max(0, int(time.time() - connected_at)) if connected_at else 0
                was_answered = bool(state.get("connectedAt")) or state.get("status") in {"connected", "ended"}
                state["status"] = "ended" if was_answered else "failed"
                state["endedAt"] = time.time()
                state["durationSeconds"] = duration
                state["endReason"] = twilio_status
                update: dict[str, Any] = {
                    "status": "completed" if was_answered else "failed",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": duration,
                }
                if not was_answered:
                    update["error_message"] = f"Twilio status: {twilio_status}"
                await self._update_call_row(state, **update)
            return Response(status_code=204)

        @self._router.websocket("/api/twilio-relay-test/stream/{call_id}")
        async def stream(websocket: WebSocket, call_id: str):
            if not self.configured or call_id not in self.calls:
                await websocket.close(code=1008)
                return
            await self._handle_stream(websocket, call_id, self.calls[call_id])

    def _build_twiml(self, call_id: str, token: str) -> str:
        host = self.public_url.removeprefix("https://").removeprefix("http://")
        root = ET.Element("Response")
        connect = ET.SubElement(root, "Connect")
        relay = ET.SubElement(
            connect,
            "ConversationRelay",
            {
                "url": f"wss://{host}/api/twilio-relay-test/stream/{urllib.parse.quote(call_id)}",
                "welcomeGreeting": _WELCOME,
                "language": "pt-PT",
                "ttsProvider": "ElevenLabs",
                "voice": _PT_PT_VOICE,
                "transcriptionProvider": "Google",
                "speechModel": "telephony",
                "interruptible": "any",
            },
        )
        ET.SubElement(relay, "Parameter", {"name": "token", "value": token})
        return ET.tostring(root, encoding="unicode")

    async def start_outbound_call(
        self,
        state: dict[str, Any],
        phone: str,
        caller_id: str,
    ) -> None:
        call_id = str(state["callId"])
        try:
            self.require_configured()
            self.assert_test_phone(phone)
            state["status"] = "preparing"
            state["transport"] = "twilio-relay-test"
            state["transcriptConsentStatus"] = "not_requested"
            state["recordingEnabled"] = False
            state["callRecordCreated"] = await self._create_call_row(state, phone)
            if not state["callRecordCreated"]:
                raise RuntimeError("SD Dialer call history is unavailable")
            result = await asyncio.to_thread(self._create_twilio_call, call_id, phone, caller_id)
            state["twilioCallSid"] = _clean(result.get("sid"), 80)
            if state.get("status") not in {"connected", "ended"}:
                state["status"] = "ringing"
            logger.info("ConversationRelay echo test started call_id=%s", call_id)
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = "Não foi possível iniciar o teste ConversationRelay."
            state["endedAt"] = time.time()
            logger.warning(
                "ConversationRelay test failed call_id=%s error_type=%s",
                call_id,
                type(exc).__name__,
            )
            await self._update_call_row(
                state,
                status="failed",
                ended_at=datetime.now(timezone.utc).isoformat(),
                error_message="ConversationRelay test setup failed",
            )

    def _create_twilio_call(self, call_id: str, phone: str, caller_id: str) -> dict[str, Any]:
        path = f"/2010-04-01/Accounts/{urllib.parse.quote(self.account_sid)}/Calls.json"
        form = {
            "To": phone,
            "From": caller_id,
            "Url": f"{self.public_url}/api/twilio-relay-test/twiml/{urllib.parse.quote(call_id)}",
            "Method": "POST",
            "StatusCallback": f"{self.public_url}/api/twilio-relay-test/status/{urllib.parse.quote(call_id)}",
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        }
        body = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
        credentials = base64.b64encode(
            f"{self.api_key_sid}:{self.api_key_secret}".encode("utf-8")
        ).decode("ascii")
        req = urllib.request.Request(
            f"https://api.twilio.com{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                if not data.get("sid"):
                    raise RuntimeError("Twilio did not return a call SID")
                return data
        except urllib.error.HTTPError as exc:
            logger.warning("Twilio call API returned HTTP %s", exc.code)
            raise RuntimeError("Twilio call creation failed") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Twilio call API unavailable") from exc

    async def _handle_stream(
        self,
        websocket: WebSocket,
        call_id: str,
        state: dict[str, Any],
    ) -> None:
        await websocket.accept()
        if not await self._verify_websocket(websocket):
            await websocket.close(code=1008)
            return
        try:
            setup = json.loads(await websocket.receive_text())
            custom = setup.get("customParameters") or {}
            if (
                setup.get("type") != "setup"
                or not self._valid_stream_token(call_id, str(custom.get("token") or ""))
                or (
                    state.get("twilioCallSid")
                    and setup.get("callSid") != state.get("twilioCallSid")
                )
            ):
                await websocket.close(code=1008)
                return
            state["status"] = "connected"
            state.setdefault("connectedAt", time.time())
            logger.info("ConversationRelay WebSocket authenticated call_id=%s", call_id)
            while True:
                message = json.loads(await websocket.receive_text())
                if message.get("type") != "prompt" or not message.get("last", False):
                    continue
                reply = _echo_reply(str(message.get("voicePrompt") or ""))
                if reply is None:
                    await websocket.send_json({"type": "end"})
                    return
                await websocket.send_json(
                    {
                        "type": "text",
                        "token": reply,
                        "lang": "pt-PT",
                        "last": True,
                        "interruptible": True,
                    }
                )
        except WebSocketDisconnect:
            logger.info("ConversationRelay WebSocket disconnected call_id=%s", call_id)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "ConversationRelay message failed call_id=%s error_type=%s",
                call_id,
                type(exc).__name__,
            )
            try:
                await websocket.close(code=1007)
            except RuntimeError:
                pass

    async def _verify_http_request(self, request: Request) -> None:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not self.auth_token or not signature:
            raise HTTPException(status_code=403, detail="Assinatura Twilio inválida")
        form = await request.form()
        params = {str(key): str(value) for key, value in form.items()}
        url = f"{self.public_url}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        if not RequestValidator(self.auth_token).validate(url, params, signature):
            raise HTTPException(status_code=403, detail="Assinatura Twilio inválida")

    async def _verify_websocket(self, websocket: WebSocket) -> bool:
        signature = websocket.headers.get("x-twilio-signature", "")
        if not self.auth_token or not signature:
            return False
        host = self.public_url.removeprefix("https://").removeprefix("http://")
        url = f"wss://{host}{websocket.url.path}"
        if websocket.url.query:
            url += f"?{websocket.url.query}"
        return RequestValidator(self.auth_token).validate(url, {}, signature)

    def _stream_token(self, call_id: str) -> str:
        expires = int(time.time()) + 15 * 60
        payload = f"{call_id}:{expires}".encode("utf-8")
        signature = hmac.new(
            self.stream_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        return f"{expires}.{signature}"

    def _valid_stream_token(self, call_id: str, token: str) -> bool:
        try:
            expires_text, supplied = token.split(".", 1)
            expires = int(expires_text)
        except (ValueError, TypeError):
            return False
        if expires < int(time.time()) or expires > int(time.time()) + 16 * 60:
            return False
        payload = f"{call_id}:{expires}".encode("utf-8")
        expected = hmac.new(
            self.stream_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(supplied, expected)

    async def _create_call_row(self, state: dict[str, Any], phone: str) -> bool:
        company_id = _clean(state.get("companyId"), 80)
        if not (company_id and self.supabase_url and self.supabase_key):
            return False
        payload = {
            "id": state.get("callId"),
            "company_id": company_id,
            "client_name": _clean(state.get("name"), 100) or None,
            "phone_number": phone,
            "status": "queued",
            "livekit_room": None,
            "recording_enabled": False,
            "recording_url": None,
            "created_by": _clean(state.get("sdUserId"), 80) or None,
            "lead_id": _clean(state.get("leadId"), 80) or None,
            "source": _clean(state.get("source"), 80) or "sd-dialer",
            "transcript_consent_status": "not_requested",
        }
        try:
            await asyncio.to_thread(
                self._supabase_request,
                "voice_calls",
                method="POST",
                params={"on_conflict": "id"},
                body=payload,
                prefer="resolution=merge-duplicates,return=minimal",
            )
            return True
        except Exception as exc:
            logger.warning(
                "ConversationRelay test history create failed call_id=%s error_type=%s",
                state.get("callId"),
                type(exc).__name__,
            )
            return False

    async def _update_call_row(self, state: dict[str, Any], **fields: Any) -> bool:
        if not state.get("callRecordCreated"):
            return False
        call_id = _clean(state.get("callId"), 80)
        if not call_id or not self.supabase_url or not self.supabase_key:
            return False
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            await asyncio.to_thread(
                self._supabase_request,
                "voice_calls",
                method="PATCH",
                params={"id": f"eq.{call_id}"},
                body=fields,
                prefer="return=minimal",
            )
            return True
        except Exception as exc:
            logger.warning(
                "ConversationRelay test history update failed call_id=%s error_type=%s",
                call_id,
                type(exc).__name__,
            )
            return False

    def _supabase_request(
        self,
        table: str,
        *,
        method: str,
        params: dict[str, str],
        body: dict[str, Any],
        prefer: str,
    ) -> None:
        query = urllib.parse.urlencode(params)
        url = f"{self.supabase_url}/rest/v1/{table}?{query}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "apikey": self.supabase_key,
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "application/json",
                "Prefer": prefer,
            },
        )
        with urllib.request.urlopen(req, timeout=12):
            return
