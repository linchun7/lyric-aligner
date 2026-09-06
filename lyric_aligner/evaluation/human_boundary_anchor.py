"""Lightweight human-anchor authority projected from a locked benchmark pack.

The full 30-outer + 30-internal human-gold pack remains a benchmark/diagnostic
population.  Production authority may instead use a smaller fixed projection of
that already pre-model-locked population: 12 outer cues and 12 internal cues.

Projection is deliberately blind to audit values and backend predictions.  It
uses only the source pack's immutable locked order/provenance and track identity.
This preserves the methodological benefit of pre-model sampling while reducing
human listening to 24 clips / 36 acoustic boundary points.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from lyric_aligner.timeline.boundary_calibration import BoundaryCalibrationPolicy


HUMAN_ANCHOR_SELECTION_SCHEMA_VERSION = "human-boundary-anchor-selection-1.0"
HUMAN_ANCHOR_SELECTION_POLICY_ID = "locked-benchmark-projection-12outer-12internal-v1"
HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION = "human-boundary-anchor-selection-1.1"
HUMAN_ANCHOR_REPLACEMENT_SELECTION_POLICY_ID = (
    "locked-benchmark-projection-12outer-12internal-question-invalid-replacement-v2"
)
HUMAN_ANCHOR_SELECTION_SCHEMA_VERSIONS = {
    HUMAN_ANCHOR_SELECTION_SCHEMA_VERSION,
    HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION,
}
HUMAN_ANCHOR_GOLD_LEGACY_SCHEMA_VERSION = "human-boundary-gold-anchor-1.0"
HUMAN_ANCHOR_GOLD_SCHEMA_VERSION = "human-boundary-gold-anchor-1.1-audit-provenance"
HUMAN_ANCHOR_GOLD_SCHEMA_VERSIONS = {
    HUMAN_ANCHOR_GOLD_LEGACY_SCHEMA_VERSION,
    HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
}
HUMAN_ANCHOR_GOLD_AUTHORITY = "human_audited_anchor_final_mix_acoustic_boundary"
HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID = "clarity-class-clear50-ambiguous100-v1"
HUMAN_ANCHOR_AUDIT_UI_REVISION = "3.0"
HUMAN_ANCHOR_AUDIT_UX_REVISION = "3.2"

ANCHOR_OUTER_CUE_COUNT = 12
ANCHOR_INTERNAL_CUE_COUNT = 12
ANCHOR_OUTER_SINGLE_COUNT = 8
ANCHOR_OUTER_MULTI_COUNT = 4
ANCHOR_CALIBRATION_COUNT = 8
ANCHOR_HOLDOUT_COUNT = 4
ANCHOR_BOUNDARY_POINTS_PER_KIND = 12
ANCHOR_TOTAL_BOUNDARY_POINTS = 36
ANCHOR_MIN_DISTINCT_TRACKS = 4

# Keep the same timing-error thresholds as the full benchmark.  Only the sample
# counts change.  This is intentionally conservative: on a four-point holdout,
# one catastrophic miss still fails the scope.
HUMAN_ANCHOR_PRODUCTION_POLICY = BoundaryCalibrationPolicy(
    min_calibration_samples=ANCHOR_CALIBRATION_COUNT,
    min_holdout_samples=ANCHOR_HOLDOUT_COUNT,
    min_distinct_tracks=ANCHOR_MIN_DISTINCT_TRACKS,
)


class HumanBoundaryAnchorError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _records(population: Mapping[str, Any], *, purpose: str) -> list[dict[str, Any]]:
    if not isinstance(population, Mapping) or str(population.get("purpose") or "") != purpose:
        raise HumanBoundaryAnchorError(f"source {purpose} population is invalid")
    raw = population.get("records")
    if not isinstance(raw, list) or len(raw) < 12:
        raise HumanBoundaryAnchorError(f"source {purpose} population is too small")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, Mapping):
            raise HumanBoundaryAnchorError("source population record must be an object")
        case_id = str(row.get("case_id") or "")
        track = str(row.get("track") or "").strip()
        if len(case_id) != 64 or case_id in seen or not track:
            raise HumanBoundaryAnchorError("source population case identity is invalid")
        seen.add(case_id)
        records.append(dict(row))
    return records


def _take_prefer_new_tracks(
    rows: Sequence[dict[str, Any]],
    *,
    count: int,
    used_tracks: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Take in locked order, preferring track diversity without using outcomes."""

    used = set(used_tracks or ())
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for prefer_new in (True, False):
        for row in rows:
            if len(selected) >= count:
                break
            case_id = str(row["case_id"])
            track = str(row["track"])
            if case_id in selected_ids:
                continue
            if prefer_new and track in used:
                continue
            selected.append(dict(row))
            selected_ids.add(case_id)
            used.add(track)
        if len(selected) >= count:
            break
    if len(selected) != count:
        raise HumanBoundaryAnchorError("locked projection cannot satisfy requested anchor count")
    return selected


