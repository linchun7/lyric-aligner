#!/usr/bin/env python3
"""Execute source-side forced-alignment jobs through an explicit external protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.alignment.forced_executor import (
    FORCED_ALIGNMENT_PROTOCOL_VERSION,
    ExternalForcedAlignmentConfig,
    ForcedAlignmentExecutionError,
    command_argv,
    execute_external_forced_alignment_jobs,
)
from lyric_aligner.assets.bindings import AssetBindingError, bindings_from_payload
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from task_contract import load_task_manifest, verify_manifest_inputs


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


def _artifact_id(artifact: dict, *, label: str) -> str:
    value = str(artifact.get("artifact_id") or "").strip()
    if not value:
        raise ValueError(f"{label} artifact_id is missing")
    return value


def _check_artifact(
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


def _requires_upstream(artifact: dict, required_id: str, *, label: str) -> None:
    upstreams = {str(value) for value in artifact.get("upstream_artifact_ids", [])}
    if required_id not in upstreams:
        raise ValueError(f"{label} artifact does not bind required upstream")


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
        occurrence_id = str(occurrence.get("occurrence_id") or "").strip()
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
        _check_artifact(
            timeline_artifact,
            fingerprint=fingerprint,
            stage=stage,
            role="canonical_timeline",
            output=timeline_path,
        )
        timeline_id = _artifact_id(timeline_artifact, label="canonical timeline")
        if timeline_id not in run_upstreams:
            raise ValueError("canonical timeline artifact is not upstream of source run")
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
        raise ValueError("no canonical lines available for forced alignment")
    return lookup, timeline_ids


def _canonical_by_job(
    plan: dict,
    lookup: dict[tuple[str, int], str],
) -> dict[str, str]:
    rows = plan.get("jobs")
    if not isinstance(rows, list):
        raise ValueError("alignment plan jobs must be a list")
    result: dict[str, str] = {}
    for job in rows:
        if not isinstance(job, dict):
            raise ValueError("alignment plan job must be an object")
        if "source_forced_alignment" not in (job.get("requested_capabilities") or []):
            continue
        job_id = str(job.get("job_id") or "").strip()
        line_index = job.get("canonical_line_index")
        if not job_id or line_index is None:
            continue
        key = (str(job.get("occurrence_id") or ""), int(line_index))
        canonical = lookup.get(key)
        if canonical is None:
            raise ValueError(
                f"forced-alignment job canonical line not found: {key[0]}/{key[1]}"
            )
        expected_sha = str(job.get("canonical_text_sha256") or "")
        if not expected_sha or expected_sha != _sha_text(canonical):
            raise ValueError("forced-alignment plan/canonical text identity mismatch")
        result[job_id] = canonical
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--plan-artifact", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--track-assets-artifact", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--external-command", required=True)
    parser.add_argument("--backend-id", required=True)
    parser.add_argument("--backend-version", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--job-id", action="append", default=None)
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

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = str(run_artifact.get("stage") or "")
        run_role = _RUN_ROLES.get(run_stage)
        if run_role is None:
            raise ValueError("unsupported source run stage")
        _check_artifact(
            run_artifact,
            fingerprint=fingerprint,
            stage=run_stage,
            role=run_role,
            output=args.run,
        )
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("source run belongs to another task")
        if run.get("algorithm_version") != __version__:
            raise ValueError("source run algorithm version mismatch")
        run_artifact_id = _artifact_id(run_artifact, label="source run")

        plan = _load(args.plan)
        plan_artifact = _load(args.plan_artifact)
        _check_artifact(
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
        if plan.get("source_run_artifact_id") != run_artifact_id:
            raise ValueError("alignment plan belongs to another source run")
        _requires_upstream(plan_artifact, run_artifact_id, label="alignment plan")

        assets = _load(args.track_assets)
        assets_artifact = _load(args.track_assets_artifact)
        _check_artifact(
            assets_artifact,
            fingerprint=fingerprint,
            stage="asset_resolution",
            role="track_assets",
            output=args.track_assets,
        )
        assets_artifact_id = _artifact_id(assets_artifact, label="track assets")
        _requires_upstream(run_artifact, assets_artifact_id, label="source run")
        bindings = bindings_from_payload(assets, verify_files=True)

        canonical_lookup, timeline_ids = _canonical_lookup(
            run, run_artifact, fingerprint=fingerprint
        )
        canonical_by_job = _canonical_by_job(plan, canonical_lookup)

        config = ExternalForcedAlignmentConfig(
            command=args.external_command,
            backend_id=args.backend_id,
            backend_version=args.backend_version,
            model_id=args.model_id,
            model_revision=args.model_revision,
            timeout_seconds=args.timeout_seconds,
        )
        evidence = execute_external_forced_alignment_jobs(
            plan=plan,
            bindings=bindings,
            canonical_text_by_job_id=canonical_by_job,
            config=config,
            selected_job_ids=args.job_id,
        )
        evidence.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_run_artifact_id": run_artifact_id,
                "source_plan_artifact_id": plan_artifact_id,
                "source_track_assets_artifact_id": assets_artifact_id,
                "selected_job_ids": args.job_id,
            }
        )
        atomic_write_json(args.out, evidence)

        argv = command_argv(args.external_command)
        executable_label = Path(argv[0]).name if argv else ""
        command_sha256 = hashlib.sha256(
            args.external_command.encode("utf-8")
        ).hexdigest()
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="source_forced_alignment_evidence",
            algorithm_version=__version__,
            outputs=(("forced_alignment_evidence", args.out),),
            normalized_config={
                "protocol_version": FORCED_ALIGNMENT_PROTOCOL_VERSION,
                "backend_id": args.backend_id,
                "backend_version": args.backend_version,
                "model_id": args.model_id,
                "model_revision": args.model_revision,
                "timeout_seconds": args.timeout_seconds,
                "command_sha256": command_sha256,
                "command_executable_basename": executable_label,
                "selected_job_ids": args.job_id,
                "source_run_artifact_id": run_artifact_id,
                "source_plan_artifact_id": plan_artifact_id,
                "source_track_assets_artifact_id": assets_artifact_id,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(
                sorted(
                    {
                        run_artifact_id,
                        plan_artifact_id,
                        assets_artifact_id,
                        *timeline_ids,
                    }
                )
            ),
            evidence={
                "backend": "external_forced_aligner",
                "backend_id": args.backend_id,
                "model_id": args.model_id,
                "model_revision": args.model_revision,
                "command_invoked": evidence["command_invoked"],
                "job_count": evidence["job_count"],
                "canonical_text_authority_unchanged": True,
                "timing_authority": "auxiliary_source_forced_alignment_evidence",
                "raw_canonical_text_in_artifact": False,
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        AssetBindingError,
        ForcedAlignmentExecutionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "source_forced_alignment_evidence",
                "backend_id": evidence["backend_id"],
                "model_id": evidence["model_id"],
                "model_revision": evidence["model_revision"],
                "command_invoked": evidence["command_invoked"],
                "jobs": evidence["job_count"],
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
