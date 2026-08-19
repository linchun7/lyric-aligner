"""Fail-closed Partial Timeline Repair shadow planning.

This module is intentionally proposal-only. It preserves trusted editor cues
byte-for-byte at the timing level and evaluates Source-to-Mix projected timing
candidates only for explicitly untrusted cues. It never promotes editor/ASR text
to canonical authority and never treats BPM/rate change as a cut.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from lyric_aligner.srt import Cue, cue_id


PARTIAL_TIMELINE_REPAIR_SCHEMA_VERSION = "1.0"
_SUPPORTED_MAPPING_KINDS = {"AFFINE", "PIECEWISE_RATE", "CUT_AWARE"}
_ALLOWED_TRUST = {"trusted", "untrusted", "unknown"}


class PartialTimelineRepairError(ValueError):
    """Raised when a partial-repair request is internally inconsistent."""


@dataclass(frozen=True)
class CueTrust:
    position: int
    status: str
    reason: str

    def __post_init__(self) -> None:
        if self.position < 0:
            raise PartialTimelineRepairError("cue trust position must be non-negative")
        if self.status not in _ALLOWED_TRUST:
            raise PartialTimelineRepairError(
                "cue trust status must be trusted, untrusted, or unknown"
            )
        if not self.reason.strip():
            raise PartialTimelineRepairError("cue trust reason must be non-empty")


@dataclass(frozen=True)
class TimingCandidate:
    position: int
    start_ms: int | None
    end_ms: int | None
    projection_status: str
    mapping_kind: str
    source: str = "source_to_mix"
    confidence: float | None = None
    projection_reason: str | None = None
    cut_aware_segment_index: int | None = None

    def __post_init__(self) -> None:
        if self.position < 0:
            raise PartialTimelineRepairError("candidate position must be non-negative")
        if self.mapping_kind not in _SUPPORTED_MAPPING_KINDS:
            raise PartialTimelineRepairError(
                f"unsupported Source-to-Mix mapping kind: {self.mapping_kind}"
            )
        if self.source != "source_to_mix":
            raise PartialTimelineRepairError(
                "partial repair timing candidates must come from Source-to-Mix"
            )
        if self.projection_status not in {"projected", "unprojectable"}:
            raise PartialTimelineRepairError(
                "projection_status must be projected or unprojectable"
            )
        if self.projection_status == "projected":
            if self.start_ms is None or self.end_ms is None:
                raise PartialTimelineRepairError(
                    "projected timing candidate requires start_ms and end_ms"
                )
            if self.start_ms < 0 or self.end_ms <= self.start_ms:
                raise PartialTimelineRepairError(
                    "projected timing candidate must be a positive interval"
                )
        else:
            if self.start_ms is not None or self.end_ms is not None:
                raise PartialTimelineRepairError(
                    "unprojectable timing candidate must not carry mix bounds"
                )
            if not (self.projection_reason or "").strip():
                raise PartialTimelineRepairError(
                    "unprojectable timing candidate requires projection_reason"
                )
        if self.confidence is not None:
            if not math.isfinite(float(self.confidence)) or not 0.0 <= float(self.confidence) <= 1.0:
                raise PartialTimelineRepairError("candidate confidence must be in [0, 1]")


@dataclass(frozen=True)
class CueRepairDecision:
    position: int
    cue_number: int
    cue_id: str
    trust_status: str
    action: str
    reason: str
    original_start_ms: int
    original_end_ms: int
    candidate_start_ms: int | None
    candidate_end_ms: int | None
    mapping_kind: str | None
    confidence: float | None
    shift_start_ms: int | None
    shift_end_ms: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trust_by_position(
    cues: Sequence[Cue], trust: Iterable[CueTrust]
) -> dict[int, CueTrust]:
    rows: dict[int, CueTrust] = {}
    for item in trust:
        if item.position >= len(cues):
            raise PartialTimelineRepairError(
                f"cue trust position {item.position} is outside the SRT"
            )
        if item.position in rows:
            raise PartialTimelineRepairError(
                f"duplicate cue trust position {item.position}"
            )
        rows[item.position] = item
    return rows


def _candidate_by_position(
    cues: Sequence[Cue], candidates: Iterable[TimingCandidate]
) -> dict[int, TimingCandidate]:
    rows: dict[int, TimingCandidate] = {}
    for item in candidates:
        if item.position >= len(cues):
            raise PartialTimelineRepairError(
                f"timing candidate position {item.position} is outside the SRT"
            )
        if item.position in rows:
            raise PartialTimelineRepairError(
                f"duplicate timing candidate position {item.position}"
            )
        rows[item.position] = item
    return rows


def _nearest_locked_left(
    cues: Sequence[Cue], trust: dict[int, CueTrust], position: int
) -> Cue | None:
    for index in range(position - 1, -1, -1):
        item = trust.get(index)
        if item is not None and item.status == "trusted":
            return cues[index]
    return None


def _nearest_locked_right(
    cues: Sequence[Cue], trust: dict[int, CueTrust], position: int
) -> Cue | None:
    for index in range(position + 1, len(cues)):
        item = trust.get(index)
        if item is not None and item.status == "trusted":
            return cues[index]
    return None


def _candidate_inside_locked_neighbors(
    candidate: TimingCandidate,
    left: Cue | None,
    right: Cue | None,
) -> tuple[bool, str]:
    assert candidate.start_ms is not None and candidate.end_ms is not None
    if left is not None and candidate.start_ms < left.end_ms:
        return False, "candidate_crosses_locked_left_cue"
    if right is not None and candidate.end_ms > right.start_ms:
        return False, "candidate_crosses_locked_right_cue"
    return True, "candidate_respects_locked_neighbors"


def choose_task_mode(
    cues: Sequence[Cue], trust: Iterable[CueTrust]
) -> str:
    """Choose preserve/hybrid/rebuild from explicit cue trust only.

    This is routing, not confidence calibration. Unknown trust never becomes a
    production-safe preserve decision.
    """

    if not cues:
        raise PartialTimelineRepairError("partial repair requires at least one cue")
    rows = _trust_by_position(cues, trust)
    statuses = [rows.get(index).status if index in rows else "unknown" for index in range(len(cues))]
    trusted = statuses.count("trusted")
    untrusted = statuses.count("untrusted")
    unknown = statuses.count("unknown")
    if trusted == len(cues):
        return "preserve"
    if trusted > 0 and untrusted > 0 and unknown == 0:
        return "hybrid"
    return "rebuild"


def plan_partial_timeline_repair(
    cues: Sequence[Cue],
    trust: Iterable[CueTrust],
    candidates: Iterable[TimingCandidate],
) -> dict[str, Any]:
    """Build a deterministic proposal without mutating SRT timing.

    ``propose_repair`` means the candidate is structurally usable for later
    calibrated policy. It does *not* mean publish-ready or auto-approved.
    """

    if not cues:
        raise PartialTimelineRepairError("partial repair requires at least one cue")
    trust_rows = _trust_by_position(cues, trust)
    candidate_rows = _candidate_by_position(cues, candidates)
    mode = choose_task_mode(cues, trust_rows.values())
    decisions: list[CueRepairDecision] = []

    for position, cue in enumerate(cues):
        item = trust_rows.get(
            position,
            CueTrust(position, "unknown", "no_explicit_trust_evidence"),
        )
        candidate = candidate_rows.get(position)

        if item.status == "trusted":
            action = "preserve"
            reason = "explicitly_trusted_editor_timing_locked"
            candidate_start = None
            candidate_end = None
            mapping_kind = None
            confidence = None
        elif item.status == "unknown":
            action = "review"
            reason = "unknown_timing_trust_requires_review"
            candidate_start = candidate.start_ms if candidate else None
            candidate_end = candidate.end_ms if candidate else None
            mapping_kind = candidate.mapping_kind if candidate else None
            confidence = candidate.confidence if candidate else None
        elif candidate is None:
            action = "review"
            reason = "untrusted_cue_has_no_source_to_mix_candidate"
            candidate_start = None
            candidate_end = None
            mapping_kind = None
            confidence = None
        elif candidate.projection_status != "projected":
            action = "block"
            reason = candidate.projection_reason or "source_to_mix_projection_unavailable"
            candidate_start = None
            candidate_end = None
            mapping_kind = candidate.mapping_kind
            confidence = candidate.confidence
        else:
            left = _nearest_locked_left(cues, trust_rows, position)
            right = _nearest_locked_right(cues, trust_rows, position)
            safe, structural_reason = _candidate_inside_locked_neighbors(
                candidate, left, right
            )
            candidate_start = candidate.start_ms
            candidate_end = candidate.end_ms
            mapping_kind = candidate.mapping_kind
            confidence = candidate.confidence
            if safe:
                action = "propose_repair"
                reason = structural_reason
            else:
                action = "block"
                reason = structural_reason

        decisions.append(
            CueRepairDecision(
                position=position,
                cue_number=cue.number,
                cue_id=cue_id(position, cue),
                trust_status=item.status,
                action=action,
                reason=reason,
                original_start_ms=cue.start_ms,
                original_end_ms=cue.end_ms,
                candidate_start_ms=candidate_start,
                candidate_end_ms=candidate_end,
                mapping_kind=mapping_kind,
                confidence=confidence,
                shift_start_ms=(
                    None if candidate_start is None else candidate_start - cue.start_ms
                ),
                shift_end_ms=(
                    None if candidate_end is None else candidate_end - cue.end_ms
                ),
            )
        )

    action_counts = {
        key: sum(item.action == key for item in decisions)
        for key in ("preserve", "propose_repair", "review", "block")
    }
    return {
        "schema_version": PARTIAL_TIMELINE_REPAIR_SCHEMA_VERSION,
        "mode": mode,
        "status": (
            "blocked"
            if action_counts["block"]
            else "review_required"
            if action_counts["review"] or action_counts["propose_repair"]
            else "preserve_ready"
        ),
        "proposal_only": True,
        "publish_ready": False,
        "canonical_text_authority": "canonical_lyrics_only",
        "primary_timing_authority": "source_to_mix_only",
        "editor_timing_role": "explicit_trust_lock_or_non_authoritative_evidence",
        "rate_change_policy": (
            "AFFINE/PIECEWISE_RATE are continuous rate mappings; rate change is not a cut"
        ),
        "cut_policy": (
            "CUT_AWARE may be used only after independent cut confirmation upstream; "
            "unprojectable intervals fail closed"
        ),
        "cue_count": len(cues),
        "task_mode": mode,
        "action_counts": action_counts,
        "decisions": [item.to_dict() for item in decisions],
    }
