"""Risk-aware fallback adjudication for subtitle boundary replacement.

This module is deliberately separate from the existing precise boundary authority.
The precise authority remains the preferred path when independently calibrated
acoustic evidence can authorize a high-confidence mutation.  This module answers
a different question for unresolved boundaries:

    Is a calibrated candidate expected to be materially better than keeping the
    editor boundary, even when the candidate is not precise enough for the strict
    boundary-authority path?

The design is intentionally backend-neutral and does not write SRT files.  It
compares human-gold-calibrated error profiles and emits one of three outcomes:
keep the editor, use a calibrated-better estimate, or use an automatic rescue.
Semantic/structural ambiguity always blocks this fallback because a wrong lyric
identity or occurrence can be far more damaging than a few hundred milliseconds
of timing error.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence


BOUNDARY_RISK_POLICY_VERSION = "1.1"


class BoundaryRiskError(ValueError):
    """Raised when a risk profile or adjudication input is unsafe to compare."""


@dataclass(frozen=True)
class BoundaryRiskPolicy:
    """Policy for the non-precise automatic fallback path.

    The values are intentionally looser than the precise boundary authority but
    still bounded.  They are not model confidence thresholds: the inputs are
    empirical human-gold error distributions from a held-out population.
    """

    minimum_profile_samples: int = 4
    minimum_profile_track_count: int = 4
    minimum_profile_coverage: float = 0.75

    calibrated_better_max_p90_ms: float = 250.0
    calibrated_better_max_catastrophic_fraction: float = 0.02
    calibrated_better_min_absolute_improvement_ms: float = 50.0
    calibrated_better_min_relative_improvement: float = 0.20

    rescue_editor_mean_error_floor_ms: float = 600.0
    rescue_candidate_max_mean_error_ms: float = 300.0
    rescue_candidate_max_p90_ms: float = 600.0
    rescue_candidate_max_catastrophic_fraction: float = 0.05
    rescue_min_absolute_improvement_ms: float = 300.0
    rescue_max_candidate_to_editor_mean_ratio: float = 0.50

    def validate(self) -> None:
        if self.minimum_profile_samples < 1:
            raise BoundaryRiskError("minimum_profile_samples must be >= 1")
        if self.minimum_profile_track_count < 1:
            raise BoundaryRiskError("minimum_profile_track_count must be >= 1")
        if not 0.0 < self.minimum_profile_coverage <= 1.0:
            raise BoundaryRiskError("minimum_profile_coverage must be within (0,1]")

        nonnegative = (
            self.calibrated_better_max_p90_ms,
            self.calibrated_better_min_absolute_improvement_ms,
            self.rescue_editor_mean_error_floor_ms,
            self.rescue_candidate_max_mean_error_ms,
            self.rescue_candidate_max_p90_ms,
            self.rescue_min_absolute_improvement_ms,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in nonnegative):
            raise BoundaryRiskError("risk policy millisecond thresholds must be finite/nonnegative")
        fractions = (
            self.calibrated_better_max_catastrophic_fraction,
            self.calibrated_better_min_relative_improvement,
            self.rescue_candidate_max_catastrophic_fraction,
            self.rescue_max_candidate_to_editor_mean_ratio,
        )
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in fractions):
            raise BoundaryRiskError("risk policy fractions must be within [0,1]")
        if self.rescue_candidate_max_p90_ms < self.calibrated_better_max_p90_ms:
            raise BoundaryRiskError("rescue p90 ceiling cannot be stricter than calibrated-better ceiling")
        if self.rescue_candidate_max_catastrophic_fraction < self.calibrated_better_max_catastrophic_fraction:
            raise BoundaryRiskError(
                "rescue catastrophic ceiling cannot be stricter than calibrated-better ceiling"
            )


@dataclass(frozen=True)
class BoundaryErrorProfile:
    """Empirical boundary-error distribution for one narrowly scoped estimator."""

    profile_id: str
    boundary_kind: str
    population: str
    sample_count: int
    predicted_count: int
    distinct_track_count: int
    coverage: float
    mean_effective_error_ms: float
    median_effective_error_ms: float
    p90_effective_error_ms: float
    max_effective_error_ms: float
    catastrophic_fraction: float
    catastrophic_threshold_ms: float
    production_authoritative: bool = False
    provenance_sha256: str = ""

    def validate(self) -> None:
        if not self.profile_id.strip():
            raise BoundaryRiskError("profile_id must be non-empty")
        if self.boundary_kind not in {"start", "end", "internal"}:
            raise BoundaryRiskError("boundary_kind must be start, end, or internal")
        if not self.population.strip():
            raise BoundaryRiskError("profile population must be non-empty")
        if self.sample_count < 1 or self.predicted_count < 0 or self.predicted_count > self.sample_count:
            raise BoundaryRiskError("profile sample/prediction counts are invalid")
        if self.distinct_track_count < 1:
            raise BoundaryRiskError("profile distinct_track_count must be >= 1")
        if not math.isfinite(self.coverage) or not 0.0 <= self.coverage <= 1.0:
            raise BoundaryRiskError("profile coverage must be within [0,1]")
        if abs(self.coverage - (self.predicted_count / self.sample_count)) > 1e-6:
            raise BoundaryRiskError("profile coverage does not match counts")
        metrics = (
            self.mean_effective_error_ms,
            self.median_effective_error_ms,
            self.p90_effective_error_ms,
            self.max_effective_error_ms,
            self.catastrophic_threshold_ms,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in metrics):
            raise BoundaryRiskError("profile timing metrics must be finite/nonnegative")
        if not (
            self.median_effective_error_ms
            <= self.p90_effective_error_ms
            <= self.max_effective_error_ms
        ):
            raise BoundaryRiskError("profile percentile/max metrics must be monotonic")
        if not math.isfinite(self.catastrophic_fraction) or not 0.0 <= self.catastrophic_fraction <= 1.0:
            raise BoundaryRiskError("profile catastrophic_fraction must be within [0,1]")
        if self.production_authoritative:
            digest = self.provenance_sha256.strip().lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise BoundaryRiskError(
                    "production-authoritative profile requires a provenance SHA-256"
                )

    def with_production_authority(self, provenance_sha256: str) -> "BoundaryErrorProfile":
        """Return a copy marked authoritative after the caller validates provenance."""

        profile = replace(
            self,
            production_authoritative=True,
            provenance_sha256=str(provenance_sha256 or "").strip().lower(),
        )
        profile.validate()
        return profile


@dataclass(frozen=True)
class BoundaryLocalSupport:
    """Candidate-specific support required in addition to population risk.

    A good estimator-wide holdout profile does not prove that one current prediction is
    sane.  This contract binds a locally estimated uncertainty to the exact candidate so
    an aggregate profile cannot authorize an arbitrary outlier timestamp by itself.
    """

    support_id: str
    boundary_kind: str
    candidate_ms: int
    estimated_p90_error_ms: float
    contradictory: bool = False
    production_authoritative: bool = False
    provenance_sha256: str = ""

    def validate(self) -> None:
        if not self.support_id.strip():
            raise BoundaryRiskError("local support id must be non-empty")
        if self.boundary_kind not in {"start", "end", "internal"}:
            raise BoundaryRiskError("local support boundary_kind is invalid")
        if self.candidate_ms < 0:
            raise BoundaryRiskError("local support candidate_ms must be nonnegative")
        if not math.isfinite(self.estimated_p90_error_ms) or self.estimated_p90_error_ms < 0.0:
            raise BoundaryRiskError("local support estimated_p90_error_ms must be finite/nonnegative")
        if self.production_authoritative:
            digest = self.provenance_sha256.strip().lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise BoundaryRiskError(
                    "production-authoritative local support requires a provenance SHA-256"
                )

    def with_production_authority(self, provenance_sha256: str) -> "BoundaryLocalSupport":
        support = replace(
            self,
            production_authoritative=True,
            provenance_sha256=str(provenance_sha256 or "").strip().lower(),
        )
        support.validate()
        return support


@dataclass(frozen=True)
class BoundaryRiskDecision:
    policy_version: str
    action: str
    selected_ms: int
    editor_ms: int
    candidate_ms: int
    estimated_editor_mean_error_ms: float
    estimated_candidate_mean_error_ms: float
    estimated_candidate_p90_error_ms: float
    expected_improvement_ms: float
    relative_improvement: float
    candidate_catastrophic_fraction: float
    semantic_ambiguity: bool
    structural_ambiguity: bool
    global_constraints_satisfied: bool
    reason: str
    local_support_id: str | None = None
    local_support_p90_error_ms: float | None = None
    local_support_authoritative: bool = False

    @property
    def automatic_mutation_allowed(self) -> bool:
        return self.action in {"auto_calibrated_better", "auto_rescue"}


def _nearest_rank(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise BoundaryRiskError("percentile requires non-empty values")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(float(fraction) * len(ordered)))
    return float(ordered[rank - 1])


def build_error_profile(
    *,
    profile_id: str,
    boundary_kind: str,
    population: str,
    effective_errors_ms: Iterable[float | int | None],
    track_ids: Iterable[str],
    catastrophic_threshold_ms: float,
) -> BoundaryErrorProfile:
    """Build a descriptive profile from a fixed human-gold comparison population.

    ``None`` errors represent unavailable predictions and reduce coverage.  This
    helper intentionally does not grant production authority; that can only be
    attached by the artifact-owning caller after validating the source benchmark
    and its provenance.
    """

    raw_errors = list(effective_errors_ms)
    tracks = [str(value or "").strip() for value in track_ids]
    if not raw_errors or len(raw_errors) != len(tracks):
        raise BoundaryRiskError("error and track populations must be non-empty and equal length")
    if any(not track for track in tracks):
        raise BoundaryRiskError("all profile rows must identify a track")
    threshold = float(catastrophic_threshold_ms)
    if not math.isfinite(threshold) or threshold <= 0.0:
        raise BoundaryRiskError("catastrophic_threshold_ms must be finite and > 0")

    errors: list[float] = []
    predicted_tracks: list[str] = []
    for raw, track in zip(raw_errors, tracks):
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise BoundaryRiskError("effective error is not numeric") from exc
        if not math.isfinite(value) or value < 0.0:
            raise BoundaryRiskError("effective errors must be finite/nonnegative")
        errors.append(value)
        predicted_tracks.append(track)
    if not errors:
        raise BoundaryRiskError("profile has no predictions")

    profile = BoundaryErrorProfile(
        profile_id=str(profile_id or "").strip(),
        boundary_kind=str(boundary_kind or "").strip(),
        population=str(population or "").strip(),
        sample_count=len(raw_errors),
        predicted_count=len(errors),
        distinct_track_count=len(set(predicted_tracks)),
        coverage=len(errors) / len(raw_errors),
        mean_effective_error_ms=float(fmean(errors)),
        median_effective_error_ms=float(median(errors)),
        p90_effective_error_ms=_nearest_rank(errors, 0.90),
        max_effective_error_ms=float(max(errors)),
        catastrophic_fraction=(
            sum(value >= threshold for value in errors) / len(errors)
        ),
        catastrophic_threshold_ms=threshold,
    )
    profile.validate()
    return profile


def build_profile_from_calibration_artifact(
    artifact: Mapping[str, Any],
    *,
    population: str = "holdout",
    production_authoritative: bool = False,
) -> BoundaryErrorProfile:
    """Build a risk profile from an existing boundary-calibration artifact.

    This function deliberately does not re-implement the production calibration
    validator.  ``production_authoritative=True`` is only appropriate after the
    caller has validated the exact calibration artifact with the existing
    production-calibration trust boundary.
    """

    if population not in {"calibration", "holdout", "all"}:
        raise BoundaryRiskError("population must be calibration, holdout, or all")
    records = artifact.get("records")
    scope = artifact.get("scope")
    policy = artifact.get("policy")
    if not isinstance(records, list) or not isinstance(scope, Mapping) or not isinstance(policy, Mapping):
        raise BoundaryRiskError("calibration artifact is missing records/scope/policy")
    boundary_kind = str(scope.get("boundary_kind") or "").strip()
    filtered = records if population == "all" else [
        row for row in records if isinstance(row, Mapping) and str(row.get("partition") or "") == population
    ]
    if not filtered:
        raise BoundaryRiskError("calibration artifact has no rows for requested population")

    errors: list[float | int | None] = []
    tracks: list[str] = []
    for row in filtered:
        if not isinstance(row, Mapping):
            raise BoundaryRiskError("calibration artifact contains a malformed record")
        errors.append(row.get("effective_error_ms"))
        tracks.append(str(row.get("track") or ""))

    try:
        fps = float(policy.get("fps", 30.0))
        catastrophic_frames = float(policy.get("catastrophic_error_frames", 15.0))
    except (TypeError, ValueError) as exc:
        raise BoundaryRiskError("calibration policy timing values are invalid") from exc
    if not math.isfinite(fps) or fps <= 0.0:
        raise BoundaryRiskError("calibration policy fps must be finite and > 0")
    catastrophic_threshold_ms = catastrophic_frames * (1000.0 / fps)
    profile = build_error_profile(
        profile_id=(
            f"{str(artifact.get('backend_id') or 'unknown')}:{boundary_kind}:{population}"
        ),
        boundary_kind=boundary_kind,
        population=population,
        effective_errors_ms=errors,
        track_ids=tracks,
        catastrophic_threshold_ms=catastrophic_threshold_ms,
    )
    if production_authoritative:
        return profile.with_production_authority(str(artifact.get("artifact_sha256") or ""))
    return profile


def _profile_ready(profile: BoundaryErrorProfile, policy: BoundaryRiskPolicy) -> bool:
    profile.validate()
    return bool(
        profile.production_authoritative
        and profile.predicted_count >= policy.minimum_profile_samples
        and profile.distinct_track_count >= policy.minimum_profile_track_count
        and profile.coverage >= policy.minimum_profile_coverage
    )


def adjudicate_calibrated_fallback(
    *,
    editor_ms: int,
    candidate_ms: int,
    editor_profile: BoundaryErrorProfile,
    candidate_profile: BoundaryErrorProfile,
    local_support: BoundaryLocalSupport | None = None,
    semantic_ambiguity: bool = False,
    structural_ambiguity: bool = False,
    global_constraints_satisfied: bool = True,
    policy: BoundaryRiskPolicy | None = None,
) -> BoundaryRiskDecision:
    """Choose whether a non-precise calibrated candidate should replace editor timing.

    This function is intentionally a *fallback* after the strict precise authority.
    It may authorize a less precise estimate only when human-gold holdout evidence
    shows a material improvement over the editor baseline.  Structural/identity
    ambiguity or topology violations always block automatic mutation.
    """

    policy = policy or BoundaryRiskPolicy()
    policy.validate()
    try:
        editor_ms = int(round(float(editor_ms)))
        candidate_ms = int(round(float(candidate_ms)))
    except (TypeError, ValueError) as exc:
        raise BoundaryRiskError("editor/candidate timing must be numeric") from exc
    if editor_ms < 0 or candidate_ms < 0:
        raise BoundaryRiskError("editor/candidate timing must be nonnegative")
    editor_profile.validate()
    candidate_profile.validate()
    if editor_profile.boundary_kind != candidate_profile.boundary_kind:
        raise BoundaryRiskError("editor and candidate profiles must share boundary_kind")
    if editor_profile.population != candidate_profile.population:
        raise BoundaryRiskError("editor and candidate profiles must share population")
    if not math.isclose(
        float(editor_profile.catastrophic_threshold_ms),
        float(candidate_profile.catastrophic_threshold_ms),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise BoundaryRiskError("editor and candidate profiles must share catastrophic threshold")
    if local_support is not None:
        local_support.validate()
        if local_support.boundary_kind != candidate_profile.boundary_kind:
            raise BoundaryRiskError("local support boundary_kind does not match candidate profile")
        if int(local_support.candidate_ms) != candidate_ms:
            raise BoundaryRiskError("local support is bound to another candidate timestamp")

    editor_mean = float(editor_profile.mean_effective_error_ms)
    candidate_mean = float(candidate_profile.mean_effective_error_ms)
    improvement = editor_mean - candidate_mean
    relative = improvement / editor_mean if editor_mean > 0.0 else 0.0

    def decision(action: str, selected: int, reason: str) -> BoundaryRiskDecision:
        return BoundaryRiskDecision(
            policy_version=BOUNDARY_RISK_POLICY_VERSION,
            action=action,
            selected_ms=selected,
            editor_ms=editor_ms,
            candidate_ms=candidate_ms,
            estimated_editor_mean_error_ms=editor_mean,
            estimated_candidate_mean_error_ms=candidate_mean,
            estimated_candidate_p90_error_ms=float(candidate_profile.p90_effective_error_ms),
            expected_improvement_ms=improvement,
            relative_improvement=relative,
            candidate_catastrophic_fraction=float(candidate_profile.catastrophic_fraction),
            semantic_ambiguity=bool(semantic_ambiguity),
            structural_ambiguity=bool(structural_ambiguity),
            global_constraints_satisfied=bool(global_constraints_satisfied),
            reason=reason,
            local_support_id=None if local_support is None else local_support.support_id,
            local_support_p90_error_ms=(
                None if local_support is None else float(local_support.estimated_p90_error_ms)
            ),
            local_support_authoritative=bool(
                local_support is not None and local_support.production_authoritative
            ),
        )

    if semantic_ambiguity or structural_ambiguity:
        return decision(
            "structural_review",
            editor_ms,
            "timing fallback cannot resolve lyric identity or structural ambiguity",
        )
    if not global_constraints_satisfied:
        return decision(
            "structural_review",
            editor_ms,
            "candidate violates global subtitle topology/ordering constraints",
        )
    if editor_profile.population != "holdout":
        return decision(
            "keep_editor",
            editor_ms,
            "automatic expected-loss fallback requires holdout risk profiles",
        )
    if not _profile_ready(editor_profile, policy) or not _profile_ready(candidate_profile, policy):
        return decision(
            "keep_editor",
            editor_ms,
            "human-gold holdout risk profiles are not sufficiently authoritative/covered",
        )
    if local_support is None:
        return decision(
            "keep_editor",
            editor_ms,
            "aggregate holdout risk cannot authorize a current candidate without local support",
        )
    if not local_support.production_authoritative:
        return decision(
            "keep_editor",
            editor_ms,
            "current candidate local support is not production-authoritative",
        )
    if local_support.contradictory:
        return decision(
            "keep_editor",
            editor_ms,
            "current candidate local evidence is contradictory",
        )
    if candidate_profile.catastrophic_fraction > policy.rescue_candidate_max_catastrophic_fraction:
        return decision(
            "keep_editor",
            editor_ms,
            "candidate catastrophic tail risk exceeds automatic-rescue ceiling",
        )

    calibrated_better = bool(
        candidate_profile.p90_effective_error_ms <= policy.calibrated_better_max_p90_ms
        and local_support.estimated_p90_error_ms <= policy.calibrated_better_max_p90_ms
        and candidate_profile.catastrophic_fraction
        <= policy.calibrated_better_max_catastrophic_fraction
        and improvement >= policy.calibrated_better_min_absolute_improvement_ms
        and relative >= policy.calibrated_better_min_relative_improvement
        and candidate_profile.p90_effective_error_ms <= editor_profile.p90_effective_error_ms
    )
    if calibrated_better:
        return decision(
            "auto_calibrated_better",
            candidate_ms,
            "holdout-calibrated candidate has materially lower typical/tail error than editor",
        )

    rescue = bool(
        editor_mean >= policy.rescue_editor_mean_error_floor_ms
        and candidate_mean <= policy.rescue_candidate_max_mean_error_ms
        and candidate_profile.p90_effective_error_ms <= policy.rescue_candidate_max_p90_ms
        and local_support.estimated_p90_error_ms <= policy.rescue_candidate_max_p90_ms
        and candidate_profile.catastrophic_fraction
        <= policy.rescue_candidate_max_catastrophic_fraction
        and improvement >= policy.rescue_min_absolute_improvement_ms
        and candidate_mean
        <= editor_mean * policy.rescue_max_candidate_to_editor_mean_ratio
    )
    if rescue:
        return decision(
            "auto_rescue",
            candidate_ms,
            "editor baseline is demonstrably poor and calibrated candidate is substantially safer",
        )

    return decision(
        "keep_editor",
        editor_ms,
        "candidate has not demonstrated enough holdout risk reduction to justify automatic fallback mutation",
    )


__all__ = [
    "BOUNDARY_RISK_POLICY_VERSION",
    "BoundaryRiskError",
    "BoundaryRiskPolicy",
    "BoundaryErrorProfile",
    "BoundaryLocalSupport",
    "BoundaryRiskDecision",
    "build_error_profile",
    "build_profile_from_calibration_artifact",
    "adjudicate_calibrated_fallback",
]
