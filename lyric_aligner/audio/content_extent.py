"""Conservative effective-content bounds for mix audio.

The physical container duration is retained for provenance.  Only a sufficiently
long *digital-zero* tail is excluded from the final occurrence's search window.
No near-silence/noise threshold is used, so ordinary fades and room/noise tails
remain part of the mix.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass(frozen=True)
class AudioContentExtent:
    full_duration: float
    content_end: float
    trailing_digital_silence: float
    trimmed: bool


def detect_audio_content_extent(
    path: Path | str,
    *,
    min_trailing_digital_silence_seconds: float = 30.0,
    block_seconds: float = 10.0,
) -> AudioContentExtent:
    """Return a conservative effective end while preserving full duration.

    Only samples that decode to exact zero qualify as digital silence.  If the
    trailing zero run is shorter than ``min_trailing_digital_silence_seconds``,
    the effective content end remains the physical file duration.
    """

    if min_trailing_digital_silence_seconds < 0:
        raise ValueError("min_trailing_digital_silence_seconds must be non-negative")
    if block_seconds <= 0:
        raise ValueError("block_seconds must be positive")

    resolved = Path(path)
    try:
        with sf.SoundFile(str(resolved), mode="r") as audio:
            sample_rate = int(audio.samplerate)
            frame_count = int(len(audio))
            if sample_rate <= 0 or frame_count <= 0:
                raise ValueError("audio must contain positive sample rate and frames")
            full_duration = frame_count / sample_rate
            block_frames = max(1, round(block_seconds * sample_rate))
            cursor = frame_count
            last_nonzero_frame: int | None = None
            while cursor > 0:
                start = max(0, cursor - block_frames)
                audio.seek(start)
                block = audio.read(cursor - start, dtype="float32", always_2d=True)
                if block.size:
                    nonzero_frames = np.flatnonzero(np.any(block != 0.0, axis=1))
                    if nonzero_frames.size:
                        last_nonzero_frame = start + int(nonzero_frames[-1])
                        break
                cursor = start
    except (RuntimeError, OSError) as exc:
        raise ValueError(f"cannot inspect audio content extent: {resolved}: {exc}") from exc

    content_end_raw = 0.0 if last_nonzero_frame is None else (last_nonzero_frame + 1) / sample_rate
    trailing = max(0.0, full_duration - content_end_raw)
    trimmed = trailing >= min_trailing_digital_silence_seconds
    content_end = content_end_raw if trimmed else full_duration
    return AudioContentExtent(
        full_duration=float(full_duration),
        content_end=float(content_end),
        trailing_digital_silence=float(trailing),
        trimmed=bool(trimmed),
    )
