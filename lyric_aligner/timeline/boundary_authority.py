"""Conservative lyric-boundary authority and difficulty routing.

This module deliberately separates *where a lyric is expected* from evidence
that is strong enough to mutate a published subtitle boundary.  Editor SRT and
LRC timestamps are useful priors, but neither is independent vocal-onset/offset
evidence.  Automatic mutation therefore requires agreement between independent
audio evidence families.  Large disagreements are escalated to structural
realignment instead of being averaged into a precise-looking wrong timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable


BOUNDARY_AUTHORITY_POLICY_VERSION = "1.0"

# Conservative initial policy.  These are intentionally stricter than the
# semantic-sync release gate because this layer authorizes mutation, rather
# than merely detecting catastrophic whole-track drift.
FINE_EDITOR_DELTA_MS = 500
STRUCTURAL_ESCALATION_DELTA_MS = 1500
HIGH_AUDIO_AGREEMENT_MS = 150
MEDIUM_AUDIO_AGREEMENT_MS = 300
MAX_AUTO_UNCERTAINTY_MS = 150

# Families are intentionally split by how independently they observe the final
# vocal boundary.  LRC/editor priors are not audio families and never count
# toward automatic mutation authority.
DIRECT_FINAL_MIX_AUDIO_FAMILIES = frozenset(
    {
        "final_mix_forced_alignment",
        "final_mix_singing_alignment",
        "final_mix_acoustic_onset",
        "final_mix_acoustic_offset",
    }
)
PROJECTED_SOURCE_AUDIO_FAMILIES = frozenset(
    {
        "source_forced_alignment_projection",
        "source_singing_alignment_projection",
        "source_mix_local_acoustic_projection",
    }
)
LOWER_TRUST_AUDIO_FAMILIES = frozenset({"asr_word_timing"})
INDEPENDENT_AUDIO_FAMILIES = (
    DIRECT_FINAL_MIX_AUDIO_FAMILIES
    | PROJECTED_SOURCE_AUDIO_FAMILIES
    | LOWER_TRUST_AUDIO_FAMILIES
)

HEAVY_RISK_FLAGS = frozenset(
    {
        "crossfade",
        "confirmed_cut_nearby",
        "possible_cut",
        "source_gap",
        "overlapping_vocals",
        "repeated_lyric_identity",
        "track_transition",
        "mapping_discontinuity",
    }
)


class BoundaryAuthorityError(ValueError):
    """Raised when boundary evidence is malformed or internally inconsistent."""


@dataclass(frozen=True)
class BoundaryEvidencePoint:
    family: str
    boundary_ms: int
    confidence: float
    uncertainty_ms: int
    correlation_group: str
    backend_id: str
    calibration_passed: bool
    calibration_sha256: str


@dataclass(frozen=True)
class BoundaryDecision:
    policy_version: str
    action: str
    selected_ms: int
    editor_ms: int
    candidate_delta_ms: int
    audio_family_count: int
    audio_spread_ms: int | None
    evidence_basis: tuple[str, ...]
    difficulty_tier: str
    required_capabilities: tuple[str, ...]
    reason: str

    @property
    def automatic_mutation_allowed(self) -> bool:
        return self.action in {"auto_fine_refine", "auto_safe_refine"}


def _normalize_confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryAuthorityError("boundary evidence confidence is invalid") from exc
    if not 0.0 <= result <= 1.0:
        raise BoundaryAuthorityError("boundary evidence confidence must be within [0,1]")
    return result


def _normalize_nonnegative_ms(value: Any, *, label: str) -> int:
    try:
        result = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise BoundaryAuthorityError(f"{label} is invalid") from exc
    if result < 0:
        raise BoundaryAuthorityError(f"{label} must be >= 0")
    return result


def normalize_boundary_evidence(points: Iterable[dict[str, Any]]) -> list[BoundaryEvidencePoint]:
    output: list[BoundaryEvidencePoint] = []
    seen_families: set[str] = set()
    for raw in points:
        if not isinstance(raw, dict):
            raise BoundaryAuthorityError("boundary evidence point must be an object")
        family = str(raw.get("family") or "").strip()
        if not family:
            raise BoundaryAuthorityError("boundary evidence family must be non-empty")
        if family in seen_families:
            raise BoundaryAuthorityError(f"duplicate boundary evidence family: {family}")
        seen_families.add(family)
        correlation_group = str(raw.get("correlation_group") or family).strip()
        if not correlation_group:
            raise BoundaryAuthorityError("boundary evidence correlation_group must be non-empty")
        calibration_passed = bool(raw.get("calibration_passed", False))
        calibration_sha256 = str(raw.get("calibration_sha256") or "").strip()
        if calibration_passed and len(calibration_sha256) != 64:
            raise BoundaryAuthorityError(
                "calibrated boundary evidence requires a calibration_sha256"
            )
        output.append(
            BoundaryEvidencePoint(
                family=family,
                boundary_ms=_normalize_nonnegative_ms(raw.get("boundary_ms"), label="boundary_ms"),
                confidence=_normalize_confidence(raw.get("confidence")),
                uncertainty_ms=_normalize_nonnegative_ms(
                    raw.get("uncertainty_ms", 0), label="uncertainty_ms"
                ),
                correlation_group=correlation_group,
                backend_id=str(raw.get("backend_id") or "").strip(),
                calibration_passed=calibration_passed,
                calibration_sha256=calibration_sha256,
            )
        )
    return output


def classify_boundary_difficulty(
    *,
    editor_ms: int,
    proposed_ms: int,
    cue_duration_ms: int,
    canonical_line_count: int,
    risk_flags: Iterable[str] = (),
) -> tuple[str, tuple[str, ...]]:
    """Route a boundary candidate by risk rather than by one global threshold."""

    delta = abs(int(proposed_ms) - int(editor_ms))
    flags = {str(flag).strip() for flag in risk_flags if str(flag).strip()}
    heavy = bool(flags & HEAVY_RISK_FLAGS)
    if delta > STRUCTURAL_ESCALATION_DELTA_MS or heavy:
        return (
            "D_structural",
            (
                "final_mix_singing_alignment",
                "vocal_separation",
                "source_local_retrieval",
                "source_forced_alignment_projection",
                "structural_realignment",
            ),
        )
    if delta > 800 or cue_duration_ms >= 8000 or canonical_line_count >= 3:
        return (
            "C_heavy_boundary",
            (
                "final_mix_singing_alignment",
                "source_forced_alignment_projection",
                "source_mix_local_acoustic_projection",
                "second_boundary_aligner",
            ),
        )
    if delta > 250 or cue_duration_ms >= 6000 or canonical_line_count >= 2:
        return (
            "B_multi_evidence",
            (
                "final_mix_singing_alignment",
                "source_forced_alignment_projection",
            ),
        )
    return (
        "A_local_refine",
        (
            "final_mix_singing_alignment",
            "source_forced_alignment_projection",
        ),
    )


def adjudicate_boundary_candidate(
    *,
    editor_ms: int,
    proposed_ms: int,
    cue_duration_ms: int,
    canonical_line_count: int,
    evidence_points: Iterable[dict[str, Any]],
    risk_flags: Iterable[str] = (),
    structural_resolution_confirmed: bool = False,
    verified_calibration_sha256s: Iterable[str] = (),
) -> BoundaryDecision:
    """Decide whether one subtitle boundary may be mutated automatically.

    Important invariants:
    - editor/LRC priors never count as independent audio evidence;
    - large deltas escalate to structural handling unless that structural change
      was independently confirmed;
    - one audio model, even a strong one, is not enough to mutate automatically;
    - backend calibration claims count only when their exact calibration SHA was
      independently verified by the artifact-owning caller;
    - ASR word timing cannot pair with itself or an editor prior to create false
      independence.
    """

    editor_ms = _normalize_nonnegative_ms(editor_ms, label="editor_ms")
    proposed_ms = _normalize_nonnegative_ms(proposed_ms, label="proposed_ms")
    cue_duration_ms = _normalize_nonnegative_ms(cue_duration_ms, label="cue_duration_ms")
    canonical_line_count = int(canonical_line_count)
    if canonical_line_count <= 0:
        raise BoundaryAuthorityError("canonical_line_count must be > 0")
    flags = tuple(sorted({str(flag).strip() for flag in risk_flags if str(flag).strip()}))
    difficulty, required = classify_boundary_difficulty(
        editor_ms=editor_ms,
        proposed_ms=proposed_ms,
        cue_duration_ms=cue_duration_ms,
        canonical_line_count=canonical_line_count,
        risk_flags=flags,
    )
    delta = abs(proposed_ms - editor_ms)
    points = normalize_boundary_evidence(evidence_points)
    verified_calibrations = {
        str(value).strip()
        for value in verified_calibration_sha256s
        if len(str(value).strip()) == 64
    }
    audio = [
        point
        for point in points
        if point.family in INDEPENDENT_AUDIO_FAMILIES
        and point.uncertainty_ms <= MAX_AUTO_UNCERTAINTY_MS
        and point.calibration_passed
        and point.calibration_sha256 in verified_calibrations
    ]
    basis = tuple(sorted(point.family for point in audio))
    spread = (
        max(point.boundary_ms for point in audio) - min(point.boundary_ms for point in audio)
        if audio
        else None
    )

    if difficulty == "D_structural" and not structural_resolution_confirmed:
        return BoundaryDecision(
            BOUNDARY_AUTHORITY_POLICY_VERSION,
            "structural_escalation",
            editor_ms,
            editor_ms,
            delta,
            len(audio),
            spread,
            basis,
            difficulty,
            required,
            "large/structural disagreement requires re-localization before any boundary mutation",
        )

    # Require at least two genuinely independent *strong* acoustic observation
    # groups.  Singing ASR word timing is deliberately auxiliary: it may expose a
    # conflict, but it cannot serve as the second vote that authorizes mutation.
    # This reflects observed hallucination/padding failures on real music windows.
    strong_audio = [point for point in audio if point.family not in LOWER_TRUST_AUDIO_FAMILIES]
    strong_correlation_groups = {point.correlation_group for point in strong_audio}
    strong_backend_ids = {point.backend_id for point in strong_audio if point.backend_id}
    if len(strong_correlation_groups) < 2 or len(strong_backend_ids) < 2:
        return BoundaryDecision(
            BOUNDARY_AUTHORITY_POLICY_VERSION,
            "keep_editor",
            editor_ms,
            editor_ms,
            delta,
            len(audio),
            spread,
            basis,
            difficulty,
            required,
            "fewer than two independent non-ASR acoustic backends/correlation groups",
        )

    # Two source-projected families can still share the same source/mapping
    # failure mode.  Require at least one direct final-mix observer among the
    # strong groups; otherwise keep the editor and request heavier evidence.
    has_direct_final = any(
        point.family in DIRECT_FINAL_MIX_AUDIO_FAMILIES for point in strong_audio
    )
    if not has_direct_final:
        return BoundaryDecision(
            BOUNDARY_AUTHORITY_POLICY_VERSION,
            "keep_editor",
            editor_ms,
            editor_ms,
            delta,
            len(audio),
            spread,
            basis,
            difficulty,
            required,
            "strong audio evidence has no direct final-mix observer",
        )

    assert spread is not None
    if spread > MEDIUM_AUDIO_AGREEMENT_MS:
        return BoundaryDecision(
            BOUNDARY_AUTHORITY_POLICY_VERSION,
            "keep_editor",
            editor_ms,
            editor_ms,
            delta,
            len(audio),
            spread,
            basis,
            difficulty,
            required,
            "independent audio boundary families disagree beyond safe tolerance",
        )

    # The final mix is the publication target, so once independent evidence has
    # cross-validated the boundary, select the robust center of direct final-mix
    # observers.  Source-projected evidence verifies the decision but does not
    # pull the published timestamp away from what is observed on the final mix.
    direct_values = [
        point.boundary_ms
        for point in audio
        if point.family in DIRECT_FINAL_MIX_AUDIO_FAMILIES
    ]
    selected = int(round(median(direct_values)))
    selected_delta = abs(selected - editor_ms)
    action = (
        "auto_fine_refine"
        if spread <= HIGH_AUDIO_AGREEMENT_MS and selected_delta <= FINE_EDITOR_DELTA_MS
        else "auto_safe_refine"
    )
    return BoundaryDecision(
        BOUNDARY_AUTHORITY_POLICY_VERSION,
        action,
        selected,
        editor_ms,
        abs(selected - editor_ms),
        len(audio),
        spread,
        basis,
        difficulty,
        required,
        "independent audio boundary families agree within calibrated conservative tolerance",
    )


def timing_mutation_has_audio_authority(row: dict[str, Any]) -> bool:
    """Return True only for rows that carry explicit audited timing authority."""

    authority = str(row.get("boundary_authority") or "").strip()
    return authority in {
        "audio_verified_boundary_v1",
        "manual_verified_boundary",
        "manual_verified_interval",
    }
