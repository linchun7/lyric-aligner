"""Bridge real P9 fusion identities into Partial Timeline Repair inputs.

The bridge is intentionally conservative: uncalibrated P9 shadow levels never
promote an editor cue to trusted/untrusted timing authority. Trust must be
provided explicitly (today by human review, later by a separately calibrated
policy). For explicitly untrusted cues, the only repair candidate emitted here
is the canonical Source-to-Mix boundary already present in P9 fusion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import (
    CueTrust,
    PartialTimelineRepairError,
    TimingCandidate,
)


PARTIAL_REPAIR_EVIDENCE_BRIDGE_SCHEMA_VERSION = "1.0"
_SUPPORTED_MAPPING_KINDS = {"AFFINE", "PIECEWISE_RATE", "CUT_AWARE"}


@dataclass(frozen=True)
class ExplicitCueTrust:
    cue_number: int
    status: str
    reason: str
    source: str = "human_review"

    def __post_init__(self) -> None:
        if self.cue_number <= 0:
            raise PartialTimelineRepairError("cue_number must be positive")
        if self.status not in {"trusted", "untrusted", "unknown"}:
            raise PartialTimelineRepairError(
                "explicit trust status must be trusted, untrusted, or unknown"
            )
        if not self.reason.strip():
            raise PartialTimelineRepairError("explicit trust reason must be non-empty")
        if self.source not in {"human_review", "calibrated_policy"}:
            raise PartialTimelineRepairError(
                "explicit trust source must be human_review or calibrated_policy"
            )


@dataclass(frozen=True)
class CueEvidenceBinding:
    cue_number: int
    position: int
    explicit_trust_status: str
    explicit_trust_source: str | None
    fusion_line_count: int
    fusion_shadow_levels: tuple[str, ...]
    fusion_conflict: bool
    candidate_status: str
    candidate_reason: str
    occurrence_id: str | None = None
    canonical_line_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fusion_shadow_levels"] = list(self.fusion_shadow_levels)
        return payload


def _validate_fusion(fusion: dict[str, Any]) -> list[dict[str, Any]]:
    if fusion.get("mode") != "shadow_only":
        raise PartialTimelineRepairError("P9 fusion must be shadow_only")
    if fusion.get("policy_calibrated") is not False:
        raise PartialTimelineRepairError(
            "this bridge expects the current uncalibrated P9 shadow contract"
        )
    if fusion.get("release_gate_eligible") is not False:
        raise PartialTimelineRepairError("P9 fusion unexpectedly allows release gating")
    if fusion.get("automatic_timing_change_allowed") is not False:
        raise PartialTimelineRepairError(
            "P9 fusion unexpectedly allows automatic timing mutation"
        )
    authority = fusion.get("authority")
    if not isinstance(authority, dict):
        raise PartialTimelineRepairError("P9 fusion authority block is missing")
    if authority.get("canonical_text") != "canonical_lyrics_only":
        raise PartialTimelineRepairError("unexpected canonical text authority")
    if authority.get("primary_timing") != "source_to_mix_only":
        raise PartialTimelineRepairError("unexpected primary timing authority")
    lines = fusion.get("lines")
    if not isinstance(lines, list):
        raise PartialTimelineRepairError("P9 fusion lines must be a list")
    output: list[dict[str, Any]] = []
    for row in lines:
        if not isinstance(row, dict):
            raise PartialTimelineRepairError("P9 fusion line must be an object")
        if row.get("shadow_level_calibrated") is not False:
            raise PartialTimelineRepairError("P9 line unexpectedly claims calibration")
        if row.get("release_gate_eligible") is not False:
            raise PartialTimelineRepairError("P9 line unexpectedly allows release")
        if row.get("automatic_timing_change_allowed") is not False:
            raise PartialTimelineRepairError(
                "P9 line unexpectedly allows automatic timing mutation"
            )
        level = str(row.get("shadow_level") or "")
        if level not in {"LOW", "MEDIUM", "HIGH", "CONFLICT"}:
            raise PartialTimelineRepairError("P9 fusion shadow level is invalid")
        boundary = row.get("source_timeline_boundary_ms")
        if (
            not isinstance(boundary, list)
            or len(boundary) != 2
            or not all(isinstance(value, int) for value in boundary)
            or boundary[0] < 0
            or boundary[1] <= boundary[0]
        ):
            raise PartialTimelineRepairError(
                "P9 source_timeline_boundary_ms is invalid"
            )
        output.append(row)
    return output


def _cue_positions(cues: Sequence[Cue]) -> dict[int, int]:
    output: dict[int, int] = {}
    for position, cue in enumerate(cues):
        if cue.number in output:
            raise PartialTimelineRepairError("SRT cue numbers must be unique")
        output[cue.number] = position
    if not output:
        raise PartialTimelineRepairError("evidence bridge requires at least one SRT cue")
    return output


def _editor_cue_number(row: dict[str, Any]) -> int | None:
    families = row.get("families")
    if not isinstance(families, list):
        raise PartialTimelineRepairError("P9 fusion families must be a list")
    editor_rows = [
        family
        for family in families
        if isinstance(family, dict) and family.get("family") == "editor"
    ]
    if len(editor_rows) > 1:
        raise PartialTimelineRepairError("P9 line has duplicate editor family rows")
    if not editor_rows or not editor_rows[0].get("available"):
        return None
    try:
        value = int(editor_rows[0]["cue_number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PartialTimelineRepairError("P9 editor cue_number is invalid") from exc
    if value <= 0:
        raise PartialTimelineRepairError("P9 editor cue_number must be positive")
    return value


def _explicit_trust_index(
    cues: Sequence[Cue], rows: Iterable[ExplicitCueTrust]
) -> tuple[list[CueTrust], dict[int, ExplicitCueTrust]]:
    positions = _cue_positions(cues)
    by_number: dict[int, ExplicitCueTrust] = {}
    output: list[CueTrust] = []
    for row in rows:
        if row.cue_number not in positions:
            raise PartialTimelineRepairError(
                f"explicit trust references unknown cue number {row.cue_number}"
            )
        if row.cue_number in by_number:
            raise PartialTimelineRepairError(
                f"duplicate explicit trust for cue number {row.cue_number}"
            )
        by_number[row.cue_number] = row
        output.append(
            CueTrust(
                position=positions[row.cue_number],
                status=row.status,
                reason=f"{row.source}:{row.reason}",
            )
        )
    output.sort(key=lambda item: item.position)
    return output, by_number


def bridge_fusion_to_partial_repair(
    *,
    cues: Sequence[Cue],
    fusion: dict[str, Any],
    mapping_kind_by_occurrence: dict[str, str],
    explicit_trust: Iterable[ExplicitCueTrust],
) -> tuple[list[CueTrust], list[TimingCandidate], dict[str, Any]]:
    """Create P1 trust/candidate inputs without deriving trust from P9 levels."""

    lines = _validate_fusion(fusion)
    positions = _cue_positions(cues)
    trust_rows, trust_by_number = _explicit_trust_index(cues, explicit_trust)

    lines_by_cue: dict[int, list[dict[str, Any]]] = {}
    ignored_line_count = 0
    for row in lines:
        cue_number = _editor_cue_number(row)
        if cue_number is None or cue_number not in positions:
            ignored_line_count += 1
            continue
        lines_by_cue.setdefault(cue_number, []).append(row)

    candidates: list[TimingCandidate] = []
    bindings: list[CueEvidenceBinding] = []
    for cue_number, position in sorted(positions.items(), key=lambda item: item[1]):
        trust = trust_by_number.get(cue_number)
        bound_lines = lines_by_cue.get(cue_number, [])
        levels = tuple(str(row["shadow_level"]) for row in bound_lines)
        conflict = any(level == "CONFLICT" for level in levels)
        candidate_status = "not_requested"
        candidate_reason = "cue_not_explicitly_untrusted"
        occurrence_id: str | None = None
        canonical_line_index: int | None = None

        if trust is not None and trust.status == "untrusted":
            if not bound_lines:
                candidate_status = "unavailable"
                candidate_reason = "no_unique_p9_editor_to_canonical_binding"
            elif len(bound_lines) > 1:
                candidate_status = "ambiguous"
                candidate_reason = "multiple_canonical_lines_bound_to_same_editor_cue"
            else:
                row = bound_lines[0]
                occurrence_id = str(row.get("occurrence_id") or "").strip()
                if not occurrence_id:
                    raise PartialTimelineRepairError(
                        "P9 bound line has no occurrence_id"
                    )
                try:
                    canonical_line_index = int(row["canonical_line_index"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise PartialTimelineRepairError(
                        "P9 bound canonical_line_index is invalid"
                    ) from exc
                mapping_kind = mapping_kind_by_occurrence.get(occurrence_id)
                if mapping_kind not in _SUPPORTED_MAPPING_KINDS:
                    candidate_status = "unavailable"
                    candidate_reason = "missing_or_unsupported_occurrence_mapping_kind"
                else:
                    boundary = row["source_timeline_boundary_ms"]
                    if int(boundary[1]) - int(boundary[0]) <= 1:
                        candidate_status = "unavailable"
                        candidate_reason = "open_end_source_timeline_boundary_is_not_repairable"
                    else:
                        candidates.append(
                            TimingCandidate(
                                position=position,
                                start_ms=int(boundary[0]),
                                end_ms=int(boundary[1]),
                                projection_status="projected",
                                mapping_kind=mapping_kind,
                                source="source_to_mix",
                                confidence=None,
                            )
                        )
                        candidate_status = "projected"
                        candidate_reason = "unique_p9_binding_uses_source_timeline_only"

        bindings.append(
            CueEvidenceBinding(
                cue_number=cue_number,
                position=position,
                explicit_trust_status=(trust.status if trust is not None else "unknown"),
                explicit_trust_source=(trust.source if trust is not None else None),
                fusion_line_count=len(bound_lines),
                fusion_shadow_levels=levels,
                fusion_conflict=conflict,
                candidate_status=candidate_status,
                candidate_reason=candidate_reason,
                occurrence_id=occurrence_id,
                canonical_line_index=canonical_line_index,
            )
        )

    report = {
        "schema_version": PARTIAL_REPAIR_EVIDENCE_BRIDGE_SCHEMA_VERSION,
        "mode": "partial_timeline_repair_evidence_bridge",
        "proposal_only": True,
        "publish_ready": False,
        "policy_calibrated": False,
        "automatic_timing_change_allowed": False,
        "trust_derivation_policy": (
            "P9 LOW/MEDIUM/HIGH/CONFLICT are diagnostics only; only explicit "
            "human_review or separately calibrated_policy input can set cue trust"
        ),
        "candidate_authority": "source_to_mix_only",
        "fusion_line_count": len(lines),
        "ignored_unbound_fusion_line_count": ignored_line_count,
        "candidate_count": len(candidates),
        "bindings": [row.to_dict() for row in bindings],
    }
    return trust_rows, candidates, report
