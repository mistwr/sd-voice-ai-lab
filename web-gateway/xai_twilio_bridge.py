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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Awaitable

import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.request_validator import RequestValidator


logger = logging.getLogger("lumin-xai-twilio")
_XAI_ENDPOINT = "wss://api.x.ai/v1/realtime"
_LISBON = ZoneInfo("Europe/Lisbon")

_FOLLOW_UP_TOOL = {
    "type": "function",
    "name": "schedule_follow_up",
    "description": "Marca na agenda do SD Dialer o contacto pedido pela pessoa. Só usar depois de a pessoa aceitar e indicar data e hora.",
    "parameters": {
        "type": "object",
        "properties": {
            "scheduled_at": {
                "type": "string",
                "description": "Data e hora em ISO 8601 com fuso horário Europe/Lisbon.",
            }
        },
        "required": ["scheduled_at"],
    },
}


def _clean(value: Any, limit: int = 1200) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", plain)).strip()


def _consent_answer(value: str) -> str | None:
    folded = _fold(value)
    if "nao" in folded.split():
        if any(term in folded for term in ("nao sei", "nao percebi", "nao entendi")):
            return None
        return "denied"
    yes = (
        folded in {"sim", "claro", "aceito", "autorizo", "pode", "esta bem", "pode ser"}
        or folded.startswith(("sim ", "claro ", "aceito ", "autorizo ", "eu autorizo ", "pode guardar", "pode registar", "pode ser "))
        or any(term in folded for term in ("autorizo a transcricao", "pode guardar", "pode registar", "aceito a transcricao"))
    )
    return "granted" if yes else None


def _pt_number(number: int, *, feminine: bool = False) -> str:
    units = ("zero", "uma" if feminine else "um", "duas" if feminine else "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove")
    teens = ("dez", "onze", "doze", "treze", "catorze", "quinze", "dezasseis", "dezassete", "dezoito", "dezanove")
    tens = {2: "vinte", 3: "trinta", 4: "quarenta", 5: "cinquenta"}
    if number < 10:
        return units[number]
    if number < 20:
        return teens[number - 10]
    ten, unit = divmod(number, 10)
    return tens[ten] if unit == 0 else f"{tens[ten]} e {units[unit]}"


