#!/usr/bin/env python3
"""Canonical split-isolated calibration selection and locked blind-test workflow."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SCRIPT_ROOT = REPOSITORY_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from evaluate_dataset import aggregate, case_metrics
from lyric_aligner.evaluation.strict_workflow import (
    STRICT_EVALUATION_SCHEMA,
    STRICT_SELECTION_SCHEMA,
    StrictEvaluationError,
    cut_boundary_metrics,
    evaluate_gates,
    file_sha256,
    ground_truth_identity,
    load_json,
    load_policy,
    load_selection,
    load_strict_evaluation,
    runtime_identity,
    selected_cases,
    select_candidate,
    selection_hash,
    validate_blind_baseline_lock,
    validate_blind_lock,
    validate_manifest_metadata,
    validate_selected_files,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def require_evaluation_isolation(evaluation: dict, *, label: str) -> None:
    validation = evaluation.get("dataset_validation")
    if not isinstance(validation, dict) or validation.get(
        "source_group_isolation_enforced"
    ) is not True:
        raise StrictEvaluationError(
            f"{label} does not prove source_group split isolation"
        )


def command_evaluate(args: argparse.Namespace) -> int:
    candidate_id = str(args.candidate_id or "").strip()
    candidate_revision = str(args.candidate_revision or "").strip()
    if not candidate_id or not candidate_revision:
        raise StrictEvaluationError(
            "candidate-id and candidate-revision must be non-empty"
        )

    manifest = load_json(args.dataset, label="dataset manifest")
    validation = validate_manifest_metadata(manifest)
    cases = selected_cases(manifest, args.split)
    # Important: only the selected split's reference/prediction/QA files are
    # resolved or read. Other split prediction/QA files may not exist yet.
    validate_selected_files(args.dataset, cases)
    runtime = runtime_identity(args.dataset, cases)
    identity = ground_truth_identity(args.dataset, manifest, args.split)

    rows = [case_metrics(case, args.dataset.parent) for case in cases]
    grouped_rows: dict[str, list[dict]] = defaultdict(list)
    grouped_cases: dict[str, list[dict]] = defaultdict(list)
    for row, case in zip(rows, cases):
        scopes = [f"language:{row['language']}"]
        scopes.extend(
            f"structural:{scenario}" for scenario in row["structural_scenarios"]
        )
        for scope in scopes:
            grouped_rows[scope].append(row)
            grouped_cases[scope].append(case)

    overall = aggregate(rows)
    overall.update(cut_boundary_metrics(cases))
    groups = {}
    for scope in sorted(grouped_rows):
        metrics = aggregate(grouped_rows[scope])
        metrics.update(cut_boundary_metrics(grouped_cases[scope]))
        groups[scope] = metrics

    payload = {
        "schema_version": STRICT_EVALUATION_SCHEMA,
        "dataset": validation["dataset"],
        "dataset_revision": validation["dataset_revision"],
        "evaluated_split": args.split,
        "candidate_id": candidate_id,
        "candidate_revision": candidate_revision,
        "runtime_identity": runtime,
        "dataset_identity": identity,
        "dataset_validation": validation,
        "overall": overall,
        "groups": groups,
        "cases": [
            {
                "id": row["id"],
                "split": row["split"],
                "language": row["language"],
                "structural_scenarios": row["structural_scenarios"],
                "reference_cues": row["reference_cues"],
                "predicted_cues": row["predicted_cues"],
                "review_candidate_count": row["review_candidate_count"],
                "publish_ready": row["publish_ready"],
            }
            for row in rows
        ],
        "privacy": "aggregate metrics, opaque IDs and SHA-256 identities only; lyric text omitted",
    }
    write_json(args.out, payload)
    print(
        json.dumps(
            {
                "split": args.split,
                "candidate_id": candidate_id,
                "candidate_revision": candidate_revision,
                "dataset_revision": validation["dataset_revision"],
                "dataset_ground_truth_sha256": identity[
                    "dataset_ground_truth_sha256"
                ],
                "case_count": identity["case_count"],
                "out": str(args.out),
            }
        )
    )
    return 0


def command_select(args: argparse.Namespace) -> int:
    baseline = load_strict_evaluation(args.baseline)
    candidates = [load_strict_evaluation(path) for path in args.candidate]
    policy = load_policy(args.policy, "calibration")
    if baseline.get("evaluated_split") != "calibration":
        raise StrictEvaluationError("baseline is not a calibration evaluation")
    require_evaluation_isolation(baseline, label="baseline calibration")
    for candidate in candidates:
        require_evaluation_isolation(
            candidate, label=f"candidate {candidate['candidate_id']}"
        )

    selected = select_candidate(baseline, candidates, policy)
    selected_eval = next(
        candidate
        for candidate in candidates
        if candidate["candidate_id"] == selected["selected_candidate_id"]
    )
    selected_path = next(
        path
        for path, candidate in zip(args.candidate, candidates)
        if candidate["candidate_id"] == selected["selected_candidate_id"]
    )
    payload = {
        "schema_version": STRICT_SELECTION_SCHEMA,
        "dataset": baseline.get("dataset"),
        "dataset_revision": baseline["dataset_revision"],
        "calibration_dataset_ground_truth_sha256": baseline["dataset_identity"][
            "dataset_ground_truth_sha256"
        ],
        "calibration_case_ids_sha256": baseline["dataset_identity"][
            "case_ids_sha256"
        ],
        "baseline_candidate_id": baseline["candidate_id"],
        "baseline_candidate_revision": baseline["candidate_revision"],
        "baseline_runtime_identity": baseline["runtime_identity"],
        "baseline_calibration_evaluation_sha256": file_sha256(args.baseline),
        "selected_candidate_id": selected["selected_candidate_id"],
        "selected_candidate_revision": selected["selected_candidate_revision"],
        "selected_runtime_identity": selected["selected_runtime_identity"],
        "selected_calibration_evaluation_sha256": file_sha256(selected_path),
        "selected_calibration_ground_truth_sha256": selected_eval[
            "dataset_identity"
        ]["dataset_ground_truth_sha256"],
        "policy_id": str(policy.get("policy_id") or args.policy.name),
        "policy_sha256": file_sha256(args.policy),
        "selection": selected,
        "privacy": "aggregate metrics, opaque IDs and SHA-256 identities only",
    }
    payload["selection_payload_sha256"] = selection_hash(payload)
    write_json(args.out, payload)
    print(
        json.dumps(
            {
                "selected_candidate_id": payload["selected_candidate_id"],
                "selected_candidate_revision": payload[
                    "selected_candidate_revision"
                ],
                "selection_payload_sha256": payload[
                    "selection_payload_sha256"
                ],
                "out": str(args.out),
            }
        )
    )
    return 0


def command_blind(args: argparse.Namespace) -> int:
    baseline = load_strict_evaluation(args.baseline)
    candidate = load_strict_evaluation(args.candidate)
    selection = load_selection(args.selection)
    policy = load_policy(args.policy, "blind_test")
    if baseline.get("evaluated_split") != "blind_test":
        raise StrictEvaluationError("baseline is not a blind_test evaluation")
    if candidate.get("evaluated_split") != "blind_test":
        raise StrictEvaluationError("candidate is not a blind_test evaluation")
    require_evaluation_isolation(baseline, label="baseline blind_test")
    require_evaluation_isolation(candidate, label="candidate blind_test")

    # Both sides are frozen at calibration selection. Otherwise the blind gate
    # could compare the selected candidate to a different/newer baseline.
    validate_blind_baseline_lock(selection, baseline)
    validate_blind_lock(selection, candidate)

    gate = evaluate_gates(baseline, candidate, policy)
    payload = {
        "schema_version": "1.0",
        "passed": bool(gate["passed"]),
        "dataset": baseline.get("dataset"),
        "dataset_revision": baseline["dataset_revision"],
        "blind_dataset_ground_truth_sha256": baseline["dataset_identity"][
            "dataset_ground_truth_sha256"
        ],
        "blind_case_ids_sha256": baseline["dataset_identity"][
            "case_ids_sha256"
        ],
        "baseline_candidate_id": selection["baseline_candidate_id"],
        "baseline_candidate_revision": selection["baseline_candidate_revision"],
        "baseline_runtime_identity": selection["baseline_runtime_identity"],
        "selected_candidate_id": selection["selected_candidate_id"],
        "selected_candidate_revision": selection["selected_candidate_revision"],
        "selected_runtime_identity": selection["selected_runtime_identity"],
        "selection_payload_sha256": selection["selection_payload_sha256"],
        "selection_file_sha256": file_sha256(args.selection),
        "baseline_blind_evaluation_sha256": file_sha256(args.baseline),
        "candidate_blind_evaluation_sha256": file_sha256(args.candidate),
        "blind_policy_id": str(policy.get("policy_id") or args.policy.name),
        "blind_policy_sha256": file_sha256(args.policy),
        "gate": gate,
        "privacy": "aggregate metrics, opaque IDs and SHA-256 identities only",
    }
    payload["blind_gate_payload_sha256"] = selection_hash(payload)
    write_json(args.out, payload)
    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "selected_candidate_id": payload["selected_candidate_id"],
                "selected_candidate_revision": payload[
                    "selected_candidate_revision"
                ],
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

    evaluate = subparsers.add_parser(
        "evaluate", help="evaluate exactly one split without reading other split predictions"
    )
    evaluate.add_argument("--dataset", required=True, type=Path)
    evaluate.add_argument(
        "--split", required=True, choices=("train", "calibration", "blind_test")
    )
    evaluate.add_argument("--candidate-id", required=True)
    evaluate.add_argument("--candidate-revision", required=True)
    evaluate.add_argument("--out", required=True, type=Path)
    evaluate.set_defaults(func=command_evaluate)

    select = subparsers.add_parser(
        "select", help="select and lock one candidate using calibration only"
    )
    select.add_argument("--baseline", required=True, type=Path)
    select.add_argument("--candidate", required=True, action="append", type=Path)
    select.add_argument("--policy", required=True, type=Path)
    select.add_argument("--out", required=True, type=Path)
    select.set_defaults(func=command_select)

    blind = subparsers.add_parser(
        "blind", help="gate only the calibration-locked candidate on blind_test"
    )
    blind.add_argument("--baseline", required=True, type=Path)
    blind.add_argument("--candidate", required=True, type=Path)
    blind.add_argument("--selection", required=True, type=Path)
    blind.add_argument("--policy", required=True, type=Path)
    blind.add_argument("--out", required=True, type=Path)
    blind.set_defaults(func=command_blind)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError, StrictEvaluationError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
