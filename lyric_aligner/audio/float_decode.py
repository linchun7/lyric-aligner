"""Floating-point source decoding without an intermediate PCM16 saturation."""
from __future__ import annotations

from pathlib import Path

FLOAT_DECODE_POLICY_ID = "pyav-float32-mono-peak-safe-2026-09-08-v1"


def decode_float_audio(path: Path, *, sampling_rate: int = 16000):
    """Decode/resample in float, then apply one gain only when peak exceeds 1.

    Gain preserves waveform shape; per-sample clipping does not. The sample
    clock and channel rematrix use libav just as the existing ASR decoder.
    No signal-dependent cropping, silence trimming or frame dropping occurs.
    Optional ASR dependencies are loaded only when this path is requested.
    """
    import av
    import numpy as np

    if type(sampling_rate) is not int or sampling_rate <= 0:
        raise ValueError("sampling_rate must be a positive integer")
    resampler = av.audio.resampler.AudioResampler(format="fltp", layout="mono", rate=sampling_rate)
    chunks = []
    with av.open(str(path), mode="r", metadata_errors="ignore") as container:
        for frame in container.decode(audio=0):
            frame.pts = None
            for converted in resampler.resample(frame):
                chunks.append(converted.to_ndarray().reshape(-1))
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().reshape(-1))
    audio = np.concatenate(chunks).astype(np.float32, copy=False) if chunks else np.empty(0, dtype=np.float32)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("decoded source must contain finite nonempty audio")
    peak = float(np.max(np.abs(audio)))
    gain = 1.0 / peak if peak > 1.0 else 1.0
    over_range = int(np.count_nonzero(np.abs(audio) > 1.0))
    if gain != 1.0:
        audio = (audio * np.float32(gain)).astype(np.float32, copy=False)
    return audio, {"policy_id": FLOAT_DECODE_POLICY_ID, "sample_rate": sampling_rate,
        "sample_count": len(audio), "decoded_peak_before_gain": peak, "applied_gain": gain,
        "samples_outside_unit_range_before_gain": over_range,
        "peak_after_gain": float(np.max(np.abs(audio))), "per_sample_clipping_applied": False}