class XaiTwilioBridge:
    """Direct Twilio Media Streams to xAI Realtime bridge; no LiveKit inference."""

    def __init__(self, calls: dict[str, dict[str, Any]]):
        self.calls = calls
        self.provider = os.getenv("LUMIN_VOICE_TRANSPORT", "livekit").strip().lower()
        self.xai_key = os.getenv("XAI_API_KEY", "").strip()
        self.model = os.getenv("XAI_REALTIME_MODEL", "grok-voice-latest").strip()
        public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        self.public_url = (
            os.getenv("LUMIN_GATEWAY_PUBLIC_URL")
            or (f"https://{public_domain}" if public_domain else "")
        ).rstrip("/")
        self.stream_secret = os.getenv("LUMIN_XAI_STREAM_SECRET", "")
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.api_key_sid = os.getenv("TWILIO_API_KEY_SID", "")
        self.api_key_secret = os.getenv("TWILIO_API_KEY_SECRET", "")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.supabase_url = os.getenv("SD_DIALER_SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SD_DIALER_SUPABASE_SERVICE_ROLE_KEY", "")
        self._router = APIRouter()
        self._register_routes()

    @property
    def enabled(self) -> bool:
        return self.provider == "xai-twilio"

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.xai_key
            and self.public_url.startswith("https://")
            and self.stream_secret
            and self.account_sid
            and self.api_key_sid
            and self.api_key_secret
            and self.auth_token
            and self.supabase_url
            and self.supabase_key
        )

    @property
    def router(self) -> APIRouter:
        return self._router

    def should_handle(self, source: str, agent_profile: dict[str, Any] | None) -> bool:
        profile_id = _clean((agent_profile or {}).get("id"), 80).casefold()
        return self.enabled and source in {"sd-dialer", "sd-dialer-manual"} and profile_id == "lumin-telecom"

    def require_configured(self) -> None:
        if not self.configured:
            raise HTTPException(
                status_code=503,
                detail="A via xAI direta não está configurada. Confirma XAI_API_KEY, Twilio, URL pública, segredo do stream e acesso de escrita ao histórico.",
            )

    def _register_routes(self) -> None:
        @self._router.post("/api/xai-twilio/twiml/{call_id}")
        async def twiml(call_id: str, request: Request):
            self.require_configured()
            state = self.calls.get(call_id)
            if not state:
                raise HTTPException(status_code=404, detail="Chamada desconhecida")
            await self._verify_twilio_request(request)
            form = await request.form()
            state["twilioCallSid"] = _clean(form.get("CallSid"), 80)
            token = self._stream_token(call_id)
            ws_base = "wss://" + self.public_url.removeprefix("https://").removeprefix("http://")
            stream_url = f"{ws_base}/api/xai-twilio/stream/{urllib.parse.quote(call_id)}"

            root = ET.Element("Response")
            connect = ET.SubElement(root, "Connect")
            stream = ET.SubElement(connect, "Stream", {"url": stream_url})
            ET.SubElement(stream, "Parameter", {"name": "token", "value": token})
            ET.SubElement(root, "Hangup")
            body = ET.tostring(root, encoding="unicode")
            return Response(content=f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>{body}", media_type="application/xml")

        @self._router.post("/api/xai-twilio/status/{call_id}")
        async def status(call_id: str, request: Request):
            self.require_configured()
            await self._verify_twilio_request(request)
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
                connected_at = float(state.get("connectedAt") or 0)
                duration_text = _clean(form.get("CallDuration"), 20)
                try:
                    duration = max(0, int(duration_text))
                except ValueError:
                    duration = max(0, int(time.time() - connected_at)) if connected_at else 0
                was_answered = bool(connected_at) or state.get("status") in {"connected", "ended"}
                state["status"] = "ended" if was_answered else "failed"
                state["endedAt"] = time.time()
                state["durationSeconds"] = duration
                state["endReason"] = twilio_status
                await self._update_call_row(
                    state,
                    status="answered" if was_answered else "failed",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=duration,
                    **({"error_message": twilio_status} if not was_answered else {}),
                )
            return Response(status_code=204)

        @self._router.websocket("/api/xai-twilio/stream/{call_id}")
        async def media_stream(websocket: WebSocket, call_id: str):
            if not self.configured:
                await websocket.close(code=1008)
                return
            state = self.calls.get(call_id)
            if not state:
                await websocket.close(code=1008)
                return
            await self._handle_stream(websocket, call_id, state)

    def _public_request_url(self, request: Request) -> str:
        if self.public_url:
            return f"{self.public_url}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
        return str(request.url)

    async def _verify_twilio_request(self, request: Request) -> None:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not self.auth_token or not signature:
            raise HTTPException(status_code=403, detail="Assinatura Twilio inválida")
        form = await request.form()
        params = {str(key): str(value) for key, value in form.items()}
        validator = RequestValidator(self.auth_token)
        if not validator.validate(self._public_request_url(request), params, signature):
            raise HTTPException(status_code=403, detail="Assinatura Twilio inválida")

    def _stream_token(self, call_id: str) -> str:
        expires = int(time.time()) + 15 * 60
        payload = f"{call_id}:{expires}".encode("utf-8")
        signature = hmac.new(self.stream_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
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
        expected = hmac.new(self.stream_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)

    async def start_outbound_call(
        self,
        state: dict[str, Any],
        phone: str,
        caller_id: str,
    ) -> None:
        call_id = str(state["callId"])
        try:
            self.require_configured()
            state["status"] = "preparing"
            state["transcriptConsentStatus"] = "pending"
            state["transport"] = "xai-twilio"
            state["callRecordCreated"] = await self._create_call_row(state, phone)
            if not state["callRecordCreated"]:
                raise RuntimeError("SD Dialer call history is unavailable")
            result = await asyncio.to_thread(self._create_twilio_call, call_id, phone, caller_id)
            state["twilioCallSid"] = _clean(result.get("sid"), 80)
            if state.get("status") not in {"connected", "ended"}:
                state["status"] = "ringing"
            logger.info("xAI direct call started call_id=%s", call_id)
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = "Não foi possível iniciar a chamada xAI/Twilio."
            state["endedAt"] = time.time()
            logger.warning("xAI direct call failed call_id=%s error_type=%s", call_id, type(exc).__name__)
            await self._update_call_row(
                state,
                status="failed",
                ended_at=datetime.now(timezone.utc).isoformat(),
                error_message="xAI/Twilio call setup failed",
            )

    def _create_twilio_call(self, call_id: str, phone: str, caller_id: str) -> dict[str, Any]:
        path = f"/2010-04-01/Accounts/{urllib.parse.quote(self.account_sid)}/Calls.json"
        url = f"https://api.twilio.com{path}"
        form = {
            "To": phone,
            "From": caller_id,
            "Url": f"{self.public_url}/api/xai-twilio/twiml/{urllib.parse.quote(call_id)}",
            "Method": "POST",
            "StatusCallback": f"{self.public_url}/api/xai-twilio/status/{urllib.parse.quote(call_id)}",
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        }
        body = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
        credentials = base64.b64encode(f"{self.api_key_sid}:{self.api_key_secret}".encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            url,
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

    async def _handle_stream(self, websocket: WebSocket, call_id: str, state: dict[str, Any]) -> None:
        await websocket.accept()
        stream_sid = ""
        authenticated = False
        xai = None
        mark_events: dict[str, asyncio.Event] = {}
        db_tasks: set[asyncio.Task[Any]] = set()
        pending_user: dict[str, str] = {}
        processed_user_items: set[str] = set()
        pending_agent: dict[str, str] = {}
        processed_agent_responses: set[str] = set()
        pending_tool_calls: dict[str, list[dict[str, Any]]] = {}
        completed_responses: set[str] = set()
        tool_responses_started: set[str] = set()
        active_response_id = "response"

        def schedule(coro: Awaitable[Any]) -> None:
            task = asyncio.create_task(coro)
            db_tasks.add(task)
            task.add_done_callback(db_tasks.discard)

        async def dispatch_tool_calls(response_id: str) -> None:
            if response_id in tool_responses_started:
                return
            calls = pending_tool_calls.pop(response_id, [])
            if not calls or xai is None:
                return
            tool_responses_started.add(response_id)
            mark_name = f"xai-response-{re.sub(r'[^a-zA-Z0-9_-]', '', response_id)[:50]}"
            mark_event = asyncio.Event()
            mark_events[mark_name] = mark_event
            try:
                await websocket.send_json({"event": "mark", "streamSid": stream_sid, "mark": {"name": mark_name}})
                try:
                    await asyncio.wait_for(mark_event.wait(), timeout=8)
                except asyncio.TimeoutError:
                    logger.info("Twilio playback mark timed out call_id=%s", call_id)
                for tool_call in calls:
                    result = await self._run_tool(state, tool_call)
                    await xai.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": _clean(tool_call.get("call_id"), 120),
                            "output": json.dumps(result, ensure_ascii=False),
                        },
                    }, ensure_ascii=False))
                await xai.send(json.dumps({"type": "response.create"}))
            except Exception as exc:
                logger.warning("xAI tool handling failed call_id=%s error_type=%s", call_id, type(exc).__name__)
            finally:
                mark_events.pop(mark_name, None)

        try:
            # Twilio sends connected and start before media. Read start first so the first
            # xAI response always has a valid streamSid to send audio back to.
            start_deadline = asyncio.get_running_loop().time() + 10
            while not stream_sid:
                remaining = start_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RuntimeError("Twilio start event timed out")
                message = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=remaining))
                if message.get("event") == "start":
                    start = message.get("start") or {}
                    custom_parameters = start.get("customParameters") or {}
                    if not self._valid_stream_token(call_id, _clean(custom_parameters.get("token"), 160)):
                        await websocket.close(code=1008)
                        return
                    if state.get("streamClaimed"):
                        await websocket.close(code=1008)
                        return
                    state["streamClaimed"] = True
                    authenticated = True
                    stream_sid = _clean(start.get("streamSid"), 80)
                    state["twilioCallSid"] = _clean(start.get("callSid"), 80) or state.get("twilioCallSid")
                    if not stream_sid:
                        raise RuntimeError("Twilio start event did not include streamSid")
                    state["status"] = "connected"
                    state.setdefault("connectedAt", time.time())
                    state["transcriptConsentStatus"] = state.get("transcriptConsentStatus") or "pending"
                    await self._update_call_row(
                        state,
                        status="answered",
                        started_at=datetime.now(timezone.utc).isoformat(),
                        transcript_consent_status="pending",
                    )
                elif message.get("event") == "stop":
                    return

            state["streamConnected"] = True
            ws_url = f"{_XAI_ENDPOINT}?model={urllib.parse.quote(self.model)}"
            xai = await websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {self.xai_key}"},
                open_timeout=12,
                close_timeout=3,
                ping_interval=20,
                ping_timeout=20,
                max_size=1_048_576,
                max_queue=16,
            )
            await self._configure_xai(xai, state)
            await xai.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "force_message",
                    "role": "assistant",
                    "interruptible": False,
                    "content": [{"type": "output_text", "text": self._disclosure(state)}],
                },
            }, ensure_ascii=False))

            async def from_twilio() -> None:
                async for raw in websocket.iter_text():
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("event")
                    if event_type == "media":
                        media = event.get("media") or {}
                        if media.get("track") not in {"inbound", "inbound_track"}:
                            continue
                        payload = media.get("payload")
                        if payload and xai is not None:
                            await xai.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": payload,
                            }))
                    elif event_type == "mark":
                        mark = _clean((event.get("mark") or {}).get("name"), 120)
                        if mark in mark_events:
                            mark_events[mark].set()
                    elif event_type == "stop":
                        return

            async def from_xai() -> None:
                nonlocal active_response_id
                assert xai is not None
                async for raw in xai:
                    if isinstance(raw, bytes):
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type", "")
                    if kind == "input_audio_buffer.speech_started":
                        await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                        continue
                    if kind in {"response.output_audio.delta", "response.audio.delta"}:
                        delta = event.get("delta")
                        if delta:
                            await websocket.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": delta},
                            })
                        continue
                    if kind in {"conversation.item.input_audio_transcription.updated", "conversation.item.input_audio_transcription.delta"}:
                        item_id = _clean(event.get("item_id") or (event.get("item") or {}).get("id"), 120)
                        text_value = _clean(event.get("transcript") or event.get("delta"), 4000)
                        if item_id and text_value and item_id not in processed_user_items:
                            pending_user[item_id] = text_value
                        continue
                    if kind == "conversation.item.input_audio_transcription.completed":
                        item_id = _clean(event.get("item_id"), 120)
                        text_value = _clean(event.get("transcript"), 4000)
                        if text_value and (not item_id or item_id not in processed_user_items):
                            await self._process_user_transcript(state, text_value, schedule)
                            if item_id:
                                processed_user_items.add(item_id)
                        if item_id:
                            pending_user.pop(item_id, None)
                        continue
                    if kind == "input_audio_buffer.committed":
                        item_id = _clean(event.get("item_id"), 120)
                        continue
                    if kind == "response.created":
                        for item_id in list(pending_user):
                            text_value = pending_user.pop(item_id)
                            if item_id not in processed_user_items:
                                await self._process_user_transcript(state, text_value, schedule)
                                processed_user_items.add(item_id)
                        active_response_id = _clean((event.get("response") or {}).get("id"), 120) or active_response_id
                        continue
                    if kind in {"response.output_audio_transcript.delta", "response.audio_transcript.delta"}:
                        response_id = _clean(event.get("response_id") or active_response_id, 120)
                        delta = _clean(event.get("delta"), 4000)
                        if delta:
                            pending_agent[response_id] = (pending_agent.get(response_id, "") + delta)[-4000:]
                        continue
                    if kind in {"response.output_audio_transcript.done", "response.audio_transcript.done"}:
                        response_id = _clean(event.get("response_id") or active_response_id, 120)
                        delta_text = pending_agent.pop(response_id, "")
                        text_value = _clean(event.get("transcript") or event.get("text") or delta_text, 4000)
                        if text_value and response_id not in processed_agent_responses:
                            await self._process_agent_transcript(state, text_value, schedule)
                            processed_agent_responses.add(response_id)
                        continue
                    if kind == "response.function_call_arguments.done":
                        response_id = _clean(event.get("response_id") or active_response_id, 120)
                        pending_tool_calls.setdefault(response_id, []).append(event)
                        if response_id in completed_responses:
                            schedule(dispatch_tool_calls(response_id))
                        continue
                    if kind == "response.done":
                        response = event.get("response") or {}
                        response_id = _clean(response.get("id") or active_response_id, 120)
                        completed_responses.add(response_id)
                        text_value = pending_agent.pop(response_id, "")
                        if text_value and response_id not in processed_agent_responses:
                            await self._process_agent_transcript(state, text_value, schedule)
                            processed_agent_responses.add(response_id)
                        if pending_tool_calls.get(response_id):
                            schedule(dispatch_tool_calls(response_id))
                        continue
                    if kind == "error":
                        error = event.get("error") or {}
                        logger.warning(
                            "xAI Realtime error call_id=%s code=%s",
                            call_id,
                            _clean(error.get("code") or error.get("type"), 80),
                        )
                        raise RuntimeError("xAI Realtime returned an error")

            twilio_task = asyncio.create_task(from_twilio())
            xai_task = asyncio.create_task(from_xai())
            done, pending = await asyncio.wait(
                {twilio_task, xai_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled():
                    exception = task.exception()
                    if exception:
                        raise exception
                    if task is xai_task:
                        raise RuntimeError("xAI Realtime WebSocket closed")
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            state["error"] = "A ligação em tempo real foi interrompida."
            logger.warning(
                "xAI media stream failed call_id=%s error_type=%s",
                call_id,
                type(exc).__name__,
            )
            try:
                await websocket.close(code=1011)
            except Exception:
                pass
        finally:
            if not authenticated:
                state["streamClaimed"] = False
                return
            state["streamConnected"] = False
            if xai is not None:
                try:
                    await xai.close()
                except Exception:
                    pass
            if authenticated:
                try:
                    await websocket.close(code=1000)
                except Exception:
                    pass
            remaining_db_tasks = list(db_tasks)
            if remaining_db_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*remaining_db_tasks, return_exceptions=True),
                        timeout=8.5,
                    )
                except asyncio.TimeoutError:
                    for task in remaining_db_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*remaining_db_tasks, return_exceptions=True)
            connected_at = float(state.get("connectedAt") or 0)
            state["endedAt"] = time.time()
            state["durationSeconds"] = max(0, int(state["endedAt"] - connected_at)) if connected_at else 0
            was_answered = bool(connected_at)
            state["status"] = "ended" if was_answered else "failed"
            await self._update_call_row(
                state,
                status="answered" if was_answered else "failed",
                ended_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=state["durationSeconds"],
            )

    async def _configure_xai(self, xai: Any, state: dict[str, Any]) -> None:
        profile = state.get("agent") or {}
        voice_choice = _clean(profile.get("voice"), 80).casefold()
        voices = {"natural": "sal", "clear": "rex", "commercial": "leo"}
        voice = voices.get(voice_choice, "sal")
        session = {
            "voice": voice,
            "instructions": self._instructions(state),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "transcription": {"model": "grok-transcribe", "language_hint": "pt-PT"},
                },
                "output": {"format": {"type": "audio/pcmu"}},
            },
            "turn_detection": {"type": "server_vad"},
        }
        follow_up_enabled = bool(
            state.get("leadId") and state.get("companyId") and state.get("sdUserId")
        )
        if follow_up_enabled:
            session["tools"] = [_FOLLOW_UP_TOOL]
        sent = False
        deadline = asyncio.get_running_loop().time() + 12
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("xAI session setup timed out")
            raw = await asyncio.wait_for(xai.recv(), timeout=remaining)
            event = json.loads(raw) if isinstance(raw, str) else {}
            kind = event.get("type")
            if kind in {"session.created", "conversation.created"} and not sent:
                await xai.send(json.dumps({"type": "session.update", "session": session}, ensure_ascii=False))
                sent = True
            elif kind == "session.updated":
                return
            elif kind == "error":
                raise RuntimeError("xAI session configuration rejected")

    def _instructions(self, state: dict[str, Any]) -> str:
        profile = state.get("agent") or {}
        name = _clean(profile.get("name"), 80) or "Lumin"
        company = _clean(profile.get("company"), 120) or "Compara+"
        opening = _clean(profile.get("opening"), 800).replace("[nome]", _clean(state.get("name"), 100) or " ")
        question_end = opening.rfind("?")
        if question_end >= 0:
            previous_sentence_end = max(
                opening.rfind(".", 0, question_end),
                opening.rfind("!", 0, question_end),
                opening.rfind("?", 0, question_end),
            )
            opening = opening[previous_sentence_end + 1 : question_end + 1].strip()
        objective = _clean(profile.get("objective"), 500)
        product = _clean(profile.get("product"), 1000)
        offer = _clean(profile.get("offer"), 1000)
        objections = _clean(profile.get("objections"), 1200)
        notes = _clean(profile.get("notes"), 2200)
        tone = _clean(profile.get("tone"), 300)
        return f"""És {name}, assistente virtual da {company}, numa chamada telefónica de saída em português de Portugal.
A tua primeira frase já informou que o áudio não é gravado e perguntou se a pessoa autoriza guardar a transcrição de texto.
Respeita a resposta. Se autorizar, conversa normalmente; se recusar, continua sem guardar transcrições; se não for claro, pergunta uma vez e não insistas.
A apresentação já foi feita. Não a repitas. Usa a pergunta de abertura configurada só se ainda fizer sentido: {opening}
Objetivo: {objective}
Produto e informação autorizada: {product}
Oferta permitida: {offer}
Objeções e recusas: {objections}
Guião adicional: {notes}
Tom: {tone}
Fala com frases naturais e curtas, uma pergunta de cada vez. Aproveita tudo o que a pessoa já disse e nunca repitas uma pergunta respondida. Confirma apenas uma vez quando não entenderes uma informação importante. Diz horas por extenso em português de Portugal. Não inventes preços, poupanças, cobertura ou condições. Só confirmes uma marcação depois de schedule_follow_up devolver sucesso. Se a pessoa pedir para não voltar a ser contactada, respeita o pedido e termina a chamada."""

    def _disclosure(self, state: dict[str, Any]) -> str:
        name = _clean(state.get("name"), 80)
        agent = _clean((state.get("agent") or {}).get("name"), 80) or "Lumin"
        company = _clean((state.get("agent") or {}).get("company"), 120) or "Compara+"
        hello = f"Olá, {name}." if name and name.casefold() not in {"cliente", "contacto manual"} else "Olá."
        return f"{hello} Sou o {agent}, assistente virtual da {company}. Não gravamos o áudio. Só guardamos a transcrição em texto com a sua autorização. Autoriza?"

    async def _process_user_transcript(
        self,
        state: dict[str, Any],
        text_value: str,
        schedule: Any,
    ) -> None:
        text_value = _clean(text_value, 4000)
        if not text_value:
            return
        consent = state.get("transcriptConsentStatus", "pending")
        if consent == "pending":
            decision = _consent_answer(text_value)
            if decision == "granted":
                state["transcriptConsentStatus"] = "granting"
                state["transcriptConsentAt"] = datetime.now(timezone.utc).isoformat()
                schedule(self._grant_and_record_transcript(state, "client", text_value))
            elif decision == "denied":
                state["transcriptConsentStatus"] = "denied"
                schedule(self._update_call_row(state, transcript_consent_status="denied"))
            return
        if consent == "granting":
            state.setdefault("pendingUserTranscripts", []).append(text_value)
            return
        if consent == "granted":
            schedule(self._insert_transcript(state, "client", text_value))

    async def _process_agent_transcript(self, state: dict[str, Any], text_value: str, schedule: Any) -> None:
        if state.get("transcriptConsentStatus") == "granted":
            schedule(self._insert_transcript(state, "agent", _clean(text_value, 4000)))
        elif state.get("transcriptConsentStatus") == "granting":
            state.setdefault("pendingAgentTranscripts", []).append(_clean(text_value, 4000))

    async def _grant_and_record_transcript(self, state: dict[str, Any], speaker: str, text_value: str) -> None:
        updated = await self._update_call_row(
            state,
            transcript_consent_status="granted",
            transcript_consent_at=state.get("transcriptConsentAt"),
        )
        if updated:
            state["transcriptConsentStatus"] = "granted"
            await self._insert_transcript(state, speaker, text_value)
            pending_user = state.pop("pendingUserTranscripts", [])
            pending_agent = state.pop("pendingAgentTranscripts", [])
            for pending_text in pending_user:
                await self._insert_transcript(state, "client", pending_text)
            for pending_text in pending_agent:
                await self._insert_transcript(state, "agent", pending_text)
        else:
            state["transcriptConsentStatus"] = "unavailable"
            state.pop("pendingUserTranscripts", None)
            state.pop("pendingAgentTranscripts", None)

    async def _run_tool(self, state: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
        name = _clean(call.get("name"), 80)
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            arguments = {}
        if name != "schedule_follow_up":
            return {"ok": False, "message": "Ferramenta desconhecida."}
        if state.get("followUpConfirmation"):
            return {"ok": True, "message": state["followUpConfirmation"]}
        if state.get("followUpBusy"):
            return {"ok": False, "message": "A marcação está a ser confirmada. Aguarda pelo resultado."}
        try:
            local = datetime.fromisoformat(_clean(arguments.get("scheduled_at"), 80).replace("Z", "+00:00"))
            if local.tzinfo is None:
                local = local.replace(tzinfo=_LISBON)
            local = local.astimezone(_LISBON)
        except (TypeError, ValueError):
            return {"ok": False, "message": "Data ou hora inválida. Pede à pessoa que esclareça."}
        utc_time = local.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if utc_time <= now + timedelta(minutes=2):
            return {"ok": False, "message": "A data ou hora já passou ou é demasiado próxima. Pede outro horário."}
        if utc_time > now + timedelta(days=180):
            return {"ok": False, "message": "A data está demasiado distante. Pede um dia mais próximo."}
        lead_id = _clean(state.get("leadId"), 80)
        company_id = _clean(state.get("companyId"), 80)
        partner_id = _clean(state.get("sdUserId"), 80)
        if not (lead_id and company_id and partner_id and self.supabase_url and self.supabase_key):
            return {"ok": False, "message": "Não foi possível confirmar na agenda."}

        state["followUpBusy"] = True
        try:
            await asyncio.to_thread(
                self._supabase_request,
                "follow_ups",
                method="PATCH",
                params={"lead_id": f"eq.{lead_id}", "done": "eq.false"},
                body={"done": True},
            )
            await asyncio.to_thread(
                self._supabase_request,
                "follow_ups",
                method="POST",
                body={
                    "lead_id": lead_id,
                    "parceiro_id": partner_id,
                    "company_id": company_id,
                    "scheduled_at": local.isoformat(timespec="seconds"),
                    "notes": "Contacto solicitado durante chamada do assistente virtual.",
                    "done": False,
                },
            )
        except Exception as exc:
            logger.warning("xAI follow-up write failed call_id=%s error_type=%s", state.get("callId"), type(exc).__name__)
            return {"ok": False, "message": "Não consegui confirmar a marcação. Não confirmes que ficou marcada."}
        finally:
            state["followUpBusy"] = False

        weekdays = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
        months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
        if local.hour == 0:
            spoken_time = "à meia-noite"
        elif local.hour == 12:
            spoken_time = "ao meio-dia"
        elif local.hour == 1:
            spoken_time = "à uma hora"
        else:
            spoken_time = f"às {_pt_number(local.hour, feminine=True)} horas"
        if local.minute:
            if local.minute == 30:
                minute_phrase = "meia"
            elif local.minute == 1:
                minute_phrase = "um minuto"
            else:
                minute_phrase = f"{_pt_number(local.minute)} minutos"
            spoken_time += f" e {minute_phrase}"
        spoken = f"{weekdays[local.weekday()]}, {local.day} de {months[local.month - 1]}, {spoken_time}"
        state["followUpConfirmation"] = f"Marcação confirmada para {spoken}."
        return {"ok": True, "message": state["followUpConfirmation"]}

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
            "transcript_consent_status": "pending",
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
            logger.warning("xAI call history create failed call_id=%s error_type=%s", state.get("callId"), type(exc).__name__)
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
            logger.warning("xAI call history update failed call_id=%s error_type=%s", call_id, type(exc).__name__)
            return False

    async def _insert_transcript(self, state: dict[str, Any], speaker: str, text_value: str) -> None:
        if (
            state.get("transcriptConsentStatus") != "granted"
            or not state.get("callRecordCreated")
            or not self.supabase_url
            or not self.supabase_key
        ):
            return
        payload = {
            "call_id": state.get("callId"),
            "company_id": state.get("companyId"),
            "speaker": speaker,
            "text": _clean(text_value, 4000),
            "is_interruption": False,
            "spoken_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await asyncio.to_thread(self._supabase_request, "voice_call_transcripts", method="POST", body=payload)
        except Exception as exc:
            logger.warning("xAI transcript write failed call_id=%s error_type=%s", state.get("callId"), type(exc).__name__)

    def _supabase_request(
        self,
        table: str,
        *,
        method: str,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> Any:
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("Supabase service role is not configured")
        query = urllib.parse.urlencode(params or {})
        url = f"{self.supabase_url}/rest/v1/{table}" + (f"?{query}" if query else "")
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None