def _partition(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repartition 12 anchors into 8 calibration + 4 blind holdout records.

    Holdout membership depends only on the immutable case hash/track, never on the
    source benchmark partition, human audit values, or model predictions.
    """

    if len(rows) != ANCHOR_BOUNDARY_POINTS_PER_KIND:
        raise HumanBoundaryAnchorError("anchor population must contain exactly 12 cases")
    sorted_rows = sorted(rows, key=lambda row: str(row["case_id"]))
    holdout: list[str] = []
    holdout_tracks: set[str] = set()
    for row in sorted_rows:
        if len(holdout) >= ANCHOR_HOLDOUT_COUNT:
            break
        track = str(row["track"])
        if track in holdout_tracks:
            continue
        holdout.append(str(row["case_id"]))
        holdout_tracks.add(track)
    for row in sorted_rows:
        if len(holdout) >= ANCHOR_HOLDOUT_COUNT:
            break
        case_id = str(row["case_id"])
        if case_id not in holdout:
            holdout.append(case_id)
    if len(holdout) != ANCHOR_HOLDOUT_COUNT:
        raise HumanBoundaryAnchorError("could not allocate anchor holdout")
    holdout_ids = set(holdout)

    output: list[dict[str, Any]] = []
    for index, source in enumerate(rows, start=1):
        row = dict(source)
        row["source_benchmark_partition"] = str(source.get("partition") or "")
        row["partition"] = "holdout" if str(row["case_id"]) in holdout_ids else "calibration"
        row["anchor_selection_index"] = index
        output.append(row)

    calibration = [row for row in output if row["partition"] == "calibration"]
    held = [row for row in output if row["partition"] == "holdout"]
    if len(calibration) != ANCHOR_CALIBRATION_COUNT or len(held) != ANCHOR_HOLDOUT_COUNT:
        raise HumanBoundaryAnchorError("anchor partition sizes are invalid")
    if len({str(row["track"]) for row in calibration}) < ANCHOR_MIN_DISTINCT_TRACKS:
        raise HumanBoundaryAnchorError("anchor calibration partition lacks track diversity")
    if len({str(row["track"]) for row in held}) < ANCHOR_MIN_DISTINCT_TRACKS:
        raise HumanBoundaryAnchorError("anchor holdout partition lacks track diversity")
    return output


def project_locked_anchor_selection(
    full_lock: Mapping[str, Any],
    *,
    question_invalid_case_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Project the fixed 24-clip authority pack from a valid locked full pack.

    The caller is responsible for cryptographically/provenance-validating the full
    pack before calling this function.  The optional exclusions are restricted to
    human-confirmed *question validity* failures (for example, a locked clip that
    does not contain the requested boundary).  Timing values and backend outcomes
    are never accepted, and replacement still follows the immutable locked order.
    """

    populations = full_lock.get("populations") if isinstance(full_lock, Mapping) else None
    if not isinstance(populations, Mapping):
        raise HumanBoundaryAnchorError("source full lock has no populations")
    outer_all = _records(populations.get("outer", {}), purpose="outer")
    internal_all = _records(populations.get("internal", {}), purpose="internal")
    invalid_ids = tuple(sorted(str(value or "").strip() for value in question_invalid_case_ids))
    if any(len(value) != 64 for value in invalid_ids) or len(set(invalid_ids)) != len(invalid_ids):
        raise HumanBoundaryAnchorError("question-invalid case IDs are invalid")
    known_ids = {str(row["case_id"]) for row in (*outer_all, *internal_all)}
    if not set(invalid_ids) <= known_ids:
        raise HumanBoundaryAnchorError("question-invalid case ID is absent from source lock")
    invalid_set = set(invalid_ids)
    outer = [row for row in outer_all if str(row["case_id"]) not in invalid_set]
    internal = [row for row in internal_all if str(row["case_id"]) not in invalid_set]

    singles = [row for row in outer if int(row.get("segment_count") or 0) == 1]
    multis = [row for row in outer if int(row.get("segment_count") or 0) >= 2]
    if len(singles) < ANCHOR_OUTER_SINGLE_COUNT or len(multis) < ANCHOR_OUTER_MULTI_COUNT:
        raise HumanBoundaryAnchorError("source outer population lacks required single/multi coverage")

    # Preserve the first eight locked single-segment cases exactly.  This guarantees
    # that the seven cases already listened to in the current v4 pack remain in the
    # authority subset, while membership still depends only on pre-model lock order.
    selected_singles = [dict(row) for row in singles[:ANCHOR_OUTER_SINGLE_COUNT]]
    used_outer_tracks = {str(row["track"]) for row in selected_singles}
    selected_multis = _take_prefer_new_tracks(
        multis,
        count=ANCHOR_OUTER_MULTI_COUNT,
        used_tracks=used_outer_tracks,
    )
    outer_anchor = _partition([*selected_singles, *selected_multis])

    # Internal records are already a multi-segment, track-diverse locked sequence.
    internal_selected = _take_prefer_new_tracks(internal, count=ANCHOR_INTERNAL_CUE_COUNT)
    internal_anchor = _partition(internal_selected)

    outer_ids = {str(row["case_id"]) for row in outer_anchor}
    internal_ids = {str(row["case_id"]) for row in internal_anchor}
    if outer_ids & internal_ids:
        raise HumanBoundaryAnchorError("outer/internal anchor populations overlap")

    replacement = bool(invalid_ids)
    selection: dict[str, Any] = {
        "schema_version": (
            HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION
            if replacement
            else HUMAN_ANCHOR_SELECTION_SCHEMA_VERSION
        ),
        "policy_id": (
            HUMAN_ANCHOR_REPLACEMENT_SELECTION_POLICY_ID
            if replacement
            else HUMAN_ANCHOR_SELECTION_POLICY_ID
        ),
        "selection_basis": (
            "deterministic_locked_order_replacement_after_human_question_validity_failure_no_timing_or_backend_outcomes"
            if replacement
            else "deterministic_projection_of_pre_model_locked_benchmark_no_audit_or_backend_outcomes"
        ),
        "source_full_lock_sha256": str(full_lock.get("lock_sha256") or ""),
        "counts": {
            "outer": len(outer_anchor),
            "internal": len(internal_anchor),
            "outer_single": sum(int(row.get("segment_count") or 0) == 1 for row in outer_anchor),
            "outer_multi": sum(int(row.get("segment_count") or 0) >= 2 for row in outer_anchor),
            "calibration_per_population": ANCHOR_CALIBRATION_COUNT,
            "holdout_per_population": ANCHOR_HOLDOUT_COUNT,
            "human_boundary_points": ANCHOR_TOTAL_BOUNDARY_POINTS,
        },
        "production_policy": asdict(HUMAN_ANCHOR_PRODUCTION_POLICY),
        "populations": {
            "outer": outer_anchor,
            "internal": internal_anchor,
        },
    }
    if replacement:
        selection["question_invalid_case_ids"] = list(invalid_ids)
    selection["selection_sha256"] = _sha_json(selection)
    return selection


def anchor_policy_dict() -> dict[str, Any]:
    return asdict(HUMAN_ANCHOR_PRODUCTION_POLICY)


def clarity_to_uncertainty_ms(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text == "clear":
        return 50
    if text == "ambiguous":
        return 100
    raise HumanBoundaryAnchorError("clarity must be 'clear' or 'ambiguous'")


def legacy_uncertainty_to_clarity(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        milliseconds = int(text)
    except ValueError as exc:
        raise HumanBoundaryAnchorError("legacy uncertainty is not an integer") from exc
    return "clear" if milliseconds <= 50 else "ambiguous"
