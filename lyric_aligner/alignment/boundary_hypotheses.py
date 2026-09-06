"""Versioned N-best boundary hypothesis contract for future Max observers.

A single timestamp throws away useful information about ambiguity.  This module
normalizes observer outputs into a small, backend-neutral hypothesis set with
probability mass, uncertainty and provenance.  Models remain observers: no
hypothesis can mutate subtitle timing by itself.

The contract is intentionally suitable for present singing/CTC backends and future
models (including a future GPT audio observer) without changing downstream risk or
global-optimization APIs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable, Mapping, Sequence


BOUNDARY_HYPOTHESES_SCHEMA_VERSION = "boundary-hypotheses-1.0"
BOUNDARY_HYPOTHESES_AUTHORITY = "observer_evidence_only_never_direct_mutation"


class BoundaryHypothesisError(ValueError):
    pass


@dataclass(frozen=True)
class BoundaryHypothesis:
    hypothesis_id: str
    boundary_ms: int
    probability: float
    uncertainty_ms: int
    rank: int
    evidence_label: str = ""

    def validate(self) -> None:
        if not self.hypothesis_id.strip():
            raise BoundaryHypothesisError("hypothesis_id must be non-empty")
        if self.boundary_ms < 0:
            raise BoundaryHypothesisError("hypothesis boundary_ms must be nonnegative")
        if not math.isfinite(self.probability) or not 0.0 <= self.probability <= 1.0:
            raise BoundaryHypothesisError("hypothesis probability must be within [0,1]")
        if self.uncertainty_ms < 0:
            raise BoundaryHypothesisError("hypothesis uncertainty_ms must be nonnegative")
        if self.rank < 1:
            raise BoundaryHypothesisError("hypothesis rank must be >= 1")


@dataclass(frozen=True)
class BoundaryHypothesisSet:
    schema_version: str
    authority: str
    observer_id: str
    observer_revision: str
    correlation_group: str
    boundary_id: str
    boundary_kind: str
    audio_basis: str
    language: str
    hypotheses: tuple[BoundaryHypothesis, ...]
    residual_probability: float
    posterior_entropy_bits: float
    top1_top2_margin: float | None
    automatic_mutation_allowed: bool = False

    def validate(self) -> None:
        if self.schema_version != BOUNDARY_HYPOTHESES_SCHEMA_VERSION:
            raise BoundaryHypothesisError("unsupported hypothesis schema")
        if self.authority != BOUNDARY_HYPOTHESES_AUTHORITY:
            raise BoundaryHypothesisError("hypothesis authority marker is invalid")
        required = (
            self.observer_id,
            self.observer_revision,
            self.correlation_group,
            self.boundary_id,
            self.audio_basis,
            self.language,
        )
        if any(not str(value).strip() for value in required):
            raise BoundaryHypothesisError("hypothesis set identity is incomplete")
        if self.boundary_kind not in {"start", "end", "internal"}:
            raise BoundaryHypothesisError("boundary_kind must be start, end, or internal")
        if not self.hypotheses:
            raise BoundaryHypothesisError("hypothesis set must not be empty")
        ids: set[str] = set()
        ranks: set[int] = set()
        previous_probability = float("inf")
        total = 0.0
        for hypothesis in self.hypotheses:
            hypothesis.validate()
            if hypothesis.hypothesis_id in ids:
                raise BoundaryHypothesisError("hypothesis ids must be unique")
            if hypothesis.rank in ranks:
                raise BoundaryHypothesisError("hypothesis ranks must be unique")
            ids.add(hypothesis.hypothesis_id)
            ranks.add(hypothesis.rank)
            if hypothesis.probability > previous_probability + 1e-12:
                raise BoundaryHypothesisError("hypotheses must be sorted by descending probability")
            previous_probability = hypothesis.probability
            total += hypothesis.probability
        if ranks != set(range(1, len(self.hypotheses) + 1)):
            raise BoundaryHypothesisError("hypothesis ranks must be contiguous from 1")
        if not math.isfinite(self.residual_probability) or not 0.0 <= self.residual_probability <= 1.0:
            raise BoundaryHypothesisError("residual_probability must be within [0,1]")
        if abs((total + self.residual_probability) - 1.0) > 1e-6:
            raise BoundaryHypothesisError("hypothesis and residual probability mass must sum to 1")
        if not math.isfinite(self.posterior_entropy_bits) or self.posterior_entropy_bits < 0.0:
            raise BoundaryHypothesisError("posterior_entropy_bits must be finite/nonnegative")
        if self.top1_top2_margin is not None:
            if not math.isfinite(self.top1_top2_margin) or not 0.0 <= self.top1_top2_margin <= 1.0:
                raise BoundaryHypothesisError("top1_top2_margin must be within [0,1]")
        if self.automatic_mutation_allowed:
            raise BoundaryHypothesisError("observer hypothesis set cannot directly authorize mutation")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["hypotheses"] = [asdict(item) for item in self.hypotheses]
        return payload


def _entropy_bits(probabilities: Iterable[float]) -> float:
    result = 0.0
    for value in probabilities:
        probability = float(value)
        if probability > 0.0:
            result -= probability * math.log2(probability)
    return float(result)


def _normalize_probability(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryHypothesisError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise BoundaryHypothesisError(f"{label} must be finite/nonnegative")
    return number


def build_boundary_hypothesis_set(
    *,
    observer_id: str,
    observer_revision: str,
    correlation_group: str,
    boundary_id: str,
    boundary_kind: str,
    audio_basis: str,
    language: str,
    candidates: Sequence[Mapping[str, Any]],
    max_hypotheses: int = 5,
) -> BoundaryHypothesisSet:
    """Normalize raw candidate scores/probabilities into a bounded N-best set.

    A candidate may provide either ``probability`` or an arbitrary nonnegative
    ``score``.  Probabilities are normalized again so callers cannot smuggle an
    over-full distribution into the contract.  If only scores are available they
    are normalized by their sum.  ``residual_probability`` remains zero because
    the caller supplied only a finite candidate set; future posterior adapters may
    build the dataclass directly when they can quantify omitted probability mass.
    """

    if max_hypotheses < 1:
        raise BoundaryHypothesisError("max_hypotheses must be >= 1")
    if boundary_kind not in {"start", "end", "internal"}:
        raise BoundaryHypothesisError("boundary_kind must be start, end, or internal")
    if not candidates:
        raise BoundaryHypothesisError("candidate list must not be empty")

    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, raw in enumerate(candidates, start=1):
        if not isinstance(raw, Mapping):
            raise BoundaryHypothesisError("candidate must be an object")
        candidate_id = str(raw.get("hypothesis_id") or f"candidate-{position}").strip()
        if not candidate_id or candidate_id in seen_ids:
            raise BoundaryHypothesisError("candidate ids must be unique/non-empty")
        seen_ids.add(candidate_id)
        try:
            boundary_ms = int(round(float(raw.get("boundary_ms"))))
            uncertainty_ms = int(round(float(raw.get("uncertainty_ms", 0))))
        except (TypeError, ValueError) as exc:
            raise BoundaryHypothesisError("candidate timing is invalid") from exc
        if boundary_ms < 0 or uncertainty_ms < 0:
            raise BoundaryHypothesisError("candidate timing must be nonnegative")
        raw_weight = raw.get("probability") if raw.get("probability") is not None else raw.get("score")
        weight = _normalize_probability(raw_weight, label="candidate probability/score")
        prepared.append(
            {
                "hypothesis_id": candidate_id,
                "boundary_ms": boundary_ms,
                "uncertainty_ms": uncertainty_ms,
                "weight": weight,
                "evidence_label": str(raw.get("evidence_label") or ""),
            }
        )
    total_weight = sum(row["weight"] for row in prepared)
    if total_weight <= 0.0:
        raise BoundaryHypothesisError("candidate probability/score mass must be > 0")
    for row in prepared:
        row["probability"] = row["weight"] / total_weight
    prepared.sort(
        key=lambda row: (
            -float(row["probability"]),
            int(row["uncertainty_ms"]),
            int(row["boundary_ms"]),
            str(row["hypothesis_id"]),
        )
    )
    selected = prepared[:max_hypotheses]
    selected_mass = sum(float(row["probability"]) for row in selected)
    residual = max(0.0, 1.0 - selected_mass)
    hypotheses = tuple(
        BoundaryHypothesis(
            hypothesis_id=str(row["hypothesis_id"]),
            boundary_ms=int(row["boundary_ms"]),
            probability=float(row["probability"]),
            uncertainty_ms=int(row["uncertainty_ms"]),
            rank=rank,
            evidence_label=str(row["evidence_label"]),
        )
        for rank, row in enumerate(selected, start=1)
    )
    entropy = _entropy_bits(
        [*(item.probability for item in hypotheses), residual]
    )
    margin = (
        None
        if len(hypotheses) < 2
        else float(hypotheses[0].probability - hypotheses[1].probability)
    )
    result = BoundaryHypothesisSet(
        schema_version=BOUNDARY_HYPOTHESES_SCHEMA_VERSION,
        authority=BOUNDARY_HYPOTHESES_AUTHORITY,
        observer_id=str(observer_id or "").strip(),
        observer_revision=str(observer_revision or "").strip(),
        correlation_group=str(correlation_group or "").strip(),
        boundary_id=str(boundary_id or "").strip(),
        boundary_kind=boundary_kind,
        audio_basis=str(audio_basis or "").strip(),
        language=str(language or "").strip(),
        hypotheses=hypotheses,
        residual_probability=residual,
        posterior_entropy_bits=entropy,
        top1_top2_margin=margin,
        automatic_mutation_allowed=False,
    )
    result.validate()
    return result


def hypothesis_set_from_dict(payload: Mapping[str, Any]) -> BoundaryHypothesisSet:
    if not isinstance(payload, Mapping):
        raise BoundaryHypothesisError("hypothesis payload must be an object")
    raw_hypotheses = payload.get("hypotheses")
    if not isinstance(raw_hypotheses, list):
        raise BoundaryHypothesisError("hypothesis payload lacks hypotheses")
    hypotheses = tuple(
        BoundaryHypothesis(
            hypothesis_id=str(row.get("hypothesis_id") or ""),
            boundary_ms=int(row.get("boundary_ms")),
            probability=float(row.get("probability")),
            uncertainty_ms=int(row.get("uncertainty_ms")),
            rank=int(row.get("rank")),
            evidence_label=str(row.get("evidence_label") or ""),
        )
        for row in raw_hypotheses
        if isinstance(row, Mapping)
    )
    if len(hypotheses) != len(raw_hypotheses):
        raise BoundaryHypothesisError("hypothesis payload contains malformed rows")
    result = BoundaryHypothesisSet(
        schema_version=str(payload.get("schema_version") or ""),
        authority=str(payload.get("authority") or ""),
        observer_id=str(payload.get("observer_id") or ""),
        observer_revision=str(payload.get("observer_revision") or ""),
        correlation_group=str(payload.get("correlation_group") or ""),
        boundary_id=str(payload.get("boundary_id") or ""),
        boundary_kind=str(payload.get("boundary_kind") or ""),
        audio_basis=str(payload.get("audio_basis") or ""),
        language=str(payload.get("language") or ""),
        hypotheses=hypotheses,
        residual_probability=float(payload.get("residual_probability")),
        posterior_entropy_bits=float(payload.get("posterior_entropy_bits")),
        top1_top2_margin=(
            None
            if payload.get("top1_top2_margin") is None
            else float(payload.get("top1_top2_margin"))
        ),
        automatic_mutation_allowed=bool(payload.get("automatic_mutation_allowed", False)),
    )
    result.validate()
    return result


__all__ = [
    "BOUNDARY_HYPOTHESES_SCHEMA_VERSION",
    "BOUNDARY_HYPOTHESES_AUTHORITY",
    "BoundaryHypothesisError",
    "BoundaryHypothesis",
    "BoundaryHypothesisSet",
    "build_boundary_hypothesis_set",
    "hypothesis_set_from_dict",
]
