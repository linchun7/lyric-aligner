"""Evaluation-only reconciliation of canonical render cues onto editor SRT cues.

Canonical lyrics own text and order, while the editor SRT supplies the default
subtitle cue topology.  This module does not move, split, merge, add, or delete
editor cues.  It only evaluates whether already-rendered canonical cues can be
owned by the existing editor cues without crossing a boundary.

No result from this module grants production segmentation authority.  A future
stage may consume stronger token/word/audio boundary evidence to rebut an editor
boundary, but this first evaluator intentionally never emits ``rebutted``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from lyric_aligner.srt import Cue, cue_id, normalized_text, text_sha256


SCHEMA_VERSION = "editor-reconcile-0.1"
SEGMENTATION_AUTHORITY = "editor_reconciliation_evaluation_only"
SUPPORTED_STATUSES = ("resolved", "still_review", "rebutted", "not_evaluable")


class EditorCueReconciliationError(ValueError):
    """Raised when reconciliation evidence is malformed or internally inconsistent."""


@dataclass(frozen=True)
class CanonicalCueEvidence:
    """One canonical-line evaluation cue plus its lineage-bearing audit identity."""

    position: int
    cue_number: int
    start_ms: int
    end_ms: int
    text: str
    occurrence_id: str
    track_id: str
    ordinal: int
    canonical_line_index: int
    timing_format: str
    end_basis: str
    cue_id: str
    text_sha256: str

    def reference(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "cue_number": self.cue_number,
            "cue_id": self.cue_id,
            "occurrence_id": self.occurrence_id,
            "track_id": self.track_id,
            "ordinal": self.ordinal,
            "canonical_line_index": self.canonical_line_index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "timing_format": self.timing_format,
            "end_basis": self.end_basis,
        }


def _int_field(row: Mapping[str, Any], name: str, *, position: int) -> int:
    try:
        value = int(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise EditorCueReconciliationError(
            f"canonical audit row {position} has invalid {name}"
        ) from exc
    return value


def canonical_evidence_from_audit(
    rendered_cues: Sequence[Cue],
    audit_rows: Sequence[Mapping[str, Any]],
) -> list[CanonicalCueEvidence]:
    """Build strict canonical evidence after SRT/report binding was validated."""

    if len(rendered_cues) != len(audit_rows):
        raise EditorCueReconciliationError(
            "canonical render cue count differs from audit row count"
        )

    evidence: list[CanonicalCueEvidence] = []
    seen_ids: set[str] = set()
    for position, (cue, row) in enumerate(zip(rendered_cues, audit_rows), start=1):
        row_position = _int_field(row, "position", position=position)
        row_number = _int_field(row, "cue_number", position=position)
        row_start = _int_field(row, "start_ms", position=position)
        row_end = _int_field(row, "end_ms", position=position)
        if row_position != position:
            raise EditorCueReconciliationError(
                f"canonical audit row {position} has non-sequential position"
            )
        if (row_number, row_start, row_end) != (cue.number, cue.start_ms, cue.end_ms):
            raise EditorCueReconciliationError(
                f"canonical audit row {position} differs from rendered cue"
            )

        recorded_cue_id = str(row.get("cue_id") or "").strip()
        expected_cue_id = cue_id(position, cue)
        if recorded_cue_id != expected_cue_id:
            raise EditorCueReconciliationError(
                f"canonical audit row {position} has invalid cue_id"
            )
        if recorded_cue_id in seen_ids:
            raise EditorCueReconciliationError("canonical audit contains duplicate cue_id")
        seen_ids.add(recorded_cue_id)

        recorded_text_hash = str(row.get("text_sha256") or "").strip()
        expected_text_hash = text_sha256(cue.text)
        if recorded_text_hash != expected_text_hash:
            raise EditorCueReconciliationError(
                f"canonical audit row {position} has invalid text_sha256"
            )

        occurrence_id = str(row.get("occurrence_id") or "").strip()
        track_id = str(row.get("track_id") or "").strip()
        if not occurrence_id or not track_id:
            raise EditorCueReconciliationError(
                f"canonical audit row {position} is missing occurrence/track identity"
            )

        evidence.append(
            CanonicalCueEvidence(
                position=position,
                cue_number=cue.number,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                occurrence_id=occurrence_id,
                track_id=track_id,
                ordinal=_int_field(row, "ordinal", position=position),
                canonical_line_index=_int_field(
                    row, "canonical_line_index", position=position
                ),
                timing_format=str(row.get("timing_format") or "").strip(),
                end_basis=str(row.get("end_basis") or "").strip(),
                cue_id=recorded_cue_id,
                text_sha256=recorded_text_hash,
            )
        )
    return evidence


def _intersects(editor: Cue, canonical: CanonicalCueEvidence) -> bool:
    return min(editor.end_ms, canonical.end_ms) > max(editor.start_ms, canonical.start_ms)


def _contains(editor: Cue, canonical: CanonicalCueEvidence) -> bool:
    return editor.start_ms <= canonical.start_ms and canonical.end_ms <= editor.end_ms


def _canonical_overlaps(rows: Sequence[CanonicalCueEvidence]) -> bool:
    ordered = sorted(rows, key=lambda item: (item.start_ms, item.end_ms, item.position))
    return any(right.start_ms < left.end_ms for left, right in zip(ordered, ordered[1:]))


def evaluate_editor_cue_reconciliation(
    editor_cues: Sequence[Cue],
    canonical_cues: Sequence[CanonicalCueEvidence],
) -> dict[str, Any]:
    """Evaluate canonical cue ownership under unchanged editor cue topology.

    A canonical cue is structurally resolved only when its complete rendered
    interval is contained by exactly one editor cue.  Crossing an editor boundary
    or fitting inside more than one overlapping editor cue is intentionally left
    for review.  Canonical cues assigned to the same editor cue must also be
    mutually non-overlapping; otherwise one editor cue would silently flatten
    concurrent canonical material.
    """

    if not editor_cues:
        raise EditorCueReconciliationError("editor SRT has no cues")
    if not canonical_cues:
        raise EditorCueReconciliationError("canonical evaluation render has no cues")

    assignments: dict[int, list[CanonicalCueEvidence]] = {
        position: [] for position in range(1, len(editor_cues) + 1)
    }
    conflicts: dict[int, set[str]] = {
        position: set() for position in range(1, len(editor_cues) + 1)
    }
    unassigned: list[dict[str, Any]] = []

    for canonical in canonical_cues:
        containers = [
            position
            for position, editor in enumerate(editor_cues, start=1)
            if _contains(editor, canonical)
        ]
        intersections = [
            position
            for position, editor in enumerate(editor_cues, start=1)
            if _intersects(editor, canonical)
        ]

        if len(containers) == 1:
            assignments[containers[0]].append(canonical)
            continue

        if len(containers) > 1:
            reason = "ambiguous_overlapping_editor_ownership"
            for position in containers:
                conflicts[position].add(reason)
            unassigned.append(
                {
                    **canonical.reference(),
                    "reason": reason,
                    "editor_positions": containers,
                }
            )
            continue

        if intersections:
            reason = "canonical_interval_crosses_editor_boundary"
            for position in intersections:
                conflicts[position].add(reason)
            unassigned.append(
                {
                    **canonical.reference(),
                    "reason": reason,
                    "editor_positions": intersections,
                }
            )
            continue

        unassigned.append(
            {
                **canonical.reference(),
                "reason": "no_editor_temporal_overlap",
                "editor_positions": [],
            }
        )

    editor_rows: list[dict[str, Any]] = []
    status_counts = {status: 0 for status in SUPPORTED_STATUSES}
    for position, editor in enumerate(editor_cues, start=1):
        assigned = sorted(
            assignments[position],
            key=lambda item: (item.start_ms, item.ordinal, item.canonical_line_index, item.position),
        )
        reasons = set(conflicts[position])
        if assigned and _canonical_overlaps(assigned):
            reasons.add("canonical_overlap_inside_editor_cue")

        if reasons:
            status = "still_review"
            reason = ",".join(sorted(reasons))
        elif assigned:
            status = "resolved"
            reason = "canonical_intervals_fit_unique_editor_cue"
        else:
            status = "not_evaluable"
            reason = "no_canonical_temporal_evidence"

        status_counts[status] += 1
        candidate_texts = [item.text for item in assigned]
        candidate_stream = "".join(normalized_text(value) for value in candidate_texts)
        editor_rows.append(
            {
                "editor_position": position,
                "editor_cue_number": editor.number,
                "editor_cue_id": cue_id(position, editor),
                "start_ms": editor.start_ms,
                "end_ms": editor.end_ms,
                "editor_text": editor.text,
                "editor_text_sha256": text_sha256(editor.text),
                "status": status,
                "reason": reason,
                "canonical_cue_count": len(assigned),
                "canonical_refs": [item.reference() for item in assigned],
                "candidate_texts": candidate_texts,
                "candidate_stream_sha256": text_sha256(candidate_stream)
                if candidate_texts
                else None,
                "text_changed": bool(candidate_texts)
                and normalized_text(editor.text)
                != "\n".join(normalized_text(value) for value in candidate_texts),
            }
        )

    editor_file_order_monotonic = all(
        right.start_ms >= left.start_ms
        for left, right in zip(editor_cues, editor_cues[1:])
    )
    order_inversions = [
        (left, right)
        for left, right in zip(editor_cues, editor_cues[1:])
        if right.start_ms < left.start_ms
    ]
    editor_file_order_recoverable_nonoverlap_reordering = (
        editor_file_order_monotonic
        or all(right.end_ms <= left.start_ms for left, right in order_inversions)
    )
    assigned_count = sum(len(rows) for rows in assignments.values())
    full_topology_candidate = (
        status_counts["resolved"] == len(editor_cues)
        and assigned_count == len(canonical_cues)
        and not unassigned
        and editor_file_order_monotonic
    )

    global_issues: list[str] = []
    if not editor_file_order_monotonic:
        global_issues.append("editor_file_order_nonmonotonic")
    if unassigned:
        global_issues.append("canonical_cues_unassigned")
    if status_counts["still_review"]:
        global_issues.append("editor_cues_still_review")
    if status_counts["not_evaluable"]:
        global_issues.append("editor_cues_not_evaluable")

    return {
        "schema_version": SCHEMA_VERSION,
        "segmentation_authority": SEGMENTATION_AUTHORITY,
        "production_authority_granted": False,
        "supported_statuses": list(SUPPORTED_STATUSES),
        "editor_cue_count": len(editor_cues),
        "canonical_cue_count": len(canonical_cues),
        "canonical_assigned_count": assigned_count,
        "canonical_unassigned_count": len(unassigned),
        "status_counts": status_counts,
        "editor_file_order_monotonic": editor_file_order_monotonic,
        "editor_file_order_recoverable_nonoverlap_reordering": (
            editor_file_order_recoverable_nonoverlap_reordering
        ),
        "full_topology_candidate": full_topology_candidate,
        "global_issues": global_issues,
        "editor_cues": editor_rows,
        "canonical_unassigned": unassigned,
    }
