"""
European Portuguese Kokoro TTS adapter for LiveKit Agents.

Uses logus2k/kokoro_tts_eu_pt via the tts_eu_pt package:
- European Portuguese (Lisbon lect)
- Kokoro/StyleTTS2-derived 81.8M model
- 24 kHz mono
- CPU-capable
"""
from __future__ import annotations

import asyncio
import threading
import numpy as np

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid
from tts_eu_pt import TTS as EuPtEngine

_SYNTH_LOCK = threading.Lock()


class KokoroPtPTTTS(tts.TTS):
    def __init__(self) -> None:
        self._engine = EuPtEngine()
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )

    @property
    def model(self) -> str:
        return "kokoro_tts_eu_pt"

    @property
    def provider(self) -> str:
        return "Kokoro PT-PT local"

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
        tts: KokoroPtPTTTS,
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
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._kokoro.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )

        def _synth():
            with _SYNTH_LOCK:
                return self._kokoro._engine.say(self._input_text)

        wav = await asyncio.to_thread(_synth)

        audio = np.asarray(wav, dtype=np.float32).reshape(-1)
        audio = np.clip(audio, -1.0, 1.0)
        pcm16 = (audio * 32767.0).astype(np.int16).tobytes()

        output_emitter.push(pcm16)
        output_emitter.flush()
