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
        model: str = "large-v3-turbo",
        cpu_threads: int = 4,
        workers: int = 1,
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
            beam_size=5,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=False,
            initial_prompt=(
                "Português europeu de chamada telefónica sobre telecomunicações. "
                "Operadoras: MEO, NOS, Vodafone e DIGI. "
                "Frases frequentes: 'A minha operadora é MEO', 'Pago vinte euros por mês', 'Pago oitenta euros'. "
                "Termos: internet, televisão, telemóveis incluídos, mensalidade, fidelização, "
                "contrato, proposta e preço mensal. Valores em euros ditos por extenso ou em algarismos. "
                "Horários naturais: sete da manhã, seis da tarde, dezoito horas e dezanove horas."
            ),
            hotwords=(
                "MEO, NOS, Vodafone, DIGI, Compara Mais, Lumin, "
                "vinte euros, vinte euros por mês, pago vinte euros, "
                "trinta euros, quarenta euros, cinquenta euros, sessenta euros, "
                "setenta euros, oitenta euros, noventa euros, cem euros, "
                "de manhã, à tarde, sete da manhã, seis da tarde, dezoito horas, dezanove horas"
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
