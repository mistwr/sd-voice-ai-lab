"""
Local Piper TTS adapter for LiveKit Agents.

Uses the open-source Piper engine and a local ONNX voice model. No TTS API
request is made when this adapter is active.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid
from piper import PiperVoice, SynthesisConfig


class PiperTTS(tts.TTS):
    def __init__(
        self,
        model_path: str,
        *,
        config_path: str | None = None,
        length_scale: float = 0.96,
    ) -> None:
        model = Path(model_path)
        if not model.exists():
            raise FileNotFoundError(f"Piper model not found: {model}")

        self._voice = PiperVoice.load(
            model,
            config_path=config_path,
            use_cuda=False,
        )
        self._syn_config = SynthesisConfig(
            length_scale=length_scale,
            normalize_audio=True,
        )

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._voice.config.sample_rate,
            num_channels=1,
        )

    @property
    def model(self) -> str:
        return "pt_PT-tugao-medium"

    @property
    def provider(self) -> str:
        return "Piper local"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return PiperChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        return None


class PiperChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: PiperTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=conn_options,
        )
        self._piper = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._piper.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )

        # Piper/ONNX is CPU-bound. Run synthesis away from the asyncio loop so
        # microphone/STT handling remains responsive while Lumin is speaking.
        chunks = await asyncio.to_thread(
            lambda: list(
                self._piper._voice.synthesize(
                    self._input_text,
                    syn_config=self._piper._syn_config,
                )
            )
        )

        for chunk in chunks:
            output_emitter.push(chunk.audio_int16_bytes)

        output_emitter.flush()
