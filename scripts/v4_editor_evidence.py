#!/usr/bin/env python3
"""Build non-authoritative editor/Jianying shadow evidence for v4 canonical timelines."""

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
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.evidence.editor import (
    EDITOR_SHADOW_POLICY_ID,
    EditorEvidenceError,
    build_editor_evidence,
)
from lyric_aligner.srt import parse_srt_strict
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


def _validate_artifact(
    artifact: dict,
    *,
    fingerprint: str,
    stage: str,
    role: str,
    output_path: Path,
) -> None:
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=output_path))
    if issues:
        raise ValueError(f"invalid {stage} artifact: " + "; ".join(issues))


def _validate_run_artifact(
    artifact: dict,
    *,
    fingerprint: str,
    run_path: Path,
) -> str:
    stage = str(artifact.get("stage") or "")
    role = _RUN_ROLES.get(stage)
    if role is None:
        raise ValueError("unsupported v4 source run stage for editor evidence")
    _validate_artifact(
        artifact,
        fingerprint=fingerprint,
        stage=stage,
        role=role,
        output_path=run_path,
    )
    return stage


def _load_effective_timelines(
    run: dict,
    run_artifact: dict,
    *,
    fingerprint: str,
) -> tuple[list[dict], list[str], list[dict]]:
    occurrences = run.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        raise ValueError("source run has no occurrence list")
    run_upstreams = {
        str(value) for value in run_artifact.get("upstream_artifact_ids", [])
    }
    timelines: list[dict] = []
    timeline_ids: list[str] = []
    skipped: list[dict] = []
    seen_occurrences: set[str] = set()

    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            raise ValueError("source run occurrence must be an object")
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        if not occurrence_id or occurrence_id in seen_occurrences:
            raise ValueError("source run occurrence ids must be unique/non-empty")
        seen_occurrences.add(occurrence_id)
        timeline_value = str(occurrence.get("timeline_path") or "").strip()
        artifact_value = str(occurrence.get("timeline_artifact_path") or "").strip()
        if not timeline_value or not artifact_value:
            skipped.append(
                {
                    "occurrence_id": occurrence_id,
                    "reason": "no_materialized_canonical_timeline",
                }
            )
            continue
        timeline_path = Path(timeline_value)
        artifact_path = Path(artifact_value)
        timeline = _load(timeline_path)
        timeline_artifact = _load(artifact_path)
        stage = str(timeline_artifact.get("stage") or "")
        if stage not in _TIMELINE_STAGES:
            raise ValueError(f"unsupported timeline stage {stage!r}")
        _validate_artifact(
            timeline_artifact,
            fingerprint=fingerprint,
            stage=stage,
            role="canonical_timeline",
            output_path=timeline_path,
        )
        artifact_id = str(timeline_artifact.get("artifact_id") or "")
        if artifact_id not in run_upstreams:
            raise ValueError(
                f"timeline artifact for {occurrence_id} is not upstream of source run"
            )
        result = timeline.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"timeline for {occurrence_id} has no result")
        if str(result.get("occurrence_id") or "") != occurrence_id:
            raise ValueError("source run/timeline occurrence identity mismatch")
        track_id = str(occurrence.get("track_id") or result.get("track_id") or "")
        if str(result.get("track_id") or "") != track_id:
            raise ValueError("source run/timeline track identity mismatch")
        timelines.append(timeline)
        timeline_ids.append(artifact_id)

    if not timelines:
        raise ValueError("source run has no materialized canonical timeline for editor evidence")
    return timelines, timeline_ids, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--search-radius-ms", type=int, default=2500)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        task_issues = verify_manifest_inputs(args.task_manifest, task)
        if task_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(task_issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        source_record = task["inputs"]["source_srt"]
        source_srt = resolve_manifest_record(args.task_manifest, source_record)
        editor_cues = parse_srt_strict(source_srt)

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = _validate_run_artifact(
            run_artifact,
            fingerprint=fingerprint,
            run_path=args.run,
        )
        if run.get("algorithm_version") != __version__:
            raise ValueError("source run algorithm version mismatch")
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("source run belongs to another task")
        timelines, timeline_ids, skipped = _load_effective_timelines(
            run,
            run_artifact,
            fingerprint=fingerprint,
        )
        evidence = build_editor_evidence(
            timelines,
            editor_cues,
            search_radius_ms=args.search_radius_ms,
            max_candidates=args.max_candidates,
        )
        evidence.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": run.get(
                    "calibration_profile_version"
                ),
                "calibration_profile_id": run.get("calibration_profile_id"),
                "source_run_stage": run_stage,
                "source_run_artifact_id": str(run_artifact["artifact_id"]),
                "source_srt_sha256": str(source_record["sha256"]),
                "skipped_occurrences": skipped,
            }
        )
        atomic_write_json(args.out, evidence)

        upstreams = {
            str(run_artifact["artifact_id"]),
            *timeline_ids,
        }
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="editor_evidence_shadow",
            algorithm_version=__version__,
            outputs=(("editor_evidence", args.out),),
            normalized_config={
                "mode": "shadow_only",
                "policy_id": EDITOR_SHADOW_POLICY_ID,
                "policy_calibrated": False,
                "search_radius_ms": args.search_radius_ms,
                "max_candidates": args.max_candidates,
                "source_run_artifact_id": str(run_artifact["artifact_id"]),
                "source_srt_sha256": str(source_record["sha256"]),
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(sorted(upstreams)),
            evidence={
                "mode": "shadow_only",
                "automatic_timing_change_allowed": False,
                "editor_cue_count": evidence["editor_cue_count"],
                "canonical_line_count": evidence["summary"]["canonical_line_count"],
                "lines_with_editor_candidate": evidence["summary"][
                    "lines_with_editor_candidate"
                ],
                "skipped_occurrence_count": len(skipped),
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        EditorEvidenceError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "shadow_evidence_only",
                "policy_id": EDITOR_SHADOW_POLICY_ID,
                "automatic_timing_change_allowed": False,
                "editor_cues": evidence["editor_cue_count"],
                "canonical_lines": evidence["summary"]["canonical_line_count"],
                "lines_with_candidates": evidence["summary"][
                    "lines_with_editor_candidate"
                ],
                "skipped_occurrences": len(skipped),
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
