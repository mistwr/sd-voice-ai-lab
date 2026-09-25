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
    text = re.sub(r"\bCRM\b", "C R M", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAPI\b", "A P I", text, flags=re.IGNORECASE)
    text = re.sub(r"\bMEO\b", "méu", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNOS\b", "nós", text, flags=re.IGNORECASE)
    text = re.sub(r"\bDIGI\b", "dígi", text, flags=re.IGNORECASE)
    text = re.sub(r"\bLUMIN\b", "Lúmin", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSD Dialer\b", "ésse dê dáialer", text, flags=re.IGNORECASE)
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
            self._voice = PiperVoice.load(
                model,
                config_path=config_path,
                use_cuda=False,
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

        # SIP/telephone playout can shave the first consonant when audio starts
        # exactly at a turn boundary. Keep a tiny buffer-opening pre-roll.
        pre_roll_samples = int(self._piper.sample_rate * 0.10)
        output_emitter.push(b"\x00\x00" * pre_roll_samples)

        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", prepared)
            if s.strip()
        ] or [prepared]

        # Do not collect the whole sentence before playback. Produce Piper audio
        # in a worker thread and forward each chunk immediately to LiveKit.
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        done = object()

        def produce() -> None:
            try:
                for index, sentence in enumerate(sentences):
                    for chunk in self._piper._voice.synthesize(
                        sentence,
                        syn_config=self._piper._syn_config,
                    ):
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            chunk.audio_int16_bytes,
                        )

                    if index < len(sentences) - 1:
                        silence_samples = int(self._piper.sample_rate * 0.08)
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            b"\x00\x00" * silence_samples,
                        )
            except BaseException as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, done)

        producer = asyncio.create_task(asyncio.to_thread(produce))

        while True:
            item = await queue.get()
            if item is done:
                break
            if isinstance(item, BaseException):
                raise item
            output_emitter.push(item)

        await producer
        output_emitter.flush()
