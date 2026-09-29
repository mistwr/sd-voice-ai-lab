from __future__ import annotations

import httpx

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid


class PiperCatalogRemoteTTS(tts.TTS):
    def __init__(self, base_url: str, voice_key: str, sample_rate: int = 24000) -> None:
        self._base_url = base_url.rstrip("/")
        self._voice_key = voice_key
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

    @property
    def provider(self) -> str:
        return "LUMIN Piper Voice Catalog"

    @property
    def model(self) -> str:
        return self._voice_key

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return PiperCatalogChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        return None


class PiperCatalogChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: PiperCatalogRemoteTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=conn_options,
        )
        self._catalog = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        timeout = httpx.Timeout(connect=8.0, read=90.0, write=10.0, pool=8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._catalog._base_url}/synthesize",
                json={
                    "text": self._input_text,
                    "voice": self._catalog._voice_key,
                },
            )
            response.raise_for_status()
            pcm16 = response.content

        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._catalog.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )
        output_emitter.push(b"\x00\x00" * int(self._catalog.sample_rate * 0.10))
        output_emitter.push(pcm16)
        output_emitter.flush()
