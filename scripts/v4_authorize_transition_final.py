#!/usr/bin/env python3
"""Authorize all adjacent transition reviews from bounded lexical/audio/structural evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    sha256_file,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.review.transition_final_resolution import (
    POLICY_ID,
    SCHEMA_VERSION,
    TransitionFinalResolutionError,
    authorize_final_transitions,
)
from lyric_aligner.text_repair import parse_srt_text
from task_contract import load_task_manifest, verify_manifest_inputs


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate_artifact(
    *, artifact_path: Path, expected_task: str, expected_stage: str,
    output_role: str, output_path: Path,
) -> dict:
    artifact = load_json(artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=expected_task,
        expected_algorithm_version=__version__,
        expected_stage=expected_stage,
    )
    issues.extend(validate_artifact_output(artifact, role=output_role, path=output_path))
    if issues:
        raise ValueError(f"invalid {expected_stage} artifact: " + "; ".join(issues))
    return artifact


def task_input_path(task: dict, key: str) -> Path:
    row = task.get("inputs", {}).get(key)
    if not isinstance(row, dict) or not row.get("path"):
        raise ValueError(f"task input missing: {key}")
    path = (ROOT / str(row["path"])).resolve()
    if not path.is_file():
        raise ValueError(f"task input does not exist: {path}")
    if sha256_file(path) != str(row.get("sha256") or ""):
        raise ValueError(f"task input hash mismatch: {key}")
    return path


def load_bound_timelines(run: dict, *, fingerprint: str) -> tuple[dict[str, dict], list[str]]:
    timelines: dict[str, dict] = {}
    artifact_ids: list[str] = []
    for occurrence in run.get("occurrences", []):
        if not isinstance(occurrence, dict):
            continue
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        timeline_raw = occurrence.get("timeline_path")
        artifact_raw = occurrence.get("timeline_artifact_path")
        if not occurrence_id or not timeline_raw or not artifact_raw:
            raise ValueError("run occurrence lacks timeline lineage")
        timeline_path = Path(str(timeline_raw))
        artifact_path = Path(str(artifact_raw))
        timeline = load_json(timeline_path)
        artifact = validate_artifact(
            artifact_path=artifact_path,
            expected_task=fingerprint,
            expected_stage="canonical_timeline_projection",
            output_role="canonical_timeline",
            output_path=timeline_path,
        )
        result = timeline.get("result")
        if not isinstance(result, dict) or str(result.get("occurrence_id") or "") != occurrence_id:
            raise ValueError(f"timeline occurrence mismatch: {occurrence_id}")
        if timeline.get("algorithm_version") != __version__:
            raise ValueError(f"timeline algorithm mismatch: {occurrence_id}")
        timelines[occurrence_id] = timeline
        artifact_ids.append(str(artifact["artifact_id"]))
    if len(timelines) != len(run.get("occurrences", [])):
        raise ValueError("timeline population is incomplete")
    return timelines, artifact_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--lexical", required=True, type=Path)
    parser.add_argument("--lexical-artifact", required=True, type=Path)
    parser.add_argument("--positional", required=True, type=Path)
    parser.add_argument("--positional-artifact", required=True, type=Path)
    parser.add_argument("--out-decisions", required=True, type=Path)
    parser.add_argument("--out-report", required=True, type=Path)
    parser.add_argument("--out-artifact", required=True, type=Path)
    args = parser.parse_args()
    try:
        task = load_task_manifest(args.task_manifest)
        manifest_issues = verify_manifest_inputs(args.task_manifest, task)
        if manifest_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(manifest_issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        source_srt = task_input_path(task, "source_srt")
        run = load_json(args.run)
        run_artifact = validate_artifact(
            artifact_path=args.run_artifact,
            expected_task=fingerprint,
            expected_stage="production_orchestration",
            output_role="v4_production_run",
            output_path=args.run,
        )
        lexical = load_json(args.lexical)
        lexical_artifact = validate_artifact(
            artifact_path=args.lexical_artifact,
            expected_task=fingerprint,
            expected_stage="adjacent_transition_editor_lexical_evidence",
            output_role="transition_editor_lexical_evidence",
            output_path=args.lexical,
        )
        positional = load_json(args.positional)
        positional_artifact = validate_artifact(
            artifact_path=args.positional_artifact,
            expected_task=fingerprint,
            expected_stage="adjacent_transition_positional_v2",
            output_role="positional_evidence",
            output_path=args.positional,
        )
        timelines, timeline_artifact_ids = load_bound_timelines(run, fingerprint=fingerprint)
        _, editor_cues = parse_srt_text(source_srt.read_text(encoding="utf-8-sig"))
        decisions, report = authorize_final_transitions(
            run=run,
            base_run_artifact_id=str(run_artifact["artifact_id"]),
            run_sha256=sha256_file(args.run),
            source_srt_sha256=sha256_file(source_srt),
            lexical=lexical,
            positional=positional,
            timelines=timelines,
            editor_cues=editor_cues,
        )
        report["lineage"] = {
            "run_artifact_id": str(run_artifact["artifact_id"]),
            "lexical_artifact_id": str(lexical_artifact["artifact_id"]),
            "positional_artifact_id": str(positional_artifact["artifact_id"]),
            "timeline_artifact_ids": sorted(timeline_artifact_ids),
            "lexical_sha256": sha256_file(args.lexical),
            "positional_sha256": sha256_file(args.positional),
        }
        atomic_write_json(args.out_decisions, decisions)
        atomic_write_json(args.out_report, report)
        upstream = {
            str(run_artifact["artifact_id"]),
            str(lexical_artifact["artifact_id"]),
            str(positional_artifact["artifact_id"]),
            *timeline_artifact_ids,
        }
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="transition_final_review_authorization",
            algorithm_version=__version__,
            outputs=(
                ("authorized_review_decisions", args.out_decisions),
                ("transition_final_authorization_report", args.out_report),
            ),
            normalized_config={
                "schema_version": SCHEMA_VERSION,
                "policy_id": POLICY_ID,
                "timing_mutation_performed": False,
                "text_mutation_performed": False,
            },
            upstream_artifact_ids=tuple(sorted(upstream)),
            evidence={
                "resolved_clear_count": int(report["resolved_clear_count"]),
                "confirmed_overlap_count": int(report["confirmed_overlap_count"]),
                "remaining_unresolved_count": int(report["remaining_unresolved_count"]),
            },
        )
        atomic_write_json(args.out_artifact, artifact)
        print(json.dumps({
            "resolved_clear_count": report["resolved_clear_count"],
            "confirmed_overlap_count": report["confirmed_overlap_count"],
            "remaining_unresolved_count": report["remaining_unresolved_count"],
            "artifact_id": artifact["artifact_id"],
        }, ensure_ascii=False))
        return 0
    except (
        OSError, KeyError, ValueError, json.JSONDecodeError,
        TransitionFinalResolutionError,
    ) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
