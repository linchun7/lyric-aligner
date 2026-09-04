"""Conservative effective-content bounds for mix audio.

The physical/container duration is retained for provenance. Effective content
may end earlier only with strict evidence: a sufficiently long digital-zero tail,
or an independently shorter decodable audio-stream duration followed only by
zero frames. No near-silence/noise threshold is used, so ordinary fades and
room/noise tails remain part of the mix.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import soundfile as sf


DECODABLE_DURATION_PROBE_TOLERANCE_SECONDS = 0.005


@dataclass(frozen=True)
class AudioContentExtent:
    full_duration: float
    content_end: float
    trailing_digital_silence: float
    trimmed: bool


def _probe_audio_stream_duration(path: Path) -> float | None:
    """Return ffprobe's first-audio-stream duration when it is usable."""

    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

    candidates: list[object] = []
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if isinstance(streams, list) and streams and isinstance(streams[0], dict):
        candidates.append(streams[0].get("duration"))
    format_payload = payload.get("format") if isinstance(payload, dict) else None
    if isinstance(format_payload, dict):
        candidates.append(format_payload.get("duration"))
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    return None


def apply_content_end_override(
    extent: AudioContentExtent,
    override_path: Path | str,
    *,
    expected_audio_sha256: str,
) -> AudioContentExtent:
    """Apply a fingerprint-bound explicit content end that may only shorten audio.

    This is intentionally task-scoped.  It is for cases where QA has proven that
    physical audio after the program end is detached export residue (for example,
    a long digital-zero gap followed by a short orphaned audio island).  It never
    extends the automatically detected content end.
    """

    path = Path(override_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"mix content extent override is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("mix content extent override must contain a JSON object")
    if payload.get("schema_version") != "mix-content-extent-1.0":
        raise ValueError("mix content extent override has unsupported schema_version")
    audio_sha = str(payload.get("audio_sha256") or "").lower()
    if audio_sha != str(expected_audio_sha256).lower():
        raise ValueError("mix content extent override audio_sha256 does not match task audio")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("mix content extent override requires a non-empty reason")
    value = payload.get("content_end_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("mix content extent override content_end_seconds must be numeric")
    content_end = float(value)
    if not math.isfinite(content_end) or content_end <= 0:
        raise ValueError("mix content extent override content_end_seconds must be positive and finite")
    if content_end > extent.content_end + 1e-6:
        raise ValueError("mix content extent override may only shorten automatic content_end")
    return AudioContentExtent(
        full_duration=extent.full_duration,
        content_end=content_end,
        trailing_digital_silence=extent.trailing_digital_silence,
        trimmed=extent.trimmed or content_end < extent.full_duration - 1e-9,
    )


def detect_audio_content_extent(
    path: Path | str,
    *,
    min_trailing_digital_silence_seconds: float = 30.0,
    block_seconds: float = 10.0,
) -> AudioContentExtent:
    """Return a conservative effective end bounded by decodable audio duration.

    Only samples that decode to exact zero qualify as digital silence.  A short
    genuine zero tail is preserved.  Separately, if SoundFile exposes extra
    zero-only frames beyond ffprobe's first audio-stream duration, that suffix is
    treated as decoder/container padding rather than usable mix time.  Conflicting
    probes never truncate nonzero decoded content.
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
            reported_full_duration = frame_count / sample_rate
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
    full_duration = float(reported_full_duration)
    trailing = max(0.0, full_duration - content_end_raw)
    content_end = (
        content_end_raw
        if trailing >= min_trailing_digital_silence_seconds
        else full_duration
    )

    probed_duration = _probe_audio_stream_duration(resolved)
    tolerance = DECODABLE_DURATION_PROBE_TOLERANCE_SECONDS
    if probed_duration is not None and probed_duration < full_duration - tolerance:
        if content_end_raw > probed_duration + tolerance:
            raise ValueError(
                "audio duration probes disagree: ffprobe ends before nonzero decoded content"
            )
        # Preserve the physical/container duration for provenance.  Only constrain
        # effective content time, and never before any decoded nonzero sample.
        decodable_end = max(content_end_raw, probed_duration)
        content_end = min(content_end, decodable_end)

    trimmed = content_end < full_duration - 1e-9
    return AudioContentExtent(
        full_duration=full_duration,
        content_end=float(content_end),
        trailing_digital_silence=float(trailing),
        trimmed=bool(trimmed),
    )
