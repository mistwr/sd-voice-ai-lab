"""
Local Piper TTS adapter for LiveKit Agents.

Uses the open-source Piper engine and a local ONNX voice model. No TTS API
request is made when this adapter is active.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid
from piper import PiperVoice, SynthesisConfig


def _prepare_ptpt_text(text: str) -> str:
    """Light cleanup for clearer European-Portuguese neural speech."""
    text = re.sub(r"[\*_#`~]+", " ", text)
    text = text.replace("&", " e ")
    text = re.sub(r"\bIA\b", "I A", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAI\b", "A I", text, flags=re.IGNORECASE)
    text = re.sub(r"\bGPT\b", "G P T", text, flags=re.IGNORECASE)
    text = re.sub(r"\bLLM\b", "L L M", text, flags=re.IGNORECASE)
    text = re.sub(r"\bWebRTC\b", "Web R T C", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class PiperTTS(tts.TTS):
    def __init__(
        self,
        model_path: str,
        *,
        config_path: str | None = None,
        length_scale: float = 1.02,
        noise_scale: float = 0.52,
        noise_w_scale: float = 0.62,
        volume: float = 0.96,
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
            noise_scale=noise_scale,
            noise_w_scale=noise_w_scale,
            normalize_audio=True,
            volume=volume,
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
        return "Piper PT-PT tuned"

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

        prepared = _prepare_ptpt_text(self._input_text)
        chunks = await asyncio.to_thread(
            lambda: list(
                self._piper._voice.synthesize(
                    prepared,
                    syn_config=self._piper._syn_config,
                )
            )
        )

        for chunk in chunks:
            output_emitter.push(chunk.audio_int16_bytes)

        output_emitter.flush()
