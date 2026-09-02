#!/usr/bin/env python3
"""Materialize a production v4 render after auditable editor-topology reconciliation.

This first production materializer intentionally supports one narrow rebuttal path:
when the evaluation proves that at least one fully timed canonical cue has no
possible temporal ownership in the immutable editor SRT topology, preserving that
topology would necessarily omit canonical lyric truth.  In that case the already
reviewed canonical evaluation segmentation may become production segmentation,
provided every canonical audit row carries a supported explicit timing format.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
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
from task_contract import load_task_manifest, verify_manifest_inputs


_SOURCE_SEGMENTATION_AUTHORITY = "canonical_line_evaluation_only"
_RECONCILIATION_SEGMENTATION_AUTHORITY = "editor_reconciliation_evaluation_only"
_PRODUCTION_SEGMENTATION_AUTHORITY = "editor_reconciled"
_SOURCE_RELEASE_BLOCKED_REASON = "editor_cue_reconciliation_required"
_REBUTTAL_MODE = "canonical_timed_segmentation_after_editor_topology_rebuttal"
_REBUTTAL_REASON = "canonical_timed_cue_without_editor_temporal_overlap"
_SUPPORTED_TIMING_FORMATS = frozenset({"line_lrc", "enhanced_lrc", "qrc_word_timing"})


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_artifact(
    payload: dict,
    *,
    fingerprint: str,
    stage: str,
    outputs: tuple[tuple[str, Path], ...],
) -> None:
    issues = validate_upstream_artifact(
        payload,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    for role, path in outputs:
        issues.extend(validate_artifact_output(payload, role=role, path=path))
    if issues:
        raise ValueError(f"invalid {stage} artifact: " + "; ".join(issues))


def _validate_evaluation_source(
    *,
    fingerprint: str,
    evaluation_srt: Path,
    report: Path,
    qa_json: Path,
    render_artifact: dict,
) -> tuple[dict, dict]:
    _validate_artifact(
        render_artifact,
        fingerprint=fingerprint,
        stage="final_render",
        outputs=(
            ("final_srt", evaluation_srt),
            ("audit_csv", report),
            ("qa_json", qa_json),
        ),
    )
    config = render_artifact.get("normalized_config")
    if not isinstance(config, dict):
        raise ValueError("canonical evaluation render has invalid normalized_config")
    if str(config.get("segmentation_authority") or "") != _SOURCE_SEGMENTATION_AUTHORITY:
        raise ValueError(
            "production reconciliation requires canonical_line_evaluation_only source render"
        )
    if config.get("legacy_fallback") is not False:
        raise ValueError("production reconciliation refuses a render that used legacy fallback")

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
        or type(review_count) is not int
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

    binding = validate_srt_report_binding(
        evaluation_srt,
        report,
        expected_task_fingerprint=fingerprint,
    )
    return qa, binding


def _validate_reconciliation(
    *,
    fingerprint: str,
    source_srt_sha256: str,
    source_render_artifact_id: str,
    reconciliation_path: Path,
    reconciliation_artifact: dict,
) -> tuple[dict, list[dict]]:
    _validate_artifact(
        reconciliation_artifact,
        fingerprint=fingerprint,
        stage="editor_cue_reconciliation_evaluation",
        outputs=(("editor_cue_reconciliation", reconciliation_path),),
    )
    config = reconciliation_artifact.get("normalized_config")
    if not isinstance(config, dict):
        raise ValueError("editor reconciliation artifact has invalid normalized_config")
    if str(config.get("segmentation_authority") or "") != _RECONCILIATION_SEGMENTATION_AUTHORITY:
        raise ValueError("editor reconciliation artifact authority mismatch")
    if config.get("production_authority_granted") is not False:
        raise ValueError("evaluation reconciliation must not already grant production authority")
    if str(config.get("source_srt_sha256") or "") != source_srt_sha256:
        raise ValueError("editor reconciliation source SRT identity mismatch")
    if str(config.get("source_render_artifact_id") or "") != source_render_artifact_id:
        raise ValueError("editor reconciliation source render identity mismatch")
    if source_render_artifact_id not in {
        str(value) for value in reconciliation_artifact.get("upstream_artifact_ids", [])
    }:
        raise ValueError("editor reconciliation is not upstream-bound to source render")

    payload = _load_json(reconciliation_path)
    if payload.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError("editor reconciliation belongs to another task")
    if str(payload.get("algorithm_version") or "") != __version__:
        raise ValueError("editor reconciliation algorithm version mismatch")
    if str(payload.get("source_srt_sha256") or "") != source_srt_sha256:
        raise ValueError("editor reconciliation payload source SRT mismatch")
    if str(payload.get("source_render_artifact_id") or "") != source_render_artifact_id:
        raise ValueError("editor reconciliation payload source render mismatch")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("editor reconciliation payload has no result")
    if str(result.get("segmentation_authority") or "") != _RECONCILIATION_SEGMENTATION_AUTHORITY:
        raise ValueError("editor reconciliation result authority mismatch")
    if result.get("production_authority_granted") is not False:
        raise ValueError("editor reconciliation result unexpectedly grants production authority")
    editor_file_order_monotonic = result.get("editor_file_order_monotonic") is True
    editor_file_order_recoverable = (
        result.get("editor_file_order_recoverable_nonoverlap_reordering") is True
    )
    if not (editor_file_order_monotonic or editor_file_order_recoverable):
        raise ValueError(
            "editor topology rebuttal requires monotonic editor file order or only "
            "non-overlapping recoverable file-order inversions"
        )
    if result.get("full_topology_candidate") is not False:
        raise ValueError(
            "topology-rebuttal materializer is only for incomplete editor topology; "
            "full topology candidates require the preserve-topology production path"
        )

    unassigned = result.get("canonical_unassigned")
    if not isinstance(unassigned, list):
        raise ValueError("editor reconciliation has invalid canonical_unassigned")
    canonical_count = result.get("canonical_cue_count")
    assigned_count = result.get("canonical_assigned_count")
    unassigned_count = result.get("canonical_unassigned_count")
    if any(type(value) is not int for value in (canonical_count, assigned_count, unassigned_count)):
        raise ValueError("editor reconciliation has invalid canonical assignment counts")
    if canonical_count < 1 or assigned_count < 0 or unassigned_count < 0:
        raise ValueError("editor reconciliation has negative/empty canonical assignment counts")
    if unassigned_count != len(unassigned):
        raise ValueError("editor reconciliation canonical_unassigned_count is inconsistent")
    if assigned_count + unassigned_count != canonical_count:
        raise ValueError("editor reconciliation canonical assignment counts do not close")

    status_counts = result.get("status_counts")
    editor_rows = result.get("editor_cues")
    editor_count = result.get("editor_cue_count")
    if type(editor_count) is not int or editor_count < 1:
        raise ValueError("editor reconciliation has invalid editor_cue_count")
    if not isinstance(status_counts, dict) or not isinstance(editor_rows, list):
        raise ValueError("editor reconciliation has invalid editor status evidence")
    if editor_count != len(editor_rows):
        raise ValueError("editor reconciliation editor_cue_count is inconsistent")
    if any(type(value) is not int or value < 0 for value in status_counts.values()):
        raise ValueError("editor reconciliation has invalid status_counts")
    status_total = sum(status_counts.values())
    if status_total != editor_count:
        raise ValueError("editor reconciliation status_counts do not close")

    witnesses = [
        row
        for row in unassigned
        if isinstance(row, dict) and row.get("reason") == "no_editor_temporal_overlap"
    ]
    if not witnesses:
        raise ValueError(
            "editor topology rebuttal requires at least one canonical cue with "
            "no editor temporal overlap"
        )
    return result, witnesses


def _validate_timed_canonical_report(
    report: Path,
    *,
    expected_cue_count: int,
) -> dict[str, int]:
    with report.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expected_cue_count:
        raise ValueError(
            "canonical audit cue count mismatch: "
            f"expected {expected_cue_count}, got {len(rows)}"
        )
    counts: dict[str, int] = {}
    for position, row in enumerate(rows, start=1):
        timing_format = str(row.get("timing_format") or "").strip()
        if timing_format not in _SUPPORTED_TIMING_FORMATS:
            raise ValueError(
                f"canonical audit row {position} lacks supported explicit timing authority: "
                f"{timing_format or '<missing>'}"
            )
        counts[timing_format] = counts.get(timing_format, 0) + 1
        if not str(row.get("occurrence_id") or "").strip():
            raise ValueError(f"canonical audit row {position} is missing occurrence identity")
        if not str(row.get("track_id") or "").strip():
            raise ValueError(f"canonical audit row {position} is missing track identity")
    return counts


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temp_path = Path(temp_name)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp_path, destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--evaluation-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--render-artifact", required=True, type=Path)
    parser.add_argument("--reconciliation", required=True, type=Path)
    parser.add_argument("--reconciliation-artifact", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--final-report", required=True, type=Path)
    parser.add_argument("--final-qa", required=True, type=Path)
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
        source_srt_sha256 = str(source_record.get("sha256") or "")
        if not source_srt_sha256:
            raise ValueError("task manifest source_srt is missing sha256")

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
                "evaluation_render_artifact": args.render_artifact,
                "reconciliation": args.reconciliation,
                "reconciliation_artifact": args.reconciliation_artifact,
            }
        )
        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={
                "final_srt": args.final_srt,
                "final_report": args.final_report,
                "final_qa": args.final_qa,
                "production_render_artifact": args.artifact_out,
            },
        )

        render_artifact = _load_json(args.render_artifact)
        source_qa, binding = _validate_evaluation_source(
            fingerprint=fingerprint,
            evaluation_srt=args.evaluation_srt,
            report=args.report,
            qa_json=args.qa_json,
            render_artifact=render_artifact,
        )
        source_render_artifact_id = str(render_artifact["artifact_id"])

        reconciliation_artifact = _load_json(args.reconciliation_artifact)
        reconciliation_result, witnesses = _validate_reconciliation(
            fingerprint=fingerprint,
            source_srt_sha256=source_srt_sha256,
            source_render_artifact_id=source_render_artifact_id,
            reconciliation_path=args.reconciliation,
            reconciliation_artifact=reconciliation_artifact,
        )
        reconciliation_artifact_id = str(reconciliation_artifact["artifact_id"])

        evaluation_cues = parse_srt_strict(args.evaluation_srt)
        if int(reconciliation_result.get("canonical_cue_count", -1)) != len(evaluation_cues):
            raise ValueError("editor reconciliation canonical cue count differs from source render")
        if int(binding.get("cue_count", -1)) != len(evaluation_cues):
            raise ValueError("canonical evaluation binding cue count mismatch")
        timing_format_counts = _validate_timed_canonical_report(
            args.report,
            expected_cue_count=len(evaluation_cues),
        )

        _atomic_copy(args.evaluation_srt, args.final_srt)
        _atomic_copy(args.report, args.final_report)

        production_qa = {
            **source_qa,
            "publish_ready": True,
            "segmentation_authority": _PRODUCTION_SEGMENTATION_AUTHORITY,
            "release_blocked_reason": "",
            "production_materialization_mode": _REBUTTAL_MODE,
            "editor_topology_resolution": "rebutted",
            "editor_topology_rebuttal_reason": _REBUTTAL_REASON,
            "editor_topology_rebuttal_witness_count": len(witnesses),
            "source_evaluation_render_artifact_id": source_render_artifact_id,
            "editor_reconciliation_artifact_id": reconciliation_artifact_id,
            "timing_format_counts": timing_format_counts,
        }
        atomic_write_json(args.final_qa, production_qa)

        validate_srt_report_binding(
            args.final_srt,
            args.final_report,
            expected_task_fingerprint=fingerprint,
        )

        source_config = render_artifact.get("normalized_config")
        assert isinstance(source_config, dict)
        production_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="final_render",
            algorithm_version=__version__,
            outputs=(
                ("final_srt", args.final_srt),
                ("audit_csv", args.final_report),
                ("qa_json", args.final_qa),
            ),
            normalized_config={
                **source_config,
                "segmentation_authority": _PRODUCTION_SEGMENTATION_AUTHORITY,
                "production_authority_granted": True,
                "production_materialization_mode": _REBUTTAL_MODE,
                "editor_topology_resolution": "rebutted",
                "editor_topology_rebuttal_reason": _REBUTTAL_REASON,
                "editor_topology_rebuttal_witness_count": len(witnesses),
                "source_evaluation_render_artifact_id": source_render_artifact_id,
                "editor_reconciliation_artifact_id": reconciliation_artifact_id,
                "legacy_fallback": False,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(
                source_render_artifact_id,
                reconciliation_artifact_id,
            ),
            evidence={
                "cue_count": len(evaluation_cues),
                "review_candidate_count": 0,
                "publish_ready": True,
                "segmentation_authority": _PRODUCTION_SEGMENTATION_AUTHORITY,
                "release_blocked_reason": "",
                "production_materialization_mode": _REBUTTAL_MODE,
                "editor_topology_resolution": "rebutted",
                "editor_topology_rebuttal_reason": _REBUTTAL_REASON,
                "editor_topology_rebuttal_witness_count": len(witnesses),
                "timing_format_counts": timing_format_counts,
            },
        )
        atomic_write_json(args.artifact_out, production_artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        FinalIntegrityError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "segmentation_authority": _PRODUCTION_SEGMENTATION_AUTHORITY,
                "production_authority_granted": True,
                "production_materialization_mode": _REBUTTAL_MODE,
                "editor_topology_resolution": "rebutted",
                "rebuttal_witness_count": len(witnesses),
                "cue_count": len(evaluation_cues),
                "artifact_id": production_artifact["artifact_id"],
                "final_srt": str(args.final_srt),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
