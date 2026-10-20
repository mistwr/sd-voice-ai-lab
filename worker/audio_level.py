"""PCM16 output gain and soft limiting shared by the voice adapters."""
from __future__ import annotations

import numpy as np

# A roughly 3 dB increase makes speech more present. The soft limiter catches
# peaks before the SIP codec or handset clips them.
_GAIN = 10.0 ** (3.0 / 20.0)
_KNEE = 0.82
_CEILING = 0.96


def boost_pcm16(audio: bytes) -> bytes:
    """Raise mono little-endian PCM16 speech level while keeping peaks controlled."""
    usable = len(audio) & ~1
    if usable == 0:
        return audio

    samples = np.frombuffer(audio[:usable], dtype="<i2").astype(np.float32)
    samples *= 1.0 / 32768.0
    samples *= _GAIN

    magnitude = np.abs(samples)
    over_knee = magnitude > _KNEE
    if np.any(over_knee):
        magnitude[over_knee] = _KNEE + (_CEILING - _KNEE) * np.tanh(
            (magnitude[over_knee] - _KNEE) / (_CEILING - _KNEE)
        )
        samples = np.copysign(magnitude, samples)

    np.clip(samples, -_CEILING, _CEILING, out=samples)
    pcm16 = np.rint(samples * 32768.0)
    np.clip(pcm16, -32768.0, 32767.0, out=pcm16)
    result = pcm16.astype("<i2").tobytes()
    return result + audio[usable:]
