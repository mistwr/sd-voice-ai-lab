from __future__ import annotations

import httpx

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid


class KokoroRemoteTTS(tts.TTS):
    def __init__(self, base_url: str, sample_rate: int = 24000) -> None:
        self._base_url = base_url.rstrip("/")
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

    @property
    def provider(self) -> str:
        return "LUMIN Kokoro PT-PT self-hosted"

    @property
    def model(self) -> str:
        return "kokoro-eu-pt"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return KokoroChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        return None


class KokoroChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: KokoroRemoteTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=conn_options,
        )
        self._kokoro = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        timeout = httpx.Timeout(connect=8.0, read=45.0, write=10.0, pool=8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._kokoro._base_url}/synthesize",
                json={"text": self._input_text},
            )
            response.raise_for_status()
            pcm16 = response.content

        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._kokoro.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )
        output_emitter.push(b"\x00\x00" * int(self._kokoro.sample_rate * 0.10))
        output_emitter.push(pcm16)
        output_emitter.flush()
