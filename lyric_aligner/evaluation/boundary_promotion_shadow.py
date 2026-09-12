"""Pre-gold shadow gate for boundary-level timing promotion.

This module closes the evaluation loop between a frozen timing-decision pack and
later human truth.  It is deliberately *not* a production authority provider:
selection is locked before gold is read, each promoted boundary must cite an
independent evidence family/correlation group, and evaluation can only return a
shadow-gate result.  A separate reviewed production-authority change is required
before Best-Safe may consume any machine timing promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from lyric_aligner.evaluation.decision_validation import evaluate_timing_decisions
from lyric_aligner.evaluation.timing_decision_pack import (
    merge_timing_decision_gold,
    verify_selection_lock,
)
from lyric_aligner.evaluation.timing_decision_review import (
    REVIEW_PARTITIONS,
    REVIEW_SCHEMA_VERSION,
    build_review_manifest,
    validate_review_response,
)

SELECTION_SCHEMA_VERSION = "boundary-promotion-shadow-selection-1.0"
EVALUATION_SCHEMA_VERSION = "boundary-promotion-shadow-evaluation-1.0"
DECISIONS_SCHEMA_VERSION = "boundary-promotion-machine-decisions-1.0"
POLICY_ID = "smart-floor-boundary-promotion-shadow-1.0"

DEFAULT_GATE_POLICY: dict[str, Any] = {
    "min_valid_count": 24,
    "min_distinct_tracks": 4,
    "min_promoted_count": 8,
    "min_promoted_distinct_tracks": 4,
    "max_new_over_500ms_count": 0,
    "max_harmful_over_100ms_rate": 0.05,
    "max_p90_regression_ms": 0.0,
    "min_mean_gain_ms": 1.0,
    "min_track_equal_mean_gain_ms": 1.0,
    "require_track_bootstrap_lower_bound_nonnegative": True,
}


class BoundaryPromotionShadowError(ValueError):
    """Raised when a shadow promotion contract is stale, incomplete or unsafe."""


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: Any, *, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise BoundaryPromotionShadowError(f"{label} must be a SHA-256 hex string")
    return text


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BoundaryPromotionShadowError(f"{label} must be a nonnegative integer")
    return value


def _positive_number(value: Any, *, label: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise BoundaryPromotionShadowError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryPromotionShadowError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        operator = ">= 0" if allow_zero else "> 0"
        raise BoundaryPromotionShadowError(f"{label} must be finite and {operator}")
    return number


def _validate_gate_policy(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    policy = deepcopy(DEFAULT_GATE_POLICY if raw is None else dict(raw))
    required = set(DEFAULT_GATE_POLICY)
    if set(policy) != required:
        missing = sorted(required - set(policy))
        extra = sorted(set(policy) - required)
        raise BoundaryPromotionShadowError(
            f"shadow gate policy fields mismatch; missing={missing}, extra={extra}"
        )
    for key in (
        "min_valid_count",
        "min_distinct_tracks",
        "min_promoted_count",
        "min_promoted_distinct_tracks",
        "max_new_over_500ms_count",
    ):
        policy[key] = _nonnegative_int(policy[key], label=f"gate_policy.{key}")
    if policy["min_valid_count"] < 1:
        raise BoundaryPromotionShadowError("gate_policy.min_valid_count must be >= 1")
    if policy["min_distinct_tracks"] < 1:
        raise BoundaryPromotionShadowError("gate_policy.min_distinct_tracks must be >= 1")
    if policy["min_promoted_count"] < 1:
        raise BoundaryPromotionShadowError("gate_policy.min_promoted_count must be >= 1")
    if policy["min_promoted_distinct_tracks"] < 1:
        raise BoundaryPromotionShadowError("gate_policy.min_promoted_distinct_tracks must be >= 1")
    rate = _positive_number(
        policy["max_harmful_over_100ms_rate"],
        label="gate_policy.max_harmful_over_100ms_rate",
        allow_zero=True,
    )
    if rate > 1:
        raise BoundaryPromotionShadowError("gate_policy.max_harmful_over_100ms_rate must be <= 1")
    policy["max_harmful_over_100ms_rate"] = rate
    for key in ("max_p90_regression_ms", "min_mean_gain_ms", "min_track_equal_mean_gain_ms"):
        policy[key] = _positive_number(policy[key], label=f"gate_policy.{key}", allow_zero=True)
    flag = policy["require_track_bootstrap_lower_bound_nonnegative"]
    if not isinstance(flag, bool):
        raise BoundaryPromotionShadowError(
            "gate_policy.require_track_bootstrap_lower_bound_nonnegative must be boolean"
        )
    return policy


def _case_index(pack: Mapping[str, Any]) -> tuple[str, dict[str, Mapping[str, Any]]]:
    lock = verify_selection_lock(pack)
    cases = pack.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BoundaryPromotionShadowError("timing decision pack requires cases")
    by_id: dict[str, Mapping[str, Any]] = {}
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise BoundaryPromotionShadowError("timing decision pack contains non-object case")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in by_id:
            raise BoundaryPromotionShadowError("timing decision pack case ids must be unique/nonempty")
        _nonnegative_int(raw.get("old_final_ms"), label=f"case {case_id} old_final_ms")
        _nonnegative_int(raw.get("hybrid_ms"), label=f"case {case_id} hybrid_ms")
        by_id[case_id] = raw
    return lock, by_id


def freeze_boundary_promotion_selection(
    pack: Mapping[str, Any],
    machine_decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze complete selector decisions before any gold is read.

    `promote_candidate` decisions must cite an evidence family that is independent
    of the candidate's correlation group.  This is an input contract, not a claim
    that the evidence is accurate; accuracy is established only by later blind or
    holdout truth.
    """
    lock, case_by_id = _case_index(pack)
    if machine_decisions.get("schema_version") != DECISIONS_SCHEMA_VERSION:
        raise BoundaryPromotionShadowError("machine decisions schema mismatch")
    if machine_decisions.get("selection_lock_sha256") != lock:
        raise BoundaryPromotionShadowError("machine decisions belong to another timing selection lock")
    if machine_decisions.get("gold_read") is not False:
        raise BoundaryPromotionShadowError("machine decisions must be frozen before gold is read")
    selector_id = str(machine_decisions.get("selector_id") or "").strip()
    selector_revision = str(machine_decisions.get("selector_revision") or "").strip()
    if not selector_id or not selector_revision:
        raise BoundaryPromotionShadowError("selector_id and selector_revision are required")
    selector_code_sha256 = _sha256_text(
        machine_decisions.get("selector_code_sha256"), label="selector_code_sha256"
    )
    gate_policy = _validate_gate_policy(machine_decisions.get("gate_policy"))
    raw_decisions = machine_decisions.get("decisions")
    if not isinstance(raw_decisions, list):
        raise BoundaryPromotionShadowError("machine decisions must contain a decisions list")

    decisions_by_id: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(raw_decisions, start=1):
        if not isinstance(raw, Mapping):
            raise BoundaryPromotionShadowError(f"decision {position} must be an object")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or case_id not in case_by_id or case_id in decisions_by_id:
            raise BoundaryPromotionShadowError("decision id is unknown or duplicate")
        case = case_by_id[case_id]
        choice = str(raw.get("choice") or "").strip()
        if choice not in {"keep_smart", "promote_candidate"}:
            raise BoundaryPromotionShadowError(f"decision {case_id} has invalid choice")
        expected_smart_ms = _nonnegative_int(
            raw.get("expected_smart_ms"), label=f"decision {case_id} expected_smart_ms"
        )
        expected_candidate_ms = _nonnegative_int(
            raw.get("expected_candidate_ms"), label=f"decision {case_id} expected_candidate_ms"
        )
        if expected_smart_ms != int(case["old_final_ms"]):
            raise BoundaryPromotionShadowError(f"decision {case_id} Smart boundary is stale")
        if expected_candidate_ms != int(case["hybrid_ms"]):
            raise BoundaryPromotionShadowError(f"decision {case_id} candidate boundary is stale")
        normalized: dict[str, Any] = {
            "id": case_id,
            "choice": choice,
            "expected_smart_ms": expected_smart_ms,
            "expected_candidate_ms": expected_candidate_ms,
        }
        if choice == "promote_candidate":
            if expected_candidate_ms == expected_smart_ms:
                raise BoundaryPromotionShadowError(
                    f"decision {case_id} cannot promote an unchanged candidate"
                )
            if raw.get("independent_timing_evidence") is not True:
                raise BoundaryPromotionShadowError(
                    f"decision {case_id} promotion lacks independent timing evidence"
                )
            candidate_family = str(raw.get("candidate_family") or "").strip()
            candidate_group = str(raw.get("candidate_correlation_group") or "").strip()
            evidence_family = str(raw.get("independent_evidence_family") or "").strip()
            evidence_group = str(raw.get("independent_evidence_correlation_group") or "").strip()
            if not all((candidate_family, candidate_group, evidence_family, evidence_group)):
                raise BoundaryPromotionShadowError(
                    f"decision {case_id} promotion requires candidate/evidence family and correlation groups"
                )
            if candidate_group == evidence_group:
                raise BoundaryPromotionShadowError(
                    f"decision {case_id} uses correlated candidate/evidence groups"
                )
            normalized.update(
                {
                    "independent_timing_evidence": True,
                    "candidate_family": candidate_family,
                    "candidate_correlation_group": candidate_group,
                    "independent_evidence_family": evidence_family,
                    "independent_evidence_correlation_group": evidence_group,
                    "independent_evidence_sha256": _sha256_text(
                        raw.get("independent_evidence_sha256"),
                        label=f"decision {case_id} independent_evidence_sha256",
                    ),
                    "reason_code": str(raw.get("reason_code") or "").strip(),
                }
            )
            if not normalized["reason_code"]:
                raise BoundaryPromotionShadowError(
                    f"decision {case_id} promotion requires reason_code"
                )
        decisions_by_id[case_id] = normalized

    if set(decisions_by_id) != set(case_by_id):
        missing = sorted(set(case_by_id) - set(decisions_by_id))
        extra = sorted(set(decisions_by_id) - set(case_by_id))
        raise BoundaryPromotionShadowError(
            f"machine decisions must account for every frozen case exactly once; missing={missing}, extra={extra}"
        )

    selection: dict[str, Any] = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "purpose": "pre_gold_boundary_promotion_shadow_never_production_authority",
        "gold_read": False,
        "production_authority_granted": False,
        "timing_selection_lock_sha256": lock,
        "task_fingerprint_sha256": pack.get("task_fingerprint_sha256"),
        "final_mix_sha256": pack.get("final_mix_sha256"),
        "selector_id": selector_id,
        "selector_revision": selector_revision,
        "selector_code_sha256": selector_code_sha256,
        "gate_policy": gate_policy,
        "case_count": len(case_by_id),
        "promoted_case_count": sum(
            row["choice"] == "promote_candidate" for row in decisions_by_id.values()
        ),
        "decisions": [decisions_by_id[case_id] for case_id in sorted(decisions_by_id)],
    }
    selection["selection_payload_sha256"] = _sha256_payload(selection)
    return selection


