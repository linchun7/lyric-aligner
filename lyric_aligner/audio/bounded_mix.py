"""Conservative bounded mix decoding with terminal compressed-audio slop handling."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import librosa


DEFAULT_TERMINAL_DECODE_TOLERANCE_SECONDS = 0.005


@dataclass(frozen=True)
class BoundedMixDecode:
    audio: Any
    decode_start: float
    effective_mix_end: float
    terminal_clamped: bool
    terminal_shortfall_seconds: float


def load_bounded_mix(
    path: Path | str,
    *,
    sr: int,
    mix_start: float,
    mix_end: float,
    full_mix_duration: float,
    padding_seconds: float,
    terminal_tolerance_seconds: float = DEFAULT_TERMINAL_DECODE_TOLERANCE_SECONDS,
) -> BoundedMixDecode:
    """Decode a bounded mix interval without padding missing audio.

    A compressed container can report a physical duration a few resampled samples
    longer than a bounded decoder returns at the exact file tail.  Such a short
    read is accepted only when the requested interval itself reaches the physical
    tail and the shortfall is within ``terminal_tolerance_seconds``.  The caller
    receives the real decodable end and must use that end for downstream timing.

    Mid-file short reads and larger terminal shortfalls remain hard failures.
    """

    if sr <= 0:
        raise ValueError("sr must be positive")
    if mix_start < 0 or mix_end <= mix_start:
        raise ValueError("invalid bounded mix interval")
    if full_mix_duration <= 0 or mix_end > full_mix_duration + 1e-9:
        raise ValueError("bounded mix interval exceeds full duration")
    if padding_seconds < 0:
        raise ValueError("padding_seconds must be non-negative")
    if terminal_tolerance_seconds < 0:
        raise ValueError("terminal_tolerance_seconds must be non-negative")

    decode_start = max(0.0, mix_start - padding_seconds)
    decode_end = min(full_mix_duration, mix_end + padding_seconds)
    audio, _ = librosa.load(
        path,
        sr=sr,
        mono=True,
        offset=decode_start,
        duration=max(0.0, decode_end - decode_start),
    )

    required_samples = int(math.ceil((mix_end - decode_start) * sr))
    # Preserve the historical one-sample rounding tolerance everywhere.
    if len(audio) + 1 >= required_samples:
        return BoundedMixDecode(
            audio=audio,
            decode_start=decode_start,
            effective_mix_end=mix_end,
            terminal_clamped=False,
            terminal_shortfall_seconds=0.0,
        )

    actual_end = decode_start + (len(audio) / sr)
    shortfall = max(0.0, mix_end - actual_end)
    reaches_physical_tail = (
        full_mix_duration - mix_end <= terminal_tolerance_seconds + (1.0 / sr)
    )
    if reaches_physical_tail and shortfall <= terminal_tolerance_seconds + (1.0 / sr):
        if actual_end <= mix_start:
            raise ValueError("terminal bounded mix decode contains no requested interval")
        return BoundedMixDecode(
            audio=audio,
            decode_start=decode_start,
            effective_mix_end=actual_end,
            terminal_clamped=True,
            terminal_shortfall_seconds=shortfall,
        )

    raise ValueError("bounded mix decode ended before requested interval")
