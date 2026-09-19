#!/usr/bin/env python3
"""Authorize lyric-sequential review decisions for Max transition occurrence ambiguities."""
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
from lyric_aligner.review.transition_lyric_resolution import (
    POLICY_ID,
    SCHEMA_VERSION,
    TransitionLyricResolutionError,
    authorize_transition_lyrics,
)
from task_contract import load_task_manifest, verify_manifest_inputs


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


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


def validate_artifact(
    *,
    artifact_path: Path,
    expected_task: str,
    expected_stage: str,
    output_role: str,
    output_path: Path,
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
        decisions, report = authorize_transition_lyrics(
            run=run,
            base_run_artifact_id=str(run_artifact["artifact_id"]),
            run_sha256=sha256_file(args.run),
            source_srt_sha256=sha256_file(source_srt),
            lexical=lexical,
            positional=positional,
        )
        report["lineage"] = {
            "run_artifact_id": str(run_artifact["artifact_id"]),
            "lexical_artifact_id": str(lexical_artifact["artifact_id"]),
            "positional_artifact_id": str(positional_artifact["artifact_id"]),
            "lexical_sha256": sha256_file(args.lexical),
            "positional_sha256": sha256_file(args.positional),
        }
        atomic_write_json(args.out_decisions, decisions)
        atomic_write_json(args.out_report, report)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="transition_lyric_review_authorization",
            algorithm_version=__version__,
            outputs=(
                ("authorized_review_decisions", args.out_decisions),
                ("transition_lyric_authorization_report", args.out_report),
            ),
            normalized_config={
                "schema_version": SCHEMA_VERSION,
                "policy_id": POLICY_ID,
                "confirmed_overlap_authority_granted": False,
                "timing_mutation_performed": False,
                "text_mutation_performed": False,
            },
            upstream_artifact_ids=tuple(sorted({
                str(run_artifact["artifact_id"]),
                str(lexical_artifact["artifact_id"]),
                str(positional_artifact["artifact_id"]),
            })),
            evidence={
                "resolved_clear_count": int(report["resolved_clear_count"]),
                "remaining_unresolved_count": int(report["remaining_unresolved_count"]),
            },
        )
        atomic_write_json(args.out_artifact, artifact)
        print(json.dumps({
            "resolved_clear_count": report["resolved_clear_count"],
            "remaining_unresolved_count": report["remaining_unresolved_count"],
            "decisions": str(args.out_decisions),
            "report": str(args.out_report),
            "artifact_id": artifact["artifact_id"],
        }, ensure_ascii=False))
        return 0
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        TransitionLyricResolutionError,
    ) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
