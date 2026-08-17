#!/usr/bin/env python3
"""Select a calibration candidate, then gate that locked candidate on blind_test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.gate import (
    EvaluationGateError,
    canonical_sha256,
    evaluate_gates,
    file_sha256,
    load_evaluation,
    load_json_object,
    load_policy,
    select_calibration_candidate,
    validate_blind_selection,
)


SELECTION_SCHEMA_VERSION = "1.0"
BLIND_GATE_SCHEMA_VERSION = "1.0"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _require_isolated(evaluation: dict, *, label: str) -> None:
    validation = evaluation.get("dataset_validation")
    if not isinstance(validation, dict):
        raise EvaluationGateError(f"{label} has no dataset_validation")
    if validation.get("source_group_isolation_enforced") is not True:
        raise EvaluationGateError(
            f"{label} does not enforce opaque source_group split isolation"
        )


def _selection_payload_sha(payload: dict) -> str:
    core = {key: value for key, value in payload.items() if key != "selection_payload_sha256"}
    return canonical_sha256(core)


def _load_selection(path: Path) -> dict:
    payload = load_json_object(path, label="selection artifact")
    if payload.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise EvaluationGateError("selection artifact schema_version mismatch")
    expected = str(payload.get("selection_payload_sha256") or "")
    if not expected or expected != _selection_payload_sha(payload):
        raise EvaluationGateError("selection artifact payload hash mismatch")
    return payload


def command_select(args: argparse.Namespace) -> int:
    baseline = load_evaluation(args.baseline)
    policy = load_policy(args.policy)
    if policy.get("split") != "calibration":
        raise EvaluationGateError("select requires a calibration policy")
    _require_isolated(baseline, label="baseline calibration evaluation")

    candidates = [load_evaluation(path) for path in args.candidate]
    if not candidates:
        raise EvaluationGateError("select requires at least one candidate evaluation")
    for candidate in candidates:
        _require_isolated(candidate, label=f"candidate {candidate['candidate_id']}")

    selected = select_calibration_candidate(
        baseline=baseline,
        candidates=candidates,
        policy=policy,
    )
    selected_id = str(selected["selected_candidate_id"])
    selected_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate["candidate_id"] == selected_id
    )
    selected_path = args.candidate[selected_index]
    selected_eval = candidates[selected_index]

    payload = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "dataset": baseline.get("dataset"),
        "dataset_validation_schema": baseline.get("dataset_validation", {}).get(
            "schema_version"
        ),
        "calibration_dataset_ground_truth_sha256": baseline["dataset_identity"][
            "dataset_ground_truth_sha256"
        ],
        "calibration_case_ids_sha256": baseline["dataset_identity"]["case_ids_sha256"],
        "baseline_candidate_id": baseline["candidate_id"],
        "baseline_calibration_evaluation_sha256": file_sha256(args.baseline),
        "selected_candidate_id": selected_id,
        "selected_calibration_evaluation_sha256": file_sha256(selected_path),
        "selected_calibration_candidate_ground_truth_sha256": selected_eval[
            "dataset_identity"
        ]["dataset_ground_truth_sha256"],
        "policy_sha256": file_sha256(args.policy),
        "policy_id": str(policy.get("policy_id") or args.policy.name),
        "selection": selected,
        "privacy": "aggregate metrics, opaque IDs and SHA-256 identities only",
    }
    payload["selection_payload_sha256"] = _selection_payload_sha(payload)
    _write_json(args.out, payload)
    print(
        json.dumps(
            {
                "selected_candidate_id": selected_id,
                "calibration_dataset_ground_truth_sha256": payload[
                    "calibration_dataset_ground_truth_sha256"
                ],
                "selection_payload_sha256": payload["selection_payload_sha256"],
                "out": str(args.out),
            }
        )
    )
    return 0


def command_blind(args: argparse.Namespace) -> int:
    baseline = load_evaluation(args.baseline)
    candidate = load_evaluation(args.candidate)
    policy = load_policy(args.policy)
    selection = _load_selection(args.selection)
    if policy.get("split") != "blind_test":
        raise EvaluationGateError("blind requires a blind_test policy")
    _require_isolated(baseline, label="baseline blind evaluation")
    _require_isolated(candidate, label="candidate blind evaluation")
    if baseline.get("dataset") != selection.get("dataset"):
        raise EvaluationGateError(
            "blind baseline dataset name differs from calibration selection"
        )
    if candidate.get("dataset") != selection.get("dataset"):
        raise EvaluationGateError(
            "blind candidate dataset name differs from calibration selection"
        )
    if baseline.get("dataset_validation", {}).get("schema_version") != selection.get(
        "dataset_validation_schema"
    ):
        raise EvaluationGateError(
            "blind dataset schema differs from calibration selection"
        )
    if candidate.get("dataset_validation", {}).get("schema_version") != selection.get(
        "dataset_validation_schema"
    ):
        raise EvaluationGateError(
            "blind candidate dataset schema differs from calibration selection"
        )

    candidate_sha = file_sha256(args.candidate)
    validate_blind_selection(
        selection=selection,
        candidate_evaluation=candidate,
        candidate_evaluation_sha256=candidate_sha,
    )
    gate = evaluate_gates(baseline, candidate, policy)
    payload = {
        "schema_version": BLIND_GATE_SCHEMA_VERSION,
        "passed": bool(gate["passed"]),
        "dataset": baseline.get("dataset"),
        "blind_dataset_ground_truth_sha256": baseline["dataset_identity"][
            "dataset_ground_truth_sha256"
        ],
        "blind_case_ids_sha256": baseline["dataset_identity"]["case_ids_sha256"],
        "selected_candidate_id": selection["selected_candidate_id"],
        "selection_payload_sha256": selection["selection_payload_sha256"],
        "selection_file_sha256": file_sha256(args.selection),
        "baseline_blind_evaluation_sha256": file_sha256(args.baseline),
        "candidate_blind_evaluation_sha256": candidate_sha,
        "blind_policy_sha256": file_sha256(args.policy),
        "blind_policy_id": str(policy.get("policy_id") or args.policy.name),
        "gate": gate,
        "privacy": "aggregate metrics, opaque IDs and SHA-256 identities only",
    }
    payload["blind_gate_payload_sha256"] = canonical_sha256(payload)
    _write_json(args.out, payload)
    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "selected_candidate_id": payload["selected_candidate_id"],
                "blind_dataset_ground_truth_sha256": payload[
                    "blind_dataset_ground_truth_sha256"
                ],
                "out": str(args.out),
            }
        )
    )
    return 0 if payload["passed"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select one candidate on calibration split")
    select.add_argument("--baseline", required=True, type=Path)
    select.add_argument("--candidate", required=True, action="append", type=Path)
    select.add_argument("--policy", required=True, type=Path)
    select.add_argument("--out", required=True, type=Path)
    select.set_defaults(func=command_select)

    blind = subparsers.add_parser("blind", help="gate calibration-selected candidate on blind_test")
    blind.add_argument("--baseline", required=True, type=Path)
    blind.add_argument("--candidate", required=True, type=Path)
    blind.add_argument("--selection", required=True, type=Path)
    blind.add_argument("--policy", required=True, type=Path)
    blind.add_argument("--out", required=True, type=Path)
    blind.set_defaults(func=command_blind)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError, EvaluationGateError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
