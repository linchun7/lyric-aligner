#!/usr/bin/env python3
"""Create or apply task-scoped replayable review decisions for a v4 production run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    sha256_file,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.review.decisions import (
    REVIEW_DECISION_SCHEMA_VERSION,
    ReviewDecisionError,
    apply_review_template,
    build_review_template,
)
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _load_run(
    *,
    task_manifest: Path,
    run_path: Path,
    run_artifact_path: Path,
) -> tuple[dict, dict, str]:
    task = load_task_manifest(task_manifest)
    issues = verify_manifest_inputs(task_manifest, task)
    if issues:
        raise ValueError("task manifest validation failed: " + "; ".join(issues))
    fingerprint = str(task["task_fingerprint_sha256"])
    run = _load(run_path)
    artifact = _load(run_artifact_path)
    artifact_issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage="production_orchestration",
    )
    artifact_issues.extend(
        validate_artifact_output(artifact, role="v4_production_run", path=run_path)
    )
    if artifact_issues:
        raise ValueError("invalid production run artifact: " + "; ".join(artifact_issues))
    if run.get("algorithm_version") != __version__:
        raise ValueError("production run algorithm version mismatch; rerun v4_run")
    if run.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError("production run belongs to another task")
    return run, artifact, fingerprint


def command_template(args: argparse.Namespace) -> int:
    run, artifact, _ = _load_run(
        task_manifest=args.task_manifest,
        run_path=args.run,
        run_artifact_path=args.run_artifact,
    )
    template = build_review_template(
        run,
        base_run_artifact_id=str(artifact["artifact_id"]),
    )
    atomic_write_json(args.out, template)
    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "review_items": len(template["review_items"]),
                "template": str(args.out),
                "base_run_artifact_id": artifact["artifact_id"],
            }
        )
    )
    return 0


def command_apply(args: argparse.Namespace) -> int:
    run, artifact, fingerprint = _load_run(
        task_manifest=args.task_manifest,
        run_path=args.run,
        run_artifact_path=args.run_artifact,
    )
    template = _load(args.decisions)
    reviewed = apply_review_template(
        run,
        template,
        base_run_artifact_id=str(artifact["artifact_id"]),
    )
    atomic_write_json(args.out, reviewed)
    resolution = reviewed["review_resolution"]
    review_artifact = build_artifact_manifest(
        task_fingerprint_sha256=fingerprint,
        stage="review_resolution",
        algorithm_version=__version__,
        outputs=(
            ("v4_reviewed_run", args.out),
            ("review_decisions", args.decisions),
        ),
        normalized_config={
            "review_decision_schema_version": REVIEW_DECISION_SCHEMA_VERSION,
            "base_run_artifact_id": str(artifact["artifact_id"]),
            "decision_source_sha256": sha256_file(args.decisions),
            "calibration_profile_version": str(run.get("calibration_profile_version") or ""),
            "calibration_profile_id": str(run.get("calibration_profile_id") or ""),
            "legacy_fallback": False,
        },
        producer={"git_commit": args.git_commit} if args.git_commit else {},
        upstream_artifact_ids=(str(artifact["artifact_id"]),),
        evidence={
            "status": reviewed["status"],
            "decision_count": int(resolution["decision_count"]),
            "resolved_issue_count": int(resolution["resolved_issue_count"]),
            "remaining_issue_count": int(resolution["remaining_issue_count"]),
        },
    )
    atomic_write_json(args.artifact_out, review_artifact)
    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "status": reviewed["status"],
                "resolved_issues": resolution["resolved_issue_count"],
                "remaining_issues": resolution["remaining_issue_count"],
                "reviewed_run": str(args.out),
                "artifact": str(args.artifact_out),
                "artifact_id": review_artifact["artifact_id"],
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    template = subparsers.add_parser("template", help="Create an editable review template")
    template.add_argument("--task-manifest", required=True, type=Path)
    template.add_argument("--run", required=True, type=Path)
    template.add_argument("--run-artifact", required=True, type=Path)
    template.add_argument("--out", required=True, type=Path)
    template.set_defaults(func=command_template)

    apply = subparsers.add_parser("apply", help="Apply decisions and create a reviewed-run artifact")
    apply.add_argument("--task-manifest", required=True, type=Path)
    apply.add_argument("--run", required=True, type=Path)
    apply.add_argument("--run-artifact", required=True, type=Path)
    apply.add_argument("--decisions", required=True, type=Path)
    apply.add_argument("--out", required=True, type=Path)
    apply.add_argument("--artifact-out", required=True, type=Path)
    apply.add_argument("--git-commit", default="")
    apply.set_defaults(func=command_apply)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, KeyError, ValueError, json.JSONDecodeError, ReviewDecisionError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
