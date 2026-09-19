"""Orthogonal structural-event detector candidates for strict evaluation.

These detectors are intentionally evaluation-only.  They do not mutate subtitle
or Max production authority.  The candidate uses facts independent of acoustic
source retrieval:

* editor reorder: source-mapped file-order SRT cues fall materially behind the
  previously observed mapped timeline frontier; unmapped overlays never create
  reorder authority;
* detached tail: a long exact-digital-zero gap near the back of the container is
  followed by a short isolated active island.

Promotion into production requires the locked calibration/blind protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.srt import parse_srt_strict


@dataclass(frozen=True)
class StructuralDetectorPolicy:
    reorder_backstep_tolerance_ms: float = 500.0
    detached_tail_scan_seconds: float = 900.0
    detached_tail_min_zero_gap_seconds: float = 2.0
    detached_tail_min_active_seconds: float = 0.25
    detached_tail_max_island_seconds: float = 30.0
    detached_tail_min_gap_start_fraction: float = 0.40
    audio_block_seconds: float = 10.0

    def validate(self) -> None:
        values = {
            "reorder_backstep_tolerance_ms": self.reorder_backstep_tolerance_ms,
            "detached_tail_scan_seconds": self.detached_tail_scan_seconds,
            "detached_tail_min_zero_gap_seconds": self.detached_tail_min_zero_gap_seconds,
            "detached_tail_min_active_seconds": self.detached_tail_min_active_seconds,
            "detached_tail_max_island_seconds": self.detached_tail_max_island_seconds,
            "audio_block_seconds": self.audio_block_seconds,
        }
        for name, value in values.items():
            if not np.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
        fraction = float(self.detached_tail_min_gap_start_fraction)
        if not np.isfinite(fraction) or not 0.0 <= fraction < 1.0:
            raise ValueError("detached_tail_min_gap_start_fraction must be in [0, 1)")
        if self.detached_tail_max_island_seconds < self.detached_tail_min_active_seconds:
            raise ValueError("detached tail max island must be >= minimum active duration")


DEFAULT_STRUCTURAL_DETECTOR_POLICY = StructuralDetectorPolicy()


def detect_editor_reorder_events(
    srt_path: Path | str,
    *,
    mapped_cue_positions: set[int] | frozenset[int] | None,
    policy: StructuralDetectorPolicy = DEFAULT_STRUCTURAL_DETECTOR_POLICY,
) -> list[dict]:
    """Detect mapped file-order regions whose cue times materially move backwards.

    ``mapped_cue_positions`` is a zero-based set of editor cue positions that have
    an existing source/occurrence mapping.  Unmapped overlays, title cards and
    voice-over cues are deliberately unable to establish or trigger reorder
    authority.  Once a mapped cue triggers a backward jump, however, all editor
    cues up to mapped chronology recovery remain inside the event interval so the
    returned region still covers unmapped material interleaved in that block.
    """

    policy.validate()
    cues = parse_srt_strict(Path(srt_path))
    if mapped_cue_positions is None:
        raise ValueError("mapped_cue_positions is required for reorder detection")
    mapped = {int(position) for position in mapped_cue_positions}
    if any(position < 0 or position >= len(cues) for position in mapped):
        raise ValueError("mapped_cue_positions contains an out-of-range cue position")
    mapped_positions = sorted(mapped)
    if len(mapped_positions) < 2:
        return []

    tolerance = float(policy.reorder_backstep_tolerance_ms)
    first_position = mapped_positions[0]
    frontier = float(cues[first_position].start_ms)
    events: list[dict] = []
    mapped_index = 1
    while mapped_index < len(mapped_positions):
        position = mapped_positions[mapped_index]
        cue = cues[position]
        if float(cue.start_ms) >= frontier - tolerance:
            frontier = max(frontier, float(cue.start_ms))
            mapped_index += 1
            continue

        frozen_frontier = frontier
        block_start = position
        recovery_position: int | None = None
        recovery_mapped_index: int | None = None
        search_index = mapped_index + 1
        while search_index < len(mapped_positions):
            candidate_position = mapped_positions[search_index]
            candidate = cues[candidate_position]
            if float(candidate.start_ms) >= frozen_frontier - tolerance:
                recovery_position = candidate_position
                recovery_mapped_index = search_index
                break
            search_index += 1

        block_end_exclusive = recovery_position if recovery_position is not None else len(cues)
        block = cues[block_start:block_end_exclusive]
        event_start = min(float(row.start_ms) for row in block)
        event_end = max(float(row.end_ms) for row in block)
        if event_end > event_start:
            events.append(
                {
                    "kind": "reorder",
                    "start_ms": event_start,
                    "end_ms": event_end,
                }
            )

        if recovery_position is None or recovery_mapped_index is None:
            break
        frontier = max(frozen_frontier, float(cues[recovery_position].start_ms))
        mapped_index = recovery_mapped_index + 1

    return events


def _exact_activity_runs(
    audio_path: Path | str,
    *,
    scan_seconds: float,
    block_seconds: float,
) -> tuple[list[tuple[bool, int, int]], int, int]:
    """Return exact-zero/activity runs over a bounded tail window.

    Run tuples are ``(active, start_frame, end_frame)`` with an exclusive end.
    Exact zero is deliberately conservative and matches the production content
    extent semantics; no near-silence energy threshold is introduced here.
    """

    path = Path(audio_path)
    try:
        with sf.SoundFile(str(path), mode="r") as audio:
            sample_rate = int(audio.samplerate)
            frame_count = int(len(audio))
            if sample_rate <= 0 or frame_count <= 0:
                raise ValueError("audio must contain positive sample rate and frames")
            scan_frames = max(1, int(round(float(scan_seconds) * sample_rate)))
            start_frame = max(0, frame_count - scan_frames)
            block_frames = max(1, int(round(float(block_seconds) * sample_rate)))
            start_frame = int(audio.seek(start_frame))

            runs: list[tuple[bool, int, int]] = []
            cursor = start_frame
            current_state: bool | None = None
            run_start = start_frame
            while cursor < frame_count:
                count = min(block_frames, frame_count - cursor)
                block = audio.read(count, dtype="float32", always_2d=True)
                actual_count = int(len(block))
                if actual_count <= 0:
                    frame_count = cursor
                    break
                active = np.any(block != 0.0, axis=1)

                segment_start = 0
                if current_state is None:
                    current_state = bool(active[0])
                    run_start = cursor
                elif bool(active[0]) != current_state:
                    runs.append((current_state, run_start, cursor))
                    current_state = bool(active[0])
                    run_start = cursor

                transitions = np.flatnonzero(active[1:] != active[:-1]) + 1
                for offset in transitions.tolist():
                    absolute = cursor + int(offset)
                    runs.append((bool(active[segment_start]), run_start, absolute))
                    current_state = bool(active[offset])
                    run_start = absolute
                    segment_start = int(offset)
                cursor += actual_count
                if actual_count < count:
                    frame_count = cursor
                    break

            if current_state is not None and run_start < frame_count:
                runs.append((current_state, run_start, frame_count))
    except (RuntimeError, OSError) as exc:
        raise ValueError(f"cannot inspect structural audio tail: {path}: {exc}") from exc

    return runs, sample_rate, frame_count


def detect_detached_tail_events(
    audio_path: Path | str,
    *,
    policy: StructuralDetectorPolicy = DEFAULT_STRUCTURAL_DETECTOR_POLICY,
) -> list[dict]:
    """Detect short active islands after a long exact-zero late-program gap."""

    policy.validate()
    runs, sample_rate, frame_count = _exact_activity_runs(
        audio_path,
        scan_seconds=float(policy.detached_tail_scan_seconds),
        block_seconds=float(policy.audio_block_seconds),
    )
    if len(runs) < 3:
        return []

    min_gap_frames = int(round(policy.detached_tail_min_zero_gap_seconds * sample_rate))
    min_active_frames = int(round(policy.detached_tail_min_active_seconds * sample_rate))
    max_island_frames = int(round(policy.detached_tail_max_island_seconds * sample_rate))
    min_gap_start = int(round(policy.detached_tail_min_gap_start_fraction * frame_count))

    events: list[dict] = []
    for index, (active, gap_start, gap_end) in enumerate(runs):
        if active:
            continue
        if gap_end - gap_start < min_gap_frames or gap_start < min_gap_start:
            continue
        if not any(prior_active for prior_active, _, _ in runs[:index]):
            continue

        first_active: int | None = None
        last_active: int | None = None
        active_frames = 0
        cursor = index + 1
        while cursor < len(runs):
            row_active, row_start, row_end = runs[cursor]
            row_len = row_end - row_start
            if not row_active and row_len >= min_gap_frames:
                break
            if row_active:
                if first_active is None:
                    first_active = row_start
                last_active = row_end
                active_frames += row_len
            cursor += 1

        if first_active is None or last_active is None:
            continue
        island_span = last_active - first_active
        if active_frames < min_active_frames:
            continue
        if island_span <= 0 or island_span > max_island_frames:
            continue

        events.append(
            {
                "kind": "detached_tail",
                "start_ms": first_active * 1000.0 / sample_rate,
                "end_ms": last_active * 1000.0 / sample_rate,
            }
        )

    return events


def detect_structural_events(
    *,
    editor_srt: Path | str | None = None,
    mapped_cue_positions: set[int] | frozenset[int] | None = None,
    audio_path: Path | str | None = None,
    policy: StructuralDetectorPolicy = DEFAULT_STRUCTURAL_DETECTOR_POLICY,
) -> list[dict]:
    """Run the orthogonal evaluation candidate over whichever inputs exist."""

    policy.validate()
    events: list[dict] = []
    if editor_srt is not None:
        events.extend(
            detect_editor_reorder_events(
                editor_srt,
                mapped_cue_positions=mapped_cue_positions,
                policy=policy,
            )
        )
    if audio_path is not None:
        events.extend(detect_detached_tail_events(audio_path, policy=policy))
    return sorted(
        events,
        key=lambda row: (
            float(row["start_ms"]),
            float(row["end_ms"]),
            str(row["kind"]),
        ),
    )
