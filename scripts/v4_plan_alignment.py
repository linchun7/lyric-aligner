#!/usr/bin/env python3
"""Plan bounded local ASR/forced-alignment evidence jobs; never execute models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.alignment.backends import inspect_backends
from lyric_aligner.alignment.planner import (
    ALIGNMENT_PLANNER_POLICY_ID,
    AlignmentPlannerConfig,
    AlignmentPlanningError,
    build_alignment_plan,
)
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


def _load_timelines(
    run: dict,
    run_artifact: dict,
    *,
    fingerprint: str,
) -> tuple[list[dict], list[str]]:
    rows = run.get("occurrences")
    if not isinstance(rows, list) or not rows:
        raise ValueError("source run has no occurrences")
    run_upstreams = {
        str(value) for value in run_artifact.get("upstream_artifact_ids", [])
    }
    timelines: list[dict] = []
    artifact_ids: list[str] = []
    seen: set[str] = set()
    for occurrence in rows:
        if not isinstance(occurrence, dict):
            raise ValueError("run occurrence must be an object")
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        if not occurrence_id or occurrence_id in seen:
            raise ValueError("run occurrence IDs must be unique/non-empty")
        seen.add(occurrence_id)
        timeline_value = str(occurrence.get("timeline_path") or "").strip()
        artifact_value = str(occurrence.get("timeline_artifact_path") or "").strip()
        if not timeline_value or not artifact_value:
            continue
        timeline_path = Path(timeline_value)
        artifact_path = Path(artifact_value)
        timeline = _load(timeline_path)
        timeline_artifact = _load(artifact_path)
        stage = str(timeline_artifact.get("stage") or "")
        if stage not in _TIMELINE_STAGES:
            raise ValueError(f"unsupported timeline stage {stage!r}")
        _check_artifact(
            timeline_artifact,
            fingerprint=fingerprint,
            stage=stage,
            role="canonical_timeline",
            output=timeline_path,
        )
        artifact_id = str(timeline_artifact.get("artifact_id") or "")
        if artifact_id not in run_upstreams:
            raise ValueError("timeline artifact is not upstream of source run")
        result = timeline.get("result")
        if not isinstance(result, dict):
            raise ValueError("canonical timeline has no result")
        if str(result.get("occurrence_id") or "") != occurrence_id:
            raise ValueError("run/timeline occurrence identity mismatch")
        timelines.append(timeline)
        artifact_ids.append(artifact_id)
    if not timelines:
        raise ValueError("no materialized canonical timeline available for planning")
    return timelines, artifact_ids


def _load_editor_evidence(
    path: Path | None,
    artifact_path: Path | None,
    *,
    fingerprint: str,
    run_artifact_id: str,
) -> tuple[dict | None, str | None]:
    if path is None and artifact_path is None:
        return None, None
    if path is None or artifact_path is None:
        raise ValueError("editor evidence payload and artifact must be supplied together")
    payload = _load(path)
    artifact = _load(artifact_path)
    _check_artifact(
        artifact,
        fingerprint=fingerprint,
        stage="editor_evidence_shadow",
        role="editor_evidence",
        output=path,
    )
    if payload.get("mode") != "shadow_only":
        raise ValueError("editor evidence is not shadow_only")
    if payload.get("source_run_artifact_id") != run_artifact_id:
        raise ValueError("editor evidence belongs to another source run")
    if run_artifact_id not in {
        str(value) for value in artifact.get("upstream_artifact_ids", [])
    }:
        raise ValueError("editor evidence artifact does not bind source run")
    return payload, str(artifact["artifact_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--editor-evidence", type=Path)
    parser.add_argument("--editor-evidence-artifact", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--mix-context-ms", type=int, default=1500)
    parser.add_argument("--source-context-ms", type=int, default=1000)
    parser.add_argument("--editor-boundary-disagreement-ms", type=int, default=500)
    parser.add_argument("--editor-ambiguous-margin-max", type=float, default=0.08)
    parser.add_argument("--include-editor-missing", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=200)
    parser.add_argument("--faster-whisper-model-id")
    parser.add_argument("--whisperx-model-id")
    parser.add_argument("--whisperx-align-model-id")
    parser.add_argument("--external-forced-aligner-command")
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
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
        run_artifact_id = str(run_artifact["artifact_id"])
        timelines, timeline_ids = _load_timelines(
            run,
            run_artifact,
            fingerprint=fingerprint,
        )
        editor, editor_artifact_id = _load_editor_evidence(
            args.editor_evidence,
            args.editor_evidence_artifact,
            fingerprint=fingerprint,
            run_artifact_id=run_artifact_id,
        )
        config = AlignmentPlannerConfig(
            mix_context_ms=args.mix_context_ms,
            source_context_ms=args.source_context_ms,
            editor_boundary_disagreement_ms=args.editor_boundary_disagreement_ms,
            editor_ambiguous_margin_max=args.editor_ambiguous_margin_max,
            include_editor_missing=args.include_editor_missing,
            max_jobs=args.max_jobs,
        )
        plan = build_alignment_plan(
            run=run,
            timeline_payloads=timelines,
            editor_evidence=editor,
            config=config,
        )
        statuses = inspect_backends(
            faster_whisper_model_id=args.faster_whisper_model_id,
            whisperx_model_id=args.whisperx_model_id,
            whisperx_align_model_id=args.whisperx_align_model_id,
            external_forced_aligner_command=args.external_forced_aligner_command,
        )
        plan.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_run_stage": run_stage,
                "source_run_artifact_id": run_artifact_id,
                "source_editor_evidence_artifact_id": editor_artifact_id,
                "backend_status": [status.to_dict() for status in statuses],
                "backend_execution_performed": False,
            }
        )
        atomic_write_json(args.out, plan)

        upstreams = {run_artifact_id, *timeline_ids}
        if editor_artifact_id:
            upstreams.add(editor_artifact_id)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="alignment_job_planning",
            algorithm_version=__version__,
            outputs=(("alignment_plan", args.out),),
            normalized_config={
                "policy_id": ALIGNMENT_PLANNER_POLICY_ID,
                **config.to_dict(),
                "source_run_artifact_id": run_artifact_id,
                "editor_evidence_artifact_id": editor_artifact_id,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(sorted(upstreams)),
            evidence={
                "mode": "plan_only",
                "backend_execution_performed": False,
                "job_count": plan["summary"]["job_count"],
                "plan_truncated": plan["summary"]["plan_truncated"],
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        AlignmentPlanningError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "plan_only",
                "jobs": plan["summary"]["job_count"],
                "truncated": plan["summary"]["plan_truncated"],
                "backend_execution_performed": False,
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
