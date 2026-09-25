"""
Local Piper TTS adapter for LiveKit Agents.

Uses the open-source Piper engine and a local ONNX voice model. No TTS API
request is made when this adapter is active.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import onnxruntime
from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid
from piper import PiperVoice, SynthesisConfig
from piper.config import PiperConfig


def _prepare_ptpt_text(text: str) -> str:
    """Light cleanup for clearer European-Portuguese neural speech."""
    text = re.sub(r"[\*_#`~]+", " ", text)
    text = text.replace("&", " e ")
    text = re.sub(r"\bIA\b", "I A", text)
    text = re.sub(r"\bAI\b", "A I", text)
    text = re.sub(r"\bGPT\b", "G P T", text)
    text = re.sub(r"\bLLM\b", "L L M", text)
    text = re.sub(r"\bWebRTC\b", "Web R T C", text)
    text = re.sub(r"\bCRM\b", "C R M", text)
    text = re.sub(r"\bAPI\b", "A P I", text)
    text = re.sub(r"\bMEO\b", "méu", text)
    text = re.sub(r"\bNOS\b", "nós", text)
    text = re.sub(r"\bDIGI\b", "dígi", text)
    text = re.sub(r"\bLUMIN\b", "Lúmin", text)
    text = re.sub(r"\bSD Dialer\b", "ésse dê dáialer", text)
    # A little extra punctuation helps Piper separate ideas instead of swallowing
    # words in long sales sentences.
    text = re.sub(r"\s*;\s*", ". ", text)
    text = re.sub(r"\s*:\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class PiperTTS(tts.TTS):
    def __init__(
        self,
        model_path: str,
        *,
        config_path: str | None = None,
        length_scale: float = 1.07,
        noise_scale: float = 0.40,
        noise_w_scale: float = 0.46,
        volume: float = 0.94,
        voice: PiperVoice | None = None,
    ) -> None:
        if voice is None:
            model = Path(model_path)
            if not model.exists():
                raise FileNotFoundError(f"Piper model not found: {model}")

            resolved_config = Path(config_path) if config_path else Path(f"{model}.json")
            with open(resolved_config, "r", encoding="utf-8") as config_file:
                config_dict = json.load(config_file)

            # Piper/ONNX defaults can consume every CPU core. That starves the
            # VAD/audio loop while a new idle worker is being prewarmed, which
            # sounds like a bad network connection or can delay speech entirely.
            # Keep synthesis fast but reserve CPU for realtime audio handling.
            ort_threads = max(1, int(os.getenv("PIPER_ORT_THREADS", "2")))
            sess_options = onnxruntime.SessionOptions()
            sess_options.intra_op_num_threads = ort_threads
            sess_options.inter_op_num_threads = 1
            sess_options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL

            self._voice = PiperVoice(
                config=PiperConfig.from_dict(config_dict),
                session=onnxruntime.InferenceSession(
                    str(model),
                    sess_options=sess_options,
                    providers=["CPUExecutionProvider"],
                ),
            )
        else:
            self._voice = voice
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

    def with_profile(
        self,
        *,
        length_scale: float,
        noise_scale: float,
        noise_w_scale: float,
        volume: float,
    ) -> "PiperTTS":
        """Create a lightweight speaking-style variant without reloading the ONNX voice."""
        return PiperTTS(
            "__shared_voice__",
            voice=self._voice,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w_scale,
            volume=volume,
        )

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

        # Telephone/SIP playout can clip the first phoneme when speech begins
        # immediately after a turn. A tiny pre-roll gives the jitter/playout
        # buffer time to open before the first real word.
        pre_roll_samples = int(self._piper.sample_rate * 0.14)
        output_emitter.push(b"\x00\x00" * pre_roll_samples)

        # Synthesize sentence by sentence. This lowers time-to-first-audio and
        # gives telephone speech a natural micro-pause instead of one long block.
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", prepared)
            if s.strip()
        ] or [prepared]

        for index, sentence in enumerate(sentences):
            chunks = await asyncio.to_thread(
                lambda s=sentence: list(
                    self._piper._voice.synthesize(
                        s,
                        syn_config=self._piper._syn_config,
                    )
                )
            )
            for chunk in chunks:
                output_emitter.push(chunk.audio_int16_bytes)

            # About 110 ms between complete ideas: enough to sound human without
            # making the conversation sluggish.
            if index < len(sentences) - 1:
                silence_samples = int(self._piper.sample_rate * 0.11)
                output_emitter.push(b"\x00\x00" * silence_samples)

        output_emitter.flush()
