#!/usr/bin/env python3
"""Execute a P5 ASR second-pass plan and emit complete composite ASR evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.alignment.asr_executor import FasterWhisperExecutionConfig
from lyric_aligner.alignment.asr_second_pass import (
    AsrSecondPassExecutionError,
    execute_second_pass_and_compose,
)
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from task_contract import (
    load_task_manifest,
    resolve_manifest_record,
    verify_manifest_inputs,
)


_RUN_ROLES = {
    "production_orchestration": "v4_production_run",
    "review_resolution": "v4_reviewed_run",
    "overlap_recomposition": "v4_recomposed_run",
    "cut_rebuild": "v4_cut_rebuilt_run",
    "combined_recomposition": "v4_combined_run",
}
_TIMELINE_STAGES = {
    "canonical_timeline_projection",
    "overlap_timeline_recomposition",
    "cut_timeline_rebuild",
    "combined_timeline_recomposition",
}


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


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


def _canonical_lookup(
    run: dict,
    run_artifact: dict,
    *,
    fingerprint: str,
) -> tuple[dict[tuple[str, int], str], set[str]]:
    rows = run.get("occurrences")
    if not isinstance(rows, list) or not rows:
        raise ValueError("source run has no occurrences")
    run_upstreams = {
        str(value) for value in run_artifact.get("upstream_artifact_ids", [])
    }
    lookup: dict[tuple[str, int], str] = {}
    timeline_ids: set[str] = set()
    for occurrence in rows:
        if not isinstance(occurrence, dict):
            raise ValueError("run occurrence must be an object")
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        timeline_value = str(occurrence.get("timeline_path") or "").strip()
        artifact_value = str(occurrence.get("timeline_artifact_path") or "").strip()
        if not occurrence_id or not timeline_value or not artifact_value:
            continue
        timeline_path = Path(timeline_value)
        timeline_artifact_path = Path(artifact_value)
        timeline = _load(timeline_path)
        timeline_artifact = _load(timeline_artifact_path)
        stage = str(timeline_artifact.get("stage") or "")
        if stage not in _TIMELINE_STAGES:
            raise ValueError(f"unsupported canonical timeline stage {stage!r}")
        _validate_artifact(
            timeline_artifact,
            fingerprint=fingerprint,
            stage=stage,
            role="canonical_timeline",
            output=timeline_path,
        )
        timeline_id = str(timeline_artifact.get("artifact_id") or "")
        if timeline_id not in run_upstreams:
            raise ValueError("canonical timeline is not upstream of source run")
        timeline_ids.add(timeline_id)
        result = timeline.get("result")
        if not isinstance(result, dict):
            raise ValueError("canonical timeline has no result")
        if str(result.get("occurrence_id") or "") != occurrence_id:
            raise ValueError("run/timeline occurrence identity mismatch")
        lines = result.get("lines")
        if not isinstance(lines, list):
            raise ValueError("canonical timeline lines must be a list")
        for line in lines:
            try:
                index = int(line["canonical_line_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("canonical timeline has invalid line index") from exc
            key = (occurrence_id, index)
            if key in lookup:
                raise ValueError("duplicate canonical line identity")
            lookup[key] = str(line.get("text") or "")
    if not lookup:
        raise ValueError("no canonical lines available for second-pass execution")
    return lookup, timeline_ids


def _artifact_id(artifact: dict, *, label: str) -> str:
    value = str(artifact.get("artifact_id") or "").strip()
    if not value:
        raise ValueError(f"{label} artifact_id is missing")
    return value


def _requires_upstream(artifact: dict, required_id: str, *, label: str) -> None:
    upstreams = {str(value) for value in artifact.get("upstream_artifact_ids", [])}
    if required_id not in upstreams:
        raise ValueError(f"{label} artifact does not bind required upstream")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--plan-artifact", required=True, type=Path)
    parser.add_argument("--first-pass-evidence", required=True, type=Path)
    parser.add_argument("--first-pass-artifact", required=True, type=Path)
    parser.add_argument("--second-pass-plan", required=True, type=Path)
    parser.add_argument("--second-pass-plan-artifact", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--include-private-text", action="store_true")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        input_issues = verify_manifest_inputs(args.task_manifest, task)
        if input_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(input_issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        audio_record = task["inputs"]["audio"]
        mix_audio = resolve_manifest_record(args.task_manifest, audio_record)

        plan = _load(args.plan)
        plan_artifact = _load(args.plan_artifact)
        _validate_artifact(
            plan_artifact,
            fingerprint=fingerprint,
            stage="alignment_job_planning",
            role="alignment_plan",
            output=args.plan,
        )
        plan_artifact_id = _artifact_id(plan_artifact, label="alignment plan")
        if plan.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("alignment plan belongs to another task")
        if plan.get("algorithm_version") != __version__:
            raise ValueError("alignment plan algorithm version mismatch")

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = str(run_artifact.get("stage") or "")
        run_role = _RUN_ROLES.get(run_stage)
        if run_role is None:
            raise ValueError("unsupported source run stage")
        _validate_artifact(
            run_artifact,
            fingerprint=fingerprint,
            stage=run_stage,
            role=run_role,
            output=args.run,
        )
        run_artifact_id = _artifact_id(run_artifact, label="source run")
        if plan.get("source_run_artifact_id") != run_artifact_id:
            raise ValueError("alignment plan belongs to another source run")
        _requires_upstream(plan_artifact, run_artifact_id, label="alignment plan")

        first_pass = _load(args.first_pass_evidence)
        first_artifact = _load(args.first_pass_artifact)
        _validate_artifact(
            first_artifact,
            fingerprint=fingerprint,
            stage="asr_evidence_local",
            role="asr_evidence",
            output=args.first_pass_evidence,
        )
        first_artifact_id = _artifact_id(first_artifact, label="first-pass ASR")
        if first_pass.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("first-pass ASR evidence belongs to another task")
        if first_pass.get("algorithm_version") != __version__:
            raise ValueError("first-pass ASR algorithm version mismatch")
        if first_pass.get("source_plan_artifact_id") != plan_artifact_id:
            raise ValueError("first-pass ASR evidence belongs to another alignment plan")
        if first_pass.get("source_run_artifact_id") != run_artifact_id:
            raise ValueError("first-pass ASR evidence belongs to another source run")
        _requires_upstream(first_artifact, plan_artifact_id, label="first-pass ASR")
        _requires_upstream(first_artifact, run_artifact_id, label="first-pass ASR")

        second_plan = _load(args.second_pass_plan)
        second_artifact = _load(args.second_pass_plan_artifact)
        _validate_artifact(
            second_artifact,
            fingerprint=fingerprint,
            stage="asr_second_pass_planning",
            role="asr_second_pass_plan",
            output=args.second_pass_plan,
        )
        second_artifact_id = _artifact_id(second_artifact, label="second-pass plan")
        if second_plan.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("second-pass plan belongs to another task")
        if second_plan.get("algorithm_version") != __version__:
            raise ValueError("second-pass plan algorithm version mismatch")
        if second_plan.get("source_plan_artifact_id") != plan_artifact_id:
            raise ValueError("second-pass plan belongs to another alignment plan")
        if second_plan.get("source_first_pass_artifact_id") != first_artifact_id:
            raise ValueError("second-pass plan belongs to another first-pass artifact")
        if second_plan.get("source_run_artifact_id") != run_artifact_id:
            raise ValueError("second-pass plan belongs to another source run")
        _requires_upstream(second_artifact, plan_artifact_id, label="second-pass plan")
        _requires_upstream(second_artifact, first_artifact_id, label="second-pass plan")

        lookup, timeline_ids = _canonical_lookup(
            run, run_artifact, fingerprint=fingerprint
        )
        plan_jobs = plan.get("jobs")
        if not isinstance(plan_jobs, list):
            raise ValueError("alignment plan jobs must be a list")
        canonical_by_job: dict[str, str] = {}
        for job in plan_jobs:
            if not isinstance(job, dict) or "mix_asr" not in (
                job.get("requested_capabilities") or []
            ):
                continue
            job_id = str(job.get("job_id") or "").strip()
            line_index = job.get("canonical_line_index")
            if not job_id or line_index is None:
                continue
            key = (str(job.get("occurrence_id") or ""), int(line_index))
            canonical = lookup.get(key)
            if canonical is None:
                raise ValueError(f"plan job canonical line not found: {key[0]}/{key[1]}")
            expected_sha = str(job.get("canonical_text_sha256") or "")
            if not expected_sha or expected_sha != _sha_text(canonical):
                raise ValueError("plan/canonical text identity mismatch")
            canonical_by_job[job_id] = canonical

        config = FasterWhisperExecutionConfig(
            model_id=args.model_id,
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            temperature=args.temperature,
            include_private_text=args.include_private_text,
        )
        evidence = execute_second_pass_and_compose(
            audio_path=mix_audio,
            alignment_plan=plan,
            second_pass_plan=second_plan,
            first_pass_evidence=first_pass,
            canonical_text_by_job_id=canonical_by_job,
            config=config,
        )
        evidence.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_plan_artifact_id": plan_artifact_id,
                "source_first_pass_artifact_id": first_artifact_id,
                "source_second_pass_plan_artifact_id": second_artifact_id,
                "source_run_artifact_id": run_artifact_id,
                "mix_audio_sha256": str(audio_record["sha256"]),
                "selected_job_ids": list(second_plan.get("selected_job_ids") or []),
            }
        )
        atomic_write_json(args.out, evidence)

        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="asr_evidence_local",
            algorithm_version=__version__,
            outputs=(("asr_evidence", args.out),),
            normalized_config={
                "backend": "faster_whisper",
                "mode": "composite_second_pass_evidence",
                "first_pass_model_id": evidence["config"]["first_pass_model_id"],
                "second_pass_model_id": evidence["config"]["second_pass_model_id"],
                "device": args.device,
                "compute_type": args.compute_type,
                "beam_size": args.beam_size,
                "temperature": args.temperature,
                "include_private_text": args.include_private_text,
                "scope_policy": evidence["scope_policy"],
                "source_plan_artifact_id": plan_artifact_id,
                "source_first_pass_artifact_id": first_artifact_id,
                "source_second_pass_plan_artifact_id": second_artifact_id,
                "source_run_artifact_id": run_artifact_id,
                "mix_audio_sha256": str(audio_record["sha256"]),
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(
                sorted(
                    {
                        plan_artifact_id,
                        first_artifact_id,
                        second_artifact_id,
                        run_artifact_id,
                        *timeline_ids,
                    }
                )
            ),
            evidence={
                "backend": "faster_whisper",
                "mode": "composite_second_pass_evidence",
                "raw_private_text_included": args.include_private_text,
                "first_pass_retained_job_count": evidence[
                    "first_pass_retained_job_count"
                ],
                "second_pass_selected_job_count": evidence[
                    "second_pass_selected_job_count"
                ],
                "second_pass_executed_job_count": evidence[
                    "second_pass_executed_job_count"
                ],
                "model_loaded_second_pass": evidence["model_loaded_second_pass"],
                "canonical_text_authority_unchanged": True,
                "primary_timing_authority_unchanged": True,
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        AsrSecondPassExecutionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "composite_second_pass_evidence",
                "jobs": evidence["job_count"],
                "first_pass_retained": evidence["first_pass_retained_job_count"],
                "second_pass_selected": evidence["second_pass_selected_job_count"],
                "second_pass_executed": evidence["second_pass_executed_job_count"],
                "model_loaded_second_pass": evidence["model_loaded_second_pass"],
                "raw_private_text_included": args.include_private_text,
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
