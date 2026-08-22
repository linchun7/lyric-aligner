#!/usr/bin/env python3
"""Evaluate canonical Max render cues against the original editor SRT topology."""

from __future__ import annotations

import argparse
import csv
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
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.qa.final_integrity import FinalIntegrityError, validate_srt_report_binding
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.editor_cue_reconcile import (
    SEGMENTATION_AUTHORITY,
    EditorCueReconciliationError,
    canonical_evidence_from_audit,
    evaluate_editor_cue_reconciliation,
)
from task_contract import (
    load_task_manifest,
    resolve_manifest_record,
    verify_manifest_inputs,
)


_SOURCE_SEGMENTATION_AUTHORITY = "canonical_line_evaluation_only"
_SOURCE_RELEASE_BLOCKED_REASON = "editor_cue_reconciliation_required"


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_audit_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("canonical evaluation audit contains no rows")
    return rows


def _validate_evaluation_render(
    *,
    fingerprint: str,
    final_srt: Path,
    report: Path,
    qa_json: Path,
    artifact: dict,
) -> None:
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage="final_render",
    )
    issues.extend(validate_artifact_output(artifact, role="final_srt", path=final_srt))
    issues.extend(validate_artifact_output(artifact, role="audit_csv", path=report))
    issues.extend(validate_artifact_output(artifact, role="qa_json", path=qa_json))
    if issues:
        raise ValueError("invalid canonical evaluation render artifact: " + "; ".join(issues))

    config = artifact.get("normalized_config")
    if not isinstance(config, dict):
        raise ValueError("canonical evaluation render has invalid normalized_config")
    if str(config.get("segmentation_authority") or "") != _SOURCE_SEGMENTATION_AUTHORITY:
        raise ValueError(
            "editor reconciliation requires canonical_line_evaluation_only source render"
        )
    if config.get("legacy_fallback") is not False:
        raise ValueError("editor reconciliation refuses a render that used legacy fallback")

    qa = _load_json(qa_json)
    if qa.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError("canonical evaluation QA belongs to another task")
    if str(qa.get("algorithm_version") or "") != __version__:
        raise ValueError("canonical evaluation QA algorithm version mismatch")
    if qa.get("passed") is not True or qa.get("structurally_valid") is not True:
        raise ValueError("canonical evaluation QA is not structurally valid")
    review_count = qa.get("review_candidate_count")
    if (
        qa.get("fully_reviewed") is not True
        or not isinstance(review_count, int)
        or isinstance(review_count, bool)
        or review_count != 0
    ):
        raise ValueError(
            "canonical evaluation QA must be fully reviewed with integer review_candidate_count=0"
        )
    if qa.get("publish_ready") is not False:
        raise ValueError("canonical evaluation QA must remain publish_ready=false")
    if str(qa.get("segmentation_authority") or "") != _SOURCE_SEGMENTATION_AUTHORITY:
        raise ValueError("canonical evaluation QA segmentation authority mismatch")
    if str(qa.get("release_blocked_reason") or "") != _SOURCE_RELEASE_BLOCKED_REASON:
        raise ValueError("canonical evaluation QA release-blocked reason mismatch")

    validate_srt_report_binding(
        final_srt,
        report,
        expected_task_fingerprint=fingerprint,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--evaluation-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--render-artifact", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        task_issues = verify_manifest_inputs(args.task_manifest, task)
        if task_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(task_issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        source_record = task["inputs"].get("source_srt")
        if not isinstance(source_record, dict):
            raise ValueError("task manifest has no source_srt input")
        source_srt = resolve_manifest_record(args.task_manifest, source_record)

        protected_inputs = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected_inputs.update(
            {
                "evaluation_srt": args.evaluation_srt,
                "evaluation_report": args.report,
                "evaluation_qa": args.qa_json,
                "render_artifact": args.render_artifact,
            }
        )
        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={
                "reconciliation_output": args.out,
                "reconciliation_artifact": args.artifact_out,
            },
        )

        render_artifact = _load_json(args.render_artifact)
        _validate_evaluation_render(
            fingerprint=fingerprint,
            final_srt=args.evaluation_srt,
            report=args.report,
            qa_json=args.qa_json,
            artifact=render_artifact,
        )

        editor_cues = parse_srt_strict(source_srt)
        rendered_cues = parse_srt_strict(args.evaluation_srt)
        audit_rows = _read_audit_rows(args.report)
        canonical_cues = canonical_evidence_from_audit(rendered_cues, audit_rows)
        result = evaluate_editor_cue_reconciliation(editor_cues, canonical_cues)

        payload = {
            "schema_version": result["schema_version"],
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "source_srt_sha256": str(source_record["sha256"]),
            "source_render_artifact_id": str(render_artifact["artifact_id"]),
            "result": result,
        }
        atomic_write_json(args.out, payload)

        reconciliation_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="editor_cue_reconciliation_evaluation",
            algorithm_version=__version__,
            outputs=(("editor_cue_reconciliation", args.out),),
            normalized_config={
                "segmentation_authority": SEGMENTATION_AUTHORITY,
                "production_authority_granted": False,
                "source_srt_sha256": str(source_record["sha256"]),
                "source_render_artifact_id": str(render_artifact["artifact_id"]),
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(str(render_artifact["artifact_id"]),),
            evidence={
                "editor_cue_count": result["editor_cue_count"],
                "canonical_cue_count": result["canonical_cue_count"],
                "canonical_assigned_count": result["canonical_assigned_count"],
                "canonical_unassigned_count": result["canonical_unassigned_count"],
                "status_counts": result["status_counts"],
                "editor_file_order_monotonic": result["editor_file_order_monotonic"],
                "full_topology_candidate": result["full_topology_candidate"],
                "production_authority_granted": False,
                "segmentation_authority": SEGMENTATION_AUTHORITY,
            },
        )
        atomic_write_json(args.artifact_out, reconciliation_artifact)
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        FinalIntegrityError,
        EditorCueReconciliationError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "segmentation_authority": SEGMENTATION_AUTHORITY,
                "production_authority_granted": False,
                "editor_cue_count": result["editor_cue_count"],
                "canonical_cue_count": result["canonical_cue_count"],
                "status_counts": result["status_counts"],
                "full_topology_candidate": result["full_topology_candidate"],
                "artifact_id": reconciliation_artifact["artifact_id"],
                "output": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