def verify_boundary_promotion_selection(
    pack: Mapping[str, Any], selection: Mapping[str, Any]
) -> str:
    lock, case_by_id = _case_index(pack)
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION or selection.get("policy_id") != POLICY_ID:
        raise BoundaryPromotionShadowError("boundary promotion selection identity mismatch")
    if selection.get("gold_read") is not False or selection.get("production_authority_granted") is not False:
        raise BoundaryPromotionShadowError("boundary promotion selection must remain pre-gold shadow-only")
    if selection.get("timing_selection_lock_sha256") != lock:
        raise BoundaryPromotionShadowError("boundary promotion selection belongs to another timing pack")
    selector_id = str(selection.get("selector_id") or "").strip()
    selector_revision = str(selection.get("selector_revision") or "").strip()
    if not selector_id or not selector_revision:
        raise BoundaryPromotionShadowError("boundary promotion selection is missing selector identity")
    _sha256_text(selection.get("selector_code_sha256"), label="selector_code_sha256")
    _validate_gate_policy(selection.get("gate_policy"))
    if selection.get("task_fingerprint_sha256") != pack.get("task_fingerprint_sha256"):
        raise BoundaryPromotionShadowError("boundary promotion selection task fingerprint mismatch")
    if selection.get("final_mix_sha256") != pack.get("final_mix_sha256"):
        raise BoundaryPromotionShadowError("boundary promotion selection final-mix identity mismatch")
    if selection.get("case_count") != len(case_by_id):
        raise BoundaryPromotionShadowError("boundary promotion selection case_count mismatch")
    decisions = selection.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(case_by_id):
        raise BoundaryPromotionShadowError("boundary promotion selection has incomplete decisions")
    seen: set[str] = set()
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise BoundaryPromotionShadowError("boundary promotion selection contains non-object decision")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or case_id not in case_by_id or case_id in seen:
            raise BoundaryPromotionShadowError("boundary promotion selection has unknown/duplicate decision")
        seen.add(case_id)
        case = case_by_id[case_id]
        if raw.get("expected_smart_ms") != case.get("old_final_ms") or raw.get("expected_candidate_ms") != case.get("hybrid_ms"):
            raise BoundaryPromotionShadowError("boundary promotion selection contains stale boundary values")
        if raw.get("choice") == "promote_candidate":
            if raw.get("expected_candidate_ms") == raw.get("expected_smart_ms"):
                raise BoundaryPromotionShadowError("promoted decision cannot target an unchanged candidate")
            if raw.get("independent_timing_evidence") is not True:
                raise BoundaryPromotionShadowError("promoted decision lost independent timing evidence")
            candidate_family = str(raw.get("candidate_family") or "").strip()
            candidate_group = str(raw.get("candidate_correlation_group") or "").strip()
            evidence_family = str(raw.get("independent_evidence_family") or "").strip()
            evidence_group = str(raw.get("independent_evidence_correlation_group") or "").strip()
            reason_code = str(raw.get("reason_code") or "").strip()
            if not all((candidate_family, candidate_group, evidence_family, evidence_group, reason_code)):
                raise BoundaryPromotionShadowError("promoted decision lost evidence lineage fields")
            if candidate_group == evidence_group:
                raise BoundaryPromotionShadowError("promoted decision uses correlated evidence")
            _sha256_text(
                raw.get("independent_evidence_sha256"),
                label=f"decision {case_id} independent_evidence_sha256",
            )
        elif raw.get("choice") != "keep_smart":
            raise BoundaryPromotionShadowError("boundary promotion selection has invalid choice")
    if seen != set(case_by_id):
        raise BoundaryPromotionShadowError("boundary promotion selection does not cover every frozen case")
    promoted_count = sum(
        isinstance(row, Mapping) and row.get("choice") == "promote_candidate"
        for row in decisions
    )
    if selection.get("promoted_case_count") != promoted_count:
        raise BoundaryPromotionShadowError("boundary promotion selection promoted_case_count mismatch")
    expected = selection.get("selection_payload_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise BoundaryPromotionShadowError("boundary promotion selection is missing payload hash")
    bare = dict(selection)
    bare.pop("selection_payload_sha256", None)
    actual = _sha256_payload(bare)
    if actual != expected:
        raise BoundaryPromotionShadowError("boundary promotion selection payload hash mismatch")
    return expected


def _verify_review_manifest_binding(
    pack: Mapping[str, Any],
    selection_hash: str,
    review_manifest: Mapping[str, Any],
) -> str:
    if review_manifest.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise BoundaryPromotionShadowError("boundary promotion review manifest schema mismatch")
    expected_manifest_sha = _sha256_text(
        review_manifest.get("manifest_sha256"),
        label="review manifest manifest_sha256",
    )
    bare = dict(review_manifest)
    bare.pop("manifest_sha256", None)
    if _sha256_payload(bare) != expected_manifest_sha:
        raise BoundaryPromotionShadowError("boundary promotion review manifest hash mismatch")
    lock, case_by_id = _case_index(pack)
    if review_manifest.get("selection_lock_sha256") != lock:
        raise BoundaryPromotionShadowError("boundary promotion review manifest belongs to another timing pack")
    if review_manifest.get("final_mix_sha256") != pack.get("final_mix_sha256"):
        raise BoundaryPromotionShadowError("boundary promotion review manifest final-mix identity mismatch")
    raw_review_cases = review_manifest.get("cases")
    if not isinstance(raw_review_cases, list) or len(raw_review_cases) != len(case_by_id):
        raise BoundaryPromotionShadowError("boundary promotion review manifest case_count mismatch")
    if review_manifest.get("case_count") != len(case_by_id):
        raise BoundaryPromotionShadowError("boundary promotion review manifest case_count metadata mismatch")
    seen_review_ids: set[str] = set()
    for raw_case in raw_review_cases:
        if not isinstance(raw_case, Mapping):
            raise BoundaryPromotionShadowError("boundary promotion review manifest contains non-object case")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or case_id not in case_by_id or case_id in seen_review_ids:
            raise BoundaryPromotionShadowError("boundary promotion review manifest has unknown/duplicate case")
        seen_review_ids.add(case_id)
        source_case = case_by_id[case_id]
        if raw_case.get("target_text") != source_case.get("target_text"):
            raise BoundaryPromotionShadowError("boundary promotion review manifest target text mismatch")
        if raw_case.get("boundary_kind") != source_case.get("boundary_kind"):
            raise BoundaryPromotionShadowError("boundary promotion review manifest boundary kind mismatch")
        if raw_case.get("clip_start_ms") != source_case.get("clip_start_ms"):
            raise BoundaryPromotionShadowError("boundary promotion review manifest clip start mismatch")
        expected_duration = int(source_case["clip_end_ms"]) - int(source_case["clip_start_ms"])
        if raw_case.get("clip_duration_ms") != expected_duration:
            raise BoundaryPromotionShadowError("boundary promotion review manifest clip duration mismatch")
        if any(key in raw_case for key in ("old_final_ms", "hybrid_ms", "private_identity")):
            raise BoundaryPromotionShadowError("boundary promotion review manifest leaks candidate/private fields")
    if seen_review_ids != set(case_by_id):
        raise BoundaryPromotionShadowError("boundary promotion review manifest does not cover frozen cases")
    if (
        review_manifest.get("candidate_positions_hidden") is not True
        or review_manifest.get("gold_hidden_during_selection") is not True
    ):
        raise BoundaryPromotionShadowError("boundary promotion review manifest is not candidate-blind")
    if review_manifest.get("boundary_promotion_selection_sha256") != selection_hash:
        raise BoundaryPromotionShadowError(
            "boundary promotion review manifest is not bound to the frozen promotion selection"
        )
    frozen_partition = review_manifest.get("boundary_promotion_partition")
    if frozen_partition not in REVIEW_PARTITIONS:
        raise BoundaryPromotionShadowError(
            "boundary promotion review manifest is missing a valid frozen partition"
        )
    expected_manifest = build_review_manifest(
        pack,
        boundary_promotion_selection_sha256=selection_hash,
        boundary_promotion_partition=str(frozen_partition),
    )
    if dict(review_manifest) != expected_manifest:
        raise BoundaryPromotionShadowError(
            "boundary promotion review manifest differs from deterministic candidate-blind material"
        )
    return expected_manifest_sha


def _verify_gold_from_review_response(
    review_manifest: Mapping[str, Any],
    review_response: Mapping[str, Any],
    gold: Mapping[str, Any],
    *,
    partition: str,
) -> None:
    try:
        expected_gold = validate_review_response(
            review_manifest,
            review_response,
            partition=partition,
        )
    except ValueError as exc:
        raise BoundaryPromotionShadowError(
            f"boundary promotion review response is invalid: {exc}"
        ) from exc
    for key in (
        "schema_version",
        "selection_lock_sha256",
        "boundary_promotion_selection_sha256",
        "boundary_promotion_partition",
        "partition",
        "review_manifest_sha256",
        "population_count",
        "valid_count",
        "invalid_count",
        "records",
        "invalid_records",
    ):
        if gold.get(key) != expected_gold.get(key):
            raise BoundaryPromotionShadowError(
                f"boundary promotion gold does not match candidate-blind review response: {key}"
            )


def _bootstrap_track_mean_gain(
    rows: Sequence[Mapping[str, Any]], *, samples: int = 2000
) -> list[float] | None:
    by_track: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.get("changed"):
            continue
        by_track[str(row["track"])].append(float(row["gain_ms"]))
    track_means = [statistics.mean(values) for _, values in sorted(by_track.items())]
    if len(track_means) < 2:
        return None
    rng = random.Random(20260912)
    draws = [statistics.mean(rng.choices(track_means, k=len(track_means))) for _ in range(samples)]
    draws.sort()
    low_index = int((len(draws) - 1) * 0.025)
    high_index = int((len(draws) - 1) * 0.975)
    return [draws[low_index], draws[high_index]]


def evaluate_boundary_promotion_shadow(
    pack: Mapping[str, Any],
    selection: Mapping[str, Any],
    gold: Mapping[str, Any],
    *,
    review_manifest: Mapping[str, Any],
    review_response: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate a pre-gold selection; never grant production authority."""
    selection_hash = verify_boundary_promotion_selection(pack, selection)
    review_manifest_hash = _verify_review_manifest_binding(pack, selection_hash, review_manifest)
    if gold.get("review_manifest_sha256") != review_manifest_hash:
        raise BoundaryPromotionShadowError("boundary promotion gold belongs to another review manifest")
    if gold.get("boundary_promotion_selection_sha256") != selection_hash:
        raise BoundaryPromotionShadowError("boundary promotion gold is not bound to the frozen promotion selection")
    frozen_partition = str(review_manifest.get("boundary_promotion_partition") or "")
    partition = str(gold.get("partition") or "")
    if partition != frozen_partition or gold.get("boundary_promotion_partition") != frozen_partition:
        raise BoundaryPromotionShadowError(
            "boundary promotion gold partition differs from frozen review partition"
        )
    _verify_gold_from_review_response(
        review_manifest,
        review_response,
        gold,
        partition=partition,
    )
    review_response_hash = _sha256_payload(review_response)
    gold_payload_hash = _sha256_payload(gold)
    records = merge_timing_decision_gold(pack, gold)
    decisions = {str(row["id"]): row for row in selection["decisions"]}
    selected_records: list[dict[str, Any]] = []
    for raw in records:
        row = dict(raw)
        decision = decisions.get(str(row["id"]))
        if decision is None:
            raise BoundaryPromotionShadowError("gold record is absent from frozen promotion decisions")
        selected_ms = row["old_final_ms"] if decision["choice"] == "keep_smart" else row["hybrid_ms"]
        row["hybrid_ms"] = selected_ms
        row["shadow_choice"] = decision["choice"]
        selected_records.append(row)
    if not selected_records:
        raise BoundaryPromotionShadowError("shadow evaluation has no valid human truth records")

    decision_eval = evaluate_timing_decisions(selected_records)
    overall = decision_eval["scopes"]["overall"]
    scored_ids = {str(row["id"]) for row in selected_records}
    promoted_scored = [
        row for row in decision_eval["records"]
        if row["id"] in scored_ids and decisions[str(row["id"])]["choice"] == "promote_candidate"
    ]
    track_count = len({str(row["track"]) for row in selected_records})
    promoted_track_count = len({str(row["track"]) for row in promoted_scored})
    bootstrap_ci = _bootstrap_track_mean_gain(promoted_scored)
    policy = _validate_gate_policy(selection.get("gate_policy"))
    eligible_partition = partition in {"blind", "holdout"}

    p90_regression = float(overall["final_p90_ms"] or 0.0) - float(overall["baseline_p90_ms"] or 0.0)
    mean_gain = float(overall["changed_mean_gain_ms"] or 0.0)
    track_equal_gain = float(overall["track_equal_mean_gain_ms"] or 0.0)
    harmful_rate = float(overall["harmful_over_100ms_rate"] or 0.0)
    gate_rows = [
        {"gate": "eligible_partition", "passed": eligible_partition, "actual": partition, "required": "blind|holdout"},
        {"gate": "min_valid_count", "passed": len(selected_records) >= policy["min_valid_count"], "actual": len(selected_records), "required": policy["min_valid_count"]},
        {"gate": "min_distinct_tracks", "passed": track_count >= policy["min_distinct_tracks"], "actual": track_count, "required": policy["min_distinct_tracks"]},
        {"gate": "min_promoted_count", "passed": len(promoted_scored) >= policy["min_promoted_count"], "actual": len(promoted_scored), "required": policy["min_promoted_count"]},
        {"gate": "min_promoted_distinct_tracks", "passed": promoted_track_count >= policy["min_promoted_distinct_tracks"], "actual": promoted_track_count, "required": policy["min_promoted_distinct_tracks"]},
        {"gate": "max_new_over_500ms_count", "passed": int(overall["new_over_500ms_error_count"]) <= policy["max_new_over_500ms_count"], "actual": int(overall["new_over_500ms_error_count"]), "required": policy["max_new_over_500ms_count"]},
        {"gate": "max_harmful_over_100ms_rate", "passed": harmful_rate <= policy["max_harmful_over_100ms_rate"] + 1e-12, "actual": harmful_rate, "required": policy["max_harmful_over_100ms_rate"]},
        {"gate": "max_p90_regression_ms", "passed": p90_regression <= policy["max_p90_regression_ms"] + 1e-12, "actual": p90_regression, "required": policy["max_p90_regression_ms"]},
        {"gate": "min_mean_gain_ms", "passed": mean_gain >= policy["min_mean_gain_ms"] - 1e-12, "actual": mean_gain, "required": policy["min_mean_gain_ms"]},
        {"gate": "min_track_equal_mean_gain_ms", "passed": track_equal_gain >= policy["min_track_equal_mean_gain_ms"] - 1e-12, "actual": track_equal_gain, "required": policy["min_track_equal_mean_gain_ms"]},
    ]
    if policy["require_track_bootstrap_lower_bound_nonnegative"]:
        lower = None if bootstrap_ci is None else float(bootstrap_ci[0])
        gate_rows.append(
            {
                "gate": "track_bootstrap_lower_bound_nonnegative",
                "passed": lower is not None and lower >= -1e-12,
                "actual": lower,
                "required": 0.0,
            }
        )
    shadow_passed = all(row["passed"] for row in gate_rows)

    result: dict[str, Any] = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "purpose": "blind_or_holdout_boundary_promotion_shadow_never_production_authority",
        "selection_payload_sha256": selection_hash,
        "review_manifest_sha256": review_manifest_hash,
        "review_response_payload_sha256": review_response_hash,
        "gold_payload_sha256": gold_payload_hash,
        "timing_selection_lock_sha256": selection["timing_selection_lock_sha256"],
        "partition": partition,
        "population_count": int(selection["case_count"]),
        "valid_truth_count": len(selected_records),
        "invalid_truth_count": int(selection["case_count"]) - len(selected_records),
        "distinct_track_count": track_count,
        "promoted_scored_count": len(promoted_scored),
        "promoted_distinct_track_count": promoted_track_count,
        "smart_baseline_mae_ms": overall["baseline_mae_ms"],
        "selected_mae_ms": overall["final_mae_ms"],
        "smart_baseline_p90_ms": overall["baseline_p90_ms"],
        "selected_p90_ms": overall["final_p90_ms"],
        "smart_baseline_max_ms": overall["baseline_max_ms"],
        "selected_max_ms": overall["final_max_ms"],
        "changed_mean_gain_ms": overall["changed_mean_gain_ms"],
        "track_equal_mean_gain_ms": overall["track_equal_mean_gain_ms"],
        "track_bootstrap_95ci_mean_gain_ms": bootstrap_ci,
        "improved_count": overall["improved_count"],
        "neutral_count": overall["neutral_count"],
        "regressed_count": overall["regressed_count"],
        "harmful_over_100ms_count": overall["harmful_over_100ms_count"],
        "harmful_over_100ms_rate": overall["harmful_over_100ms_rate"],
        "new_over_500ms_error_count": overall["new_over_500ms_error_count"],
        "gate_policy": policy,
        "gate_results": gate_rows,
        "shadow_gate_passed": shadow_passed,
        "production_authority_granted": False,
        "production_writeback_permitted": False,
        "next_step": (
            "separate_reviewed_authority_contract_required"
            if shadow_passed
            else "keep_smart_best_safe_floor"
        ),
        "decision_validation": decision_eval,
    }
    result["evaluation_payload_sha256"] = _sha256_payload(result)
    return result
