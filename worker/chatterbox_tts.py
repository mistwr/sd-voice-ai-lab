"""
Remote Chatterbox TTS adapter for LUMIN LiveKit agents.

The model is self-hosted by LUMIN. A custom agent can pass a short-lived
signed reference-audio URL to clone an authorised voice. With no reference
audio the service uses its built-in neutral system voice.
"""
from __future__ import annotations

import httpx

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid


class ChatterboxRemoteTTS(tts.TTS):
    def __init__(
        self,
        base_url: str,
        *,
        audio_prompt_url: str = "",
        language_id: str = "pt",
        sample_rate: int = 24000,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._audio_prompt_url = audio_prompt_url.strip()
        self._language_id = language_id or "pt"
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

    @property
    def provider(self) -> str:
        return "LUMIN Chatterbox self-hosted"

    @property
    def model(self) -> str:
        return "chatterbox-multilingual-v3"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return ChatterboxChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        return None


class ChatterboxChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: ChatterboxRemoteTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=conn_options,
        )
        self._chatterbox = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        payload = {
            "text": self._input_text,
            "language_id": self._chatterbox._language_id,
        }
        if self._chatterbox._audio_prompt_url:
            payload["audio_prompt_url"] = self._chatterbox._audio_prompt_url

        timeout = httpx.Timeout(connect=8.0, read=55.0, write=10.0, pool=8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._chatterbox._base_url}/synthesize",
                json=payload,
            )
            response.raise_for_status()
            pcm16 = response.content

        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._chatterbox.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )
        # Small pre-roll avoids clipping the first phoneme on SIP/WebRTC playout.
        output_emitter.push(b"\x00\x00" * int(self._chatterbox.sample_rate * 0.10))
        output_emitter.push(pcm16)
        output_emitter.flush()
