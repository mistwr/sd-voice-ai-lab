"""Offline Portuguese speech recognition for LiveKit Agents.

Uses faster-whisper on CPU with an int8 model. No hosted STT inference API is
called when this adapter is active.
"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np
from livekit import rtc
from livekit.agents import stt
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, shortuuid

logger = logging.getLogger("lumin-local-whisper")


class LocalWhisperSTT(stt.STT):
    def __init__(
        self,
        *,
        model: str = "small",
        cpu_threads: int = 2,
        workers: int = 2,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
                diarization=False,
                offline_recognize=True,
            )
        )
        from faster_whisper import WhisperModel

        self._model_name = model
        self._model = WhisperModel(
            model,
            device="cpu",
            compute_type="int8",
            cpu_threads=cpu_threads,
            num_workers=workers,
        )
        logger.info(
            "local Portuguese Whisper model loaded",
            extra={"model": model, "device": "cpu", "compute_type": "int8"},
        )

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def provider(self) -> str:
        return "faster-whisper-local"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        frames = [buffer] if isinstance(buffer, rtc.AudioFrame) else list(buffer)
        request_id = shortuuid()
        if not frames:
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id=request_id,
            )

        audio = self._to_mono_16khz(frames)
        if audio.size == 0:
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id=request_id,
            )

        started = time.perf_counter()
        text = await asyncio.to_thread(self._transcribe, audio)
        logger.info(
            "local Whisper transcription complete",
            extra={
                "audio_seconds": round(sum(frame.duration for frame in frames), 2),
                "processing_ms": round((time.perf_counter() - started) * 1000),
                "transcript_chars": len(text),
            },
        )
        alternatives = (
            [stt.SpeechData(language="pt", text=text)]
            if text
            else []
        )
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=request_id,
            alternatives=alternatives,
        )

    def _transcribe(self, audio: np.ndarray) -> str:
        # faster-whisper returns a lazy generator; consume it here so CTranslate2
        # runs off the asyncio event loop rather than blocking real-time audio.
        segments, _ = self._model.transcribe(
            audio,
            language="pt",
            task="transcribe",
            beam_size=1,
            best_of=1,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=False,
            initial_prompt=(
                "Português europeu. Conversa telefónica sobre telecomunicações, "
                "energia, tarifas, faturas, contratos, internet, telemóvel e televisão. "
                "Marcas: MEO, NOS, DIGI e Lumin."
            ),
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    @staticmethod
    def _to_mono_16khz(frames: list[rtc.AudioFrame]) -> np.ndarray:
        sample_rate = frames[0].sample_rate
        num_channels = frames[0].num_channels
        if any(
            frame.sample_rate != sample_rate or frame.num_channels != num_channels
            for frame in frames
        ):
            raise ValueError("Audio format changed during one recognized utterance")

        resampled_frames = frames
        if sample_rate != 16000:
            resampler = rtc.AudioResampler(
                input_rate=sample_rate,
                output_rate=16000,
                num_channels=num_channels,
                quality=rtc.AudioResamplerQuality.QUICK,
            )
            resampled_frames = []
            for frame in frames:
                resampled_frames.extend(resampler.push(frame))
            resampled_frames.extend(resampler.flush())

        if not resampled_frames:
            return np.empty(0, dtype=np.float32)

        samples = np.concatenate(
            [np.frombuffer(frame.data, dtype=np.int16) for frame in resampled_frames]
        )
        if num_channels > 1:
            samples = samples.reshape(-1, num_channels).mean(axis=1)
        return samples.astype(np.float32) / 32768.0
