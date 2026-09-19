#!/usr/bin/env python3
"""Plan a bounded second ASR pass from weak/missing first-pass local evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.alignment.asr_routing import (
    ASR_SECOND_PASS_POLICY_ID,
    AsrRoutingError,
    AsrSecondPassRoutingConfig,
    build_second_pass_plan,
)
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_artifact(
    artifact: dict,
    *,
    fingerprint: str,
    stage: str,
    role: str,
    output: Path,
) -> None:
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=output))
    if issues:
        raise ValueError(f"invalid {stage} artifact: " + "; ".join(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--plan-artifact", required=True, type=Path)
    parser.add_argument("--first-pass-evidence", required=True, type=Path)
    parser.add_argument("--first-pass-artifact", required=True, type=Path)
    parser.add_argument("--second-pass-model-id", required=True)
    parser.add_argument("--min-canonical-text-support", type=float, default=0.65)
    parser.add_argument("--min-avg-logprob", type=float, default=-0.75)
    parser.add_argument("--max-no-speech-prob", type=float, default=0.60)
    parser.add_argument("--min-language-probability", type=float, default=0.65)
    parser.add_argument("--no-reroute-missing-segments", action="store_true")
    parser.add_argument("--no-reroute-missing-line-support", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        second_pass_model_id = str(args.second_pass_model_id or "").strip()
        if not second_pass_model_id:
            raise ValueError("second-pass-model-id must be non-empty")

        task = load_task_manifest(args.task_manifest)
        input_issues = verify_manifest_inputs(args.task_manifest, task)
        if input_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(input_issues))
        fingerprint = str(task["task_fingerprint_sha256"])

        plan = _load(args.plan)
        plan_artifact = _load(args.plan_artifact)
        _validate_artifact(
            plan_artifact,
            fingerprint=fingerprint,
            stage="alignment_job_planning",
            role="alignment_plan",
            output=args.plan,
        )
        if plan.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("alignment plan belongs to another task")
        if plan.get("algorithm_version") != __version__:
            raise ValueError("alignment plan algorithm version mismatch")

        first_pass = _load(args.first_pass_evidence)
        first_artifact = _load(args.first_pass_artifact)
        _validate_artifact(
            first_artifact,
            fingerprint=fingerprint,
            stage="asr_evidence_local",
            role="asr_evidence",
            output=args.first_pass_evidence,
        )
        if first_pass.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("first-pass ASR evidence belongs to another task")
        if first_pass.get("algorithm_version") != __version__:
            raise ValueError("first-pass ASR evidence algorithm version mismatch")

        plan_artifact_id = str(plan_artifact.get("artifact_id") or "")
        if first_pass.get("source_plan_artifact_id") != plan_artifact_id:
            raise ValueError("first-pass ASR evidence belongs to another alignment plan")
        if plan_artifact_id not in {
            str(value) for value in first_artifact.get("upstream_artifact_ids", [])
        }:
            raise ValueError("first-pass ASR artifact does not bind alignment plan")
        if first_pass.get("source_run_artifact_id") != plan.get("source_run_artifact_id"):
            raise ValueError("first-pass ASR evidence belongs to another source run")

        first_pass_model_id = str(
            (first_pass.get("config") or {}).get("model_id") or ""
        ).strip()
        if not first_pass_model_id:
            raise ValueError("first-pass ASR evidence must record config.model_id")
        if first_pass_model_id == second_pass_model_id:
            raise ValueError(
                "second-pass-model-id must differ from first-pass model_id; "
                "otherwise this is not an accuracy-model escalation"
            )

        config = AsrSecondPassRoutingConfig(
            min_canonical_text_support=args.min_canonical_text_support,
            min_avg_logprob=args.min_avg_logprob,
            max_no_speech_prob=args.max_no_speech_prob,
            min_language_probability=args.min_language_probability,
            reroute_missing_segments=not args.no_reroute_missing_segments,
            reroute_missing_line_support=not args.no_reroute_missing_line_support,
            max_jobs=args.max_jobs,
        )
        second_pass = build_second_pass_plan(
            alignment_plan=plan,
            first_pass_evidence=first_pass,
            config=config,
        )
        second_pass.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_run_artifact_id": plan.get("source_run_artifact_id"),
                "source_plan_artifact_id": plan_artifact_id,
                "source_first_pass_artifact_id": str(first_artifact["artifact_id"]),
                "first_pass_model_id": first_pass_model_id,
                "second_pass_model_id": second_pass_model_id,
                "selected_job_ids": [row["job_id"] for row in second_pass["jobs"]],
            }
        )
        atomic_write_json(args.out, second_pass)

        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="asr_second_pass_planning",
            algorithm_version=__version__,
            outputs=(("asr_second_pass_plan", args.out),),
            normalized_config={
                "policy_id": ASR_SECOND_PASS_POLICY_ID,
                "policy_calibrated": False,
                **config.to_dict(),
                "scope_policy": "reuse_exact_first_pass_local_windows",
                "source_plan_artifact_id": plan_artifact_id,
                "source_first_pass_artifact_id": str(first_artifact["artifact_id"]),
                "first_pass_model_id": first_pass_model_id,
                "second_pass_model_id": second_pass_model_id,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(
                plan_artifact_id,
                str(first_artifact["artifact_id"]),
            ),
            evidence={
                "mode": "second_pass_plan_only",
                "policy_calibrated": False,
                "backend_execution_performed": False,
                "scope_policy": "reuse_exact_first_pass_local_windows",
                "second_pass_job_count": second_pass["summary"]["second_pass_job_count"],
                "second_pass_plan_truncated": second_pass["summary"][
                    "second_pass_plan_truncated"
                ],
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        AsrRoutingError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "second_pass_plan_only",
                "policy_calibrated": False,
                "scope_policy": "reuse_exact_first_pass_local_windows",
                "jobs": second_pass["summary"]["second_pass_job_count"],
                "truncated": second_pass["summary"]["second_pass_plan_truncated"],
                "first_pass_model_id": first_pass_model_id,
                "second_pass_model_id": second_pass_model_id,
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
