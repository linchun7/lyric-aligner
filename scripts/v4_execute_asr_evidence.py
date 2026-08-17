#!/usr/bin/env python3
"""Execute planner-selected local mix ASR jobs with faster-whisper evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.alignment.asr_executor import (
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
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
    return lookup, timeline_ids


def _filter_jobs(plan: dict, selected_ids: list[str]) -> dict:
    if not selected_ids:
        return plan
    requested = {str(value).strip() for value in selected_ids if str(value).strip()}
    jobs = plan.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("alignment plan jobs must be a list")
    available = {str(job.get("job_id") or "") for job in jobs if isinstance(job, dict)}
    missing = sorted(requested - available)
    if missing:
        raise ValueError("requested job_id not found in plan: " + ", ".join(missing))
    result = deepcopy(plan)
    result["jobs"] = [
        job for job in jobs if str(job.get("job_id") or "") in requested
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--plan-artifact", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--job-id", action="append", default=[])
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
        if plan.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("alignment plan belongs to another task")
        if plan.get("algorithm_version") != __version__:
            raise ValueError("alignment plan algorithm version mismatch")
        if plan.get("backend_execution_performed") is not False:
            raise ValueError("alignment plan already reports backend execution")

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
        run_artifact_id = str(run_artifact["artifact_id"])
        if plan.get("source_run_artifact_id") != run_artifact_id:
            raise ValueError("alignment plan belongs to another source run")
        if run_artifact_id not in {
            str(value) for value in plan_artifact.get("upstream_artifact_ids", [])
        }:
            raise ValueError("alignment plan artifact does not bind source run")

        lookup, timeline_ids = _canonical_lookup(
            run,
            run_artifact,
            fingerprint=fingerprint,
        )
        selected_plan = _filter_jobs(plan, args.job_id)
        canonical_by_job: dict[str, str] = {}
        for job in selected_plan.get("jobs", []):
            if not isinstance(job, dict):
                continue
            line_index = job.get("canonical_line_index")
            if line_index is None:
                continue
            key = (str(job.get("occurrence_id") or ""), int(line_index))
            canonical = lookup.get(key)
            if canonical is None:
                raise ValueError(f"plan job canonical line not found: {key[0]}/{key[1]}")
            expected_sha = str(job.get("canonical_text_sha256") or "")
            if not expected_sha or expected_sha != _sha_text(canonical):
                raise ValueError("plan/canonical text identity mismatch")
            canonical_by_job[str(job["job_id"])] = canonical

        config = FasterWhisperExecutionConfig(
            model_id=args.model_id,
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            temperature=args.temperature,
            include_private_text=args.include_private_text,
        )
        evidence = execute_faster_whisper_jobs(
            audio_path=mix_audio,
            plan=selected_plan,
            canonical_text_by_job_id=canonical_by_job,
            config=config,
        )
        evidence.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_plan_artifact_id": str(plan_artifact["artifact_id"]),
                "source_run_artifact_id": run_artifact_id,
                "mix_audio_sha256": str(audio_record["sha256"]),
                "selected_job_ids": [
                    str(job.get("job_id") or "")
                    for job in selected_plan.get("jobs", [])
                    if "mix_asr" in (job.get("requested_capabilities") or [])
                ],
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
                "model_id": args.model_id,
                "device": args.device,
                "compute_type": args.compute_type,
                "beam_size": args.beam_size,
                "temperature": args.temperature,
                "include_private_text": args.include_private_text,
                "source_plan_artifact_id": str(plan_artifact["artifact_id"]),
                "source_run_artifact_id": run_artifact_id,
                "mix_audio_sha256": str(audio_record["sha256"]),
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(
                sorted(
                    {
                        str(plan_artifact["artifact_id"]),
                        run_artifact_id,
                        *timeline_ids,
                    }
                )
            ),
            evidence={
                "backend": "faster_whisper",
                "job_count": evidence["job_count"],
                "raw_private_text_included": args.include_private_text,
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
        AsrExecutionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "executed",
                "backend": "faster_whisper",
                "jobs": evidence["job_count"],
                "raw_private_text_included": args.include_private_text,
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
