"""Explicit baseline/candidate gates for calibration and blind-test evaluation.

This is the lower-level/legacy-compatible evaluation gate. New P1 workflows
should use ``strict_workflow.py`` / ``v4_calibration_workflow.py``. The module
remains tested because existing evaluation callers may still consume schema 2.0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


GATE_SCHEMA_VERSION = "1.0"


class EvaluationGateError(ValueError):
    """Raised when evaluation inputs or a gate policy are incompatible."""


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise EvaluationGateError(f"{label} must be a JSON object")
    return payload


def load_evaluation(path: Path) -> dict[str, Any]:
    payload = load_json_object(path, label="evaluation")
    if str(payload.get("schema_version") or "") != "2.0":
        raise EvaluationGateError("evaluation schema_version must be 2.0")
    split = str(payload.get("evaluated_split") or "")
    if split not in {"calibration", "blind_test", "train"}:
        raise EvaluationGateError("evaluation has invalid evaluated_split")
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        raise EvaluationGateError("evaluation is missing candidate_id")
    identity = payload.get("dataset_identity")
    if not isinstance(identity, dict) or not str(
        identity.get("dataset_ground_truth_sha256") or ""
    ):
        raise EvaluationGateError("evaluation is missing dataset ground-truth identity")
    return payload


def load_policy(path: Path) -> dict[str, Any]:
    payload = load_json_object(path, label="gate policy")
    if str(payload.get("schema_version") or "") != GATE_SCHEMA_VERSION:
        raise EvaluationGateError(
            f"gate policy schema_version must be {GATE_SCHEMA_VERSION}"
        )
    split = str(payload.get("split") or "")
    if split not in {"calibration", "blind_test"}:
        raise EvaluationGateError("gate policy split must be calibration or blind_test")
    gates = payload.get("gates")
    if not isinstance(gates, list) or not gates:
        raise EvaluationGateError("gate policy requires at least one gate")
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise EvaluationGateError(f"gate {index} must be an object")
        if str(gate.get("scope") or "") == "" or str(gate.get("metric") or "") == "":
            raise EvaluationGateError(f"gate {index} requires scope and metric")
        if gate.get("direction") not in {"higher", "lower"}:
            raise EvaluationGateError(f"gate {index} direction must be higher/lower")
        if "max_regression_abs" not in gate:
            raise EvaluationGateError(
                f"gate {index} must explicitly declare max_regression_abs"
            )
        if float(gate["max_regression_abs"]) < 0:
            raise EvaluationGateError(f"gate {index} max_regression_abs must be >= 0")
    ranking = payload.get("ranking", [])
    if ranking is not None and not isinstance(ranking, list):
        raise EvaluationGateError("gate policy ranking must be a list")
    for index, item in enumerate(ranking or []):
        if not isinstance(item, dict):
            raise EvaluationGateError(f"ranking {index} must be an object")
        if str(item.get("scope") or "") == "" or str(item.get("metric") or "") == "":
            raise EvaluationGateError(f"ranking {index} requires scope and metric")
        if item.get("direction") not in {"higher", "lower"}:
            raise EvaluationGateError(f"ranking {index} direction must be higher/lower")
    return payload


def _scope_metrics(evaluation: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope == "overall":
        metrics = evaluation.get("overall")
    else:
        metrics = evaluation.get("groups", {}).get(scope)
    if not isinstance(metrics, dict):
        raise EvaluationGateError(f"evaluation is missing required scope {scope}")
    return metrics


def metric_value(evaluation: dict[str, Any], *, scope: str, metric: str) -> float:
    metrics = _scope_metrics(evaluation, scope)
    if metric not in metrics:
        raise EvaluationGateError(f"scope {scope} is missing metric {metric}")
    try:
        return float(metrics[metric])
    except (TypeError, ValueError) as exc:
        raise EvaluationGateError(f"metric {scope}/{metric} is not numeric") from exc


def validate_pair(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    expected_split: str,
) -> None:
    if baseline.get("evaluated_split") != expected_split:
        raise EvaluationGateError("baseline evaluation split mismatch")
    if candidate.get("evaluated_split") != expected_split:
        raise EvaluationGateError("candidate evaluation split mismatch")
    baseline_identity = baseline["dataset_identity"]["dataset_ground_truth_sha256"]
    candidate_identity = candidate["dataset_identity"]["dataset_ground_truth_sha256"]
    if baseline_identity != candidate_identity:
        raise EvaluationGateError(
            "baseline/candidate evaluations use different ground-truth datasets"
        )
    if int(baseline["dataset_identity"].get("case_count", -1)) != int(
        candidate["dataset_identity"].get("case_count", -2)
    ):
        raise EvaluationGateError("baseline/candidate evaluation case counts differ")
    if baseline["dataset_identity"].get("case_ids_sha256") != candidate[
        "dataset_identity"
    ].get("case_ids_sha256"):
        raise EvaluationGateError("baseline/candidate evaluation case IDs differ")


def evaluate_gates(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    expected_split = str(policy["split"])
    validate_pair(baseline, candidate, expected_split=expected_split)
    results: list[dict[str, Any]] = []
    passed = True
    for gate in policy["gates"]:
        scope = str(gate["scope"])
        metric = str(gate["metric"])
        direction = str(gate["direction"])
        baseline_value = metric_value(baseline, scope=scope, metric=metric)
        candidate_value = metric_value(candidate, scope=scope, metric=metric)
        delta = candidate_value - baseline_value
        regression = (
            max(0.0, baseline_value - candidate_value)
            if direction == "higher"
            else max(0.0, candidate_value - baseline_value)
        )
        max_regression = float(gate["max_regression_abs"])
        reasons: list[str] = []
        if regression > max_regression + 1e-12:
            reasons.append(
                f"regression {regression:.6f} exceeds {max_regression:.6f}"
            )
        if "min_candidate" in gate and candidate_value < float(gate["min_candidate"]):
            reasons.append(
                f"candidate {candidate_value:.6f} below min_candidate {float(gate['min_candidate']):.6f}"
            )
        if "max_candidate" in gate and candidate_value > float(gate["max_candidate"]):
            reasons.append(
                f"candidate {candidate_value:.6f} above max_candidate {float(gate['max_candidate']):.6f}"
            )
        if "min_improvement_abs" in gate:
            required = float(gate["min_improvement_abs"])
            improvement = delta if direction == "higher" else -delta
            if improvement < required - 1e-12:
                reasons.append(
                    f"improvement {improvement:.6f} below required {required:.6f}"
                )
        gate_passed = not reasons
        passed = passed and gate_passed
        results.append(
            {
                "scope": scope,
                "metric": metric,
                "direction": direction,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "delta": delta,
                "regression_abs": regression,
                "passed": gate_passed,
                "reasons": reasons,
            }
        )
    return {
        "passed": passed,
        "split": expected_split,
        "candidate_id": candidate["candidate_id"],
        "dataset_ground_truth_sha256": candidate["dataset_identity"][
            "dataset_ground_truth_sha256"
        ],
        "gates": results,
    }


def _ranking_key(
    evaluation: dict[str, Any], ranking: Iterable[dict[str, Any]]
) -> tuple[float, ...]:
    values: list[float] = []
    for item in ranking:
        value = metric_value(
            evaluation,
            scope=str(item["scope"]),
            metric=str(item["metric"]),
        )
        values.append(value if item["direction"] == "higher" else -value)
    return tuple(values)


def select_calibration_candidate(
    *,
    baseline: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if policy.get("split") != "calibration":
        raise EvaluationGateError("calibration selection requires calibration policy")
    ranking = policy.get("ranking") or []
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ids: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in ids:
            raise EvaluationGateError("candidate IDs must be unique/non-empty")
        ids.add(candidate_id)
        gate_result = evaluate_gates(baseline, candidate, policy)
        rows.append((candidate, gate_result))
    passing = [(candidate, result) for candidate, result in rows if result["passed"]]
    if not passing:
        raise EvaluationGateError("no calibration candidate passes all gates")
    passing.sort(
        key=lambda pair: (
            _ranking_key(pair[0], ranking),
            str(pair[0]["candidate_id"]),
        ),
        reverse=True,
    )
    selected, selected_gate = passing[0]
    return {
        "selected_candidate_id": selected["candidate_id"],
        "selected_gate": selected_gate,
        "candidate_gate_results": {
            candidate["candidate_id"]: gate
            for candidate, gate in rows
        },
    }


def validate_blind_selection(
    *,
    selection: dict[str, Any],
    candidate_evaluation: dict[str, Any],
    candidate_evaluation_sha256: str,
) -> None:
    selected_id = str(selection.get("selected_candidate_id") or "")
    if candidate_evaluation.get("candidate_id") != selected_id:
        raise EvaluationGateError(
            "blind-test candidate does not match calibration-selected candidate"
        )
    locked_calibration_sha = str(
        selection.get("selected_calibration_evaluation_sha256") or ""
    )
    if not locked_calibration_sha:
        raise EvaluationGateError(
            "selection artifact is missing calibration evaluation lock"
        )
    # Selection writers use selection_payload_sha256. Accept the older field as
    # a compatibility alias for already-produced experimental artifacts, but
    # require one explicit non-empty self identity.
    selection_identity = str(
        selection.get("selection_payload_sha256")
        or selection.get("selection_artifact_sha256")
        or ""
    )
    if not selection_identity:
        raise EvaluationGateError("selection artifact is missing self-identity")
    # The blind evaluation is intentionally a different file/split. Its SHA is
    # recorded by the final gate, while candidate identity is locked by selection.
    if not str(candidate_evaluation_sha256 or "").strip():
        raise EvaluationGateError("blind candidate evaluation SHA is missing")
