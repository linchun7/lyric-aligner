"""Deterministic pre-model sampling for human lyric-boundary calibration.

Human boundary gold is split into two locked populations before backend scoring:

* ``outer``: 30 representative editor cues used only for lexical start/end gold.
  The default pack deliberately includes both single-segment and multi-segment cues.
* ``internal``: 30 different multi-segment cues used only for one canonical
  segment-to-segment boundary per cue.

The internal population excludes every outer case by default.  Selection and
calibration/holdout partitioning depend only on immutable report/canonical lyric
provenance, never on model predictions or acoustic scores.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


HUMAN_GOLD_SELECTION_SCHEMA_VERSION = "human-boundary-gold-selection-2.0"
HUMAN_GOLD_OUTER_POLICY_ID = "pre-model-stratified-outer-30-v1"
HUMAN_GOLD_INTERNAL_POLICY_ID = "pre-model-stratified-internal-30-v1"
DEFAULT_TARGET_COUNT = 30
DEFAULT_HOLDOUT_COUNT = 10
DEFAULT_MAX_PER_TRACK = 3
DEFAULT_OUTER_MULTI_COUNT = 10
DEFAULT_OUTER_HOLDOUT_MULTI_COUNT = 3


class HumanBoundaryGoldError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _positive_int(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HumanBoundaryGoldError(f"{label} is invalid") from exc
    if result <= 0:
        raise HumanBoundaryGoldError(f"{label} must be positive")
    return result


def _nonnegative_int(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HumanBoundaryGoldError(f"{label} is invalid") from exc
    if result < 0:
        raise HumanBoundaryGoldError(f"{label} must be nonnegative")
    return result


def _normalized_space(value: Any) -> str:
    return " ".join(str(value or "").split())


@dataclass(frozen=True)
class GoldCandidate:
    case_id: str
    cue_number: int
    track: str
    start_ms: int
    end_ms: int
    canonical_text: str
    segments: tuple[dict[str, Any], ...]
    internal_boundary_index: int | None
    source_status: str
    source_confidence: str
    source_evidence: str

    @property
    def is_multisegment(self) -> bool:
        return len(self.segments) >= 2

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["segments"] = [dict(segment) for segment in self.segments]
        value["segment_count"] = len(self.segments)
        return value


def candidate_from_report_row(
    row: Mapping[str, Any],
    *,
    lyric_lines_by_track: Mapping[str, Sequence[Mapping[str, Any]]],
) -> GoldCandidate | None:
    """Build one exact-provenance existing-cue candidate.

    A candidate is accepted only when its persisted LRC indices reconstruct the
    canonical report text exactly.  Single-segment candidates are valid for the
    outer pack; only multi-segment candidates can enter the internal pack.
    """

    if not isinstance(row, Mapping) or str(row.get("kind") or "") != "existing":
        return None
    raw_cue = str(row.get("original_cue") or "").strip()
    if not raw_cue.isdigit():
        return None
    cue_number = int(raw_cue)
    start_ms = _nonnegative_int(row.get("start_ms"), label="candidate start_ms")
    end_ms = _nonnegative_int(row.get("end_ms"), label="candidate end_ms")
    if end_ms <= start_ms:
        raise HumanBoundaryGoldError("candidate interval is not monotonic")
    track = str(row.get("track") or "").strip()
    if not track:
        return None

    raw_indices = [part.strip() for part in str(row.get("lrc_indices") or "").split(";")]
    if not raw_indices or any(not part.isdigit() for part in raw_indices):
        return None
    indices = [int(part) for part in raw_indices]
    if len(set(indices)) != len(indices):
        return None

    segments: list[dict[str, Any]] = []
    raw_segments = row.get("canonical_segments_json")
    if raw_segments not in (None, "", "[]"):
        try:
            parsed = json.loads(raw_segments) if isinstance(raw_segments, str) else raw_segments
        except json.JSONDecodeError as exc:
            raise HumanBoundaryGoldError("canonical_segments_json is malformed") from exc
        if not isinstance(parsed, list):
            raise HumanBoundaryGoldError("canonical_segments_json must be a list")
        for segment in parsed:
            if not isinstance(segment, Mapping):
                raise HumanBoundaryGoldError("canonical segment must be an object")
            text = _normalized_space(segment.get("text"))
            if not text:
                raise HumanBoundaryGoldError("canonical segment text is empty")
            lrc_index = _nonnegative_int(segment.get("lrc_index"), label="segment lrc_index")
            segments.append({"lrc_index": lrc_index, "text": text})
    else:
        lyric_lines = lyric_lines_by_track.get(track)
        if lyric_lines is None:
            return None
        line_by_index: dict[int, Mapping[str, Any]] = {}
        for line in lyric_lines:
            if not isinstance(line, Mapping):
                raise HumanBoundaryGoldError("lyric line must be an object")
            index = _nonnegative_int(line.get("index"), label="lyric line index")
            if index in line_by_index:
                raise HumanBoundaryGoldError("lyric line indices must be unique")
            line_by_index[index] = line
        for index in indices:
            line = line_by_index.get(index)
            if line is None:
                return None
            text = _normalized_space(line.get("text"))
            if not text:
                return None
            segments.append({"lrc_index": index, "text": text})

    if not segments or [int(segment["lrc_index"]) for segment in segments] != indices:
        return None
    joined = _normalized_space(" ".join(str(segment["text"]) for segment in segments))
    canonical_text = _normalized_space(row.get("text"))
    if not canonical_text or joined != canonical_text:
        return None

    identity = {
        "cue_number": cue_number,
        "track": track,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "canonical_text": canonical_text,
        "segments": segments,
    }
    case_id = _sha_json(identity)
    boundary_index = (
        1 + (int(case_id[:8], 16) % (len(segments) - 1))
        if len(segments) >= 2
        else None
    )
    return GoldCandidate(
        case_id=case_id,
        cue_number=cue_number,
        track=track,
        start_ms=start_ms,
        end_ms=end_ms,
        canonical_text=canonical_text,
        segments=tuple(dict(segment) for segment in segments),
        internal_boundary_index=boundary_index,
        source_status=str(row.get("status") or ""),
        source_confidence=str(row.get("confidence") or ""),
        source_evidence=str(row.get("evidence") or ""),
    )


def _unique_candidates(
    candidates: Sequence[GoldCandidate], *, excluded_case_ids: Iterable[str] = ()
) -> list[GoldCandidate]:
    excluded = {str(value) for value in excluded_case_ids}
    by_id: dict[str, GoldCandidate] = {}
    for candidate in candidates:
        if candidate.case_id in by_id:
            raise HumanBoundaryGoldError("gold candidate IDs must be unique")
        by_id[candidate.case_id] = candidate
    return [candidate for candidate in by_id.values() if candidate.case_id not in excluded]


def _track_diverse_take(
    candidates: Sequence[GoldCandidate],
    *,
    count: int,
    max_per_track: int,
    selected: list[GoldCandidate],
    track_counts: dict[str, int],
) -> None:
    if count <= 0:
        return
    selected_ids = {candidate.case_id for candidate in selected}
    by_track: dict[str, list[GoldCandidate]] = {}
    for candidate in candidates:
        if candidate.case_id in selected_ids:
            continue
        by_track.setdefault(candidate.track, []).append(candidate)
    track_order = sorted(by_track, key=lambda track: _sha_json({"track": track}))
    for track in track_order:
        by_track[track].sort(key=lambda candidate: candidate.case_id)

    added = 0
    cursors = {track: 0 for track in track_order}
    while added < count:
        progress = False
        for track in track_order:
            if added >= count:
                break
            if track_counts.get(track, 0) >= max_per_track:
                continue
            rows = by_track[track]
            cursor = cursors[track]
            while cursor < len(rows) and rows[cursor].case_id in selected_ids:
                cursor += 1
            cursors[track] = cursor
            if cursor >= len(rows):
                continue
            candidate = rows[cursor]
            cursors[track] = cursor + 1
            selected.append(candidate)
            selected_ids.add(candidate.case_id)
            track_counts[track] = track_counts.get(track, 0) + 1
            added += 1
            progress = True
        if not progress:
            raise HumanBoundaryGoldError("diversity policy cannot produce requested candidate count")


def _partition_selected(
    selected: Sequence[GoldCandidate],
    *,
    holdout_count: int,
    purpose: str,
    outer_holdout_multi_count: int,
) -> tuple[set[str], set[str]]:
    holdout_ids: set[str] = set()
    holdout_tracks: set[str] = set()

    def take(pool: Sequence[GoldCandidate], wanted: int) -> None:
        for candidate in sorted(pool, key=lambda item: item.case_id):
            if len(holdout_ids) >= holdout_count or wanted <= 0:
                break
            if candidate.case_id in holdout_ids or candidate.track in holdout_tracks:
                continue
            holdout_ids.add(candidate.case_id)
            holdout_tracks.add(candidate.track)
            wanted -= 1

    if purpose == "outer":
        multi = [candidate for candidate in selected if candidate.is_multisegment]
        single = [candidate for candidate in selected if not candidate.is_multisegment]
        take(multi, min(outer_holdout_multi_count, holdout_count))
        take(single, holdout_count - len(holdout_ids))

    take(selected, holdout_count - len(holdout_ids))
    if len(holdout_ids) < holdout_count:
        for candidate in sorted(selected, key=lambda item: item.case_id):
            if len(holdout_ids) >= holdout_count:
                break
            holdout_ids.add(candidate.case_id)
            holdout_tracks.add(candidate.track)
    if len(holdout_ids) != holdout_count:
        raise HumanBoundaryGoldError("could not allocate exact holdout count")
    return holdout_ids, holdout_tracks


def select_gold_candidates(
    candidates: Sequence[GoldCandidate],
    *,
    purpose: str,
    excluded_case_ids: Iterable[str] = (),
    target_count: int = DEFAULT_TARGET_COUNT,
    holdout_count: int = DEFAULT_HOLDOUT_COUNT,
    max_per_track: int = DEFAULT_MAX_PER_TRACK,
    outer_multi_count: int = DEFAULT_OUTER_MULTI_COUNT,
    outer_holdout_multi_count: int = DEFAULT_OUTER_HOLDOUT_MULTI_COUNT,
) -> dict[str, Any]:
    """Select and partition one production gold population pre-model."""

    target_count = _positive_int(target_count, label="target_count")
    holdout_count = _positive_int(holdout_count, label="holdout_count")
    max_per_track = _positive_int(max_per_track, label="max_per_track")
    if holdout_count >= target_count:
        raise HumanBoundaryGoldError("holdout_count must be smaller than target_count")
    if purpose not in {"outer", "internal"}:
        raise HumanBoundaryGoldError("purpose must be outer or internal")

    available = _unique_candidates(candidates, excluded_case_ids=excluded_case_ids)
    selected: list[GoldCandidate] = []
    track_counts: dict[str, int] = {}

    if purpose == "outer":
        outer_multi_count = _positive_int(outer_multi_count, label="outer_multi_count")
        if outer_multi_count >= target_count:
            raise HumanBoundaryGoldError("outer_multi_count must be smaller than target_count")
        singles = [candidate for candidate in available if not candidate.is_multisegment]
        multis = [candidate for candidate in available if candidate.is_multisegment]
        _track_diverse_take(
            singles,
            count=target_count - outer_multi_count,
            max_per_track=max_per_track,
            selected=selected,
            track_counts=track_counts,
        )
        _track_diverse_take(
            multis,
            count=outer_multi_count,
            max_per_track=max_per_track,
            selected=selected,
            track_counts=track_counts,
        )
        policy_id = HUMAN_GOLD_OUTER_POLICY_ID
        boundary_kinds = ["start", "end"]
    else:
        multis = [candidate for candidate in available if candidate.is_multisegment]
        _track_diverse_take(
            multis,
            count=target_count,
            max_per_track=max_per_track,
            selected=selected,
            track_counts=track_counts,
        )
        policy_id = HUMAN_GOLD_INTERNAL_POLICY_ID
        boundary_kinds = ["internal"]

    if len(selected) != target_count or len({row.case_id for row in selected}) != target_count:
        raise HumanBoundaryGoldError("selection did not produce exact unique target count")
    if any(track_counts.get(candidate.track, 0) > max_per_track for candidate in selected):
        raise HumanBoundaryGoldError("selection exceeded per-track cap")

    selected_tracks = {candidate.track for candidate in selected}
    minimum_distinct_tracks = (target_count + max_per_track - 1) // max_per_track
    if len(selected_tracks) < minimum_distinct_tracks:
        raise HumanBoundaryGoldError("selected gold pack lacks required track diversity")

    holdout_ids, holdout_tracks = _partition_selected(
        selected,
        holdout_count=holdout_count,
        purpose=purpose,
        outer_holdout_multi_count=outer_holdout_multi_count,
    )
    records: list[dict[str, Any]] = []
    for candidate in selected:
        payload = candidate.to_dict()
        payload["purpose"] = purpose
        payload["boundary_kinds"] = list(boundary_kinds)
        payload["partition"] = "holdout" if candidate.case_id in holdout_ids else "calibration"
        records.append(payload)

    calibration_tracks = {
        row["track"] for row in records if row["partition"] == "calibration"
    }
    if len(calibration_tracks) < 5 or len(holdout_tracks) < 5:
        raise HumanBoundaryGoldError("calibration/holdout partitions must each span at least five tracks")

    selection = {
        "schema_version": HUMAN_GOLD_SELECTION_SCHEMA_VERSION,
        "policy_id": policy_id,
        "purpose": purpose,
        "selection_basis": "immutable_report_and_canonical_provenance_only_no_backend_predictions",
        "target_count": target_count,
        "holdout_count": holdout_count,
        "max_per_track": max_per_track,
        "excluded_case_ids_sha256": _sha_json(sorted(str(value) for value in excluded_case_ids)),
        "distinct_track_count": len(selected_tracks),
        "calibration_distinct_track_count": len(calibration_tracks),
        "holdout_distinct_track_count": len(holdout_tracks),
        "single_segment_count": sum(not candidate.is_multisegment for candidate in selected),
        "multi_segment_count": sum(candidate.is_multisegment for candidate in selected),
        "records": records,
    }
    selection["selection_sha256"] = _sha_json(selection)
    return selection


def validate_audited_selection(
    selection: Mapping[str, Any],
    audited_records: Sequence[Mapping[str, Any]],
) -> None:
    """Ensure an audit cannot rewrite any locked sampling/provenance identity."""

    if selection.get("schema_version") != HUMAN_GOLD_SELECTION_SCHEMA_VERSION:
        raise HumanBoundaryGoldError("unsupported human-gold selection schema")
    selected = selection.get("records")
    if not isinstance(selected, list) or len(selected) != len(audited_records):
        raise HumanBoundaryGoldError("audited record count does not match locked selection")
    immutable_keys = (
        "case_id",
        "cue_number",
        "track",
        "start_ms",
        "end_ms",
        "canonical_text",
        "segments",
        "segment_count",
        "internal_boundary_index",
        "purpose",
        "boundary_kinds",
        "partition",
    )
    for locked, audited in zip(selected, audited_records):
        if not isinstance(locked, Mapping) or not isinstance(audited, Mapping):
            raise HumanBoundaryGoldError("human-gold record must be an object")
        for key in immutable_keys:
            if audited.get(key) != locked.get(key):
                raise HumanBoundaryGoldError(f"audited record changed locked field {key}")
        for forbidden in ("prediction_ms", "backend_id", "model_id", "model_revision"):
            if forbidden in audited:
                raise HumanBoundaryGoldError("backend predictions must not be embedded in human gold")
