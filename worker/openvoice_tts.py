from __future__ import annotations

import httpx

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid


class OpenVoiceRemoteTTS(tts.TTS):
    def __init__(
        self,
        base_url: str,
        *,
        audio_prompt_url: str,
        fallback_url: str = "",
        sample_rate: int = 22050,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._audio_prompt_url = audio_prompt_url.strip()
        self._fallback_url = fallback_url.rstrip("/")
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

    @property
    def provider(self) -> str:
        return "LUMIN OpenVoice self-hosted"

    @property
    def model(self) -> str:
        return "openvoice-v2-piper-ptpt"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return OpenVoiceChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        return None


class OpenVoiceChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: OpenVoiceRemoteTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=conn_options,
        )
        self._openvoice = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        if not self._openvoice._audio_prompt_url:
            raise RuntimeError("Custom voice sample is missing")

        timeout = httpx.Timeout(connect=8.0, read=65.0, write=10.0, pool=8.0)
        sample_rate = self._openvoice.sample_rate
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    f"{self._openvoice._base_url}/synthesize",
                    json={
                        "text": self._input_text,
                        "audio_prompt_url": self._openvoice._audio_prompt_url,
                    },
                )
                response.raise_for_status()
                pcm16 = response.content
                sample_rate = int(response.headers.get("X-Sample-Rate") or sample_rate)
            except Exception:
                if not self._openvoice._fallback_url:
                    raise
                fallback_timeout = httpx.Timeout(connect=8.0, read=180.0, write=10.0, pool=8.0)
                async with httpx.AsyncClient(timeout=fallback_timeout) as fallback_client:
                    fallback = await fallback_client.post(
                        f"{self._openvoice._fallback_url}/synthesize",
                        json={
                            "text": self._input_text,
                            "language_id": "pt",
                            "audio_prompt_url": self._openvoice._audio_prompt_url,
                        },
                    )
                    fallback.raise_for_status()
                    pcm16 = fallback.content
                    sample_rate = int(fallback.headers.get("X-Sample-Rate") or 24000)

        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )
        output_emitter.push(b"\x00\x00" * int(sample_rate * 0.10))
        output_emitter.push(pcm16)
        output_emitter.flush()
