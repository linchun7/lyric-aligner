#!/usr/bin/env python3
"""Apply an auditable display-only lyric policy to a publish-ready v4 render.

This stage never changes cue count, number, start time, occurrence identity, or
canonical line identity. It may change viewer-facing text through task-bound
high-confidence overrides / strong-profanity masking and may optionally shorten an
extreme line-LRC end hold whose source end is only the next lyric start. It can never
extend a cue or move its start. Source canonical text and source timing are preserved
in the output audit.
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
from lyric_aligner.qa.final_integrity import (
    FinalIntegrityError,
    read_audit_rows,
    validate_qa_payload,
    validate_srt_report_binding,
)
from lyric_aligner.srt import Cue, cue_id, parse_srt_strict, text_sha256
from lyric_aligner.text.display_policy import (
    DisplayPolicyError,
    apply_display_policy,
    apply_display_timing_policy,
    load_display_policy,
)
from task_contract import load_task_manifest, verify_manifest_inputs


_PRODUCTION_SEGMENTATION_AUTHORITY = "editor_reconciled"
_DISPLAY_MATERIALIZATION_MODE = "production_display_policy"


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_source_render(
    *,
    fingerprint: str,
    source_srt: Path,
    source_report: Path,
    source_qa_path: Path,
    source_artifact: dict,
) -> tuple[dict, dict, str]:
    issues = validate_upstream_artifact(
        source_artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage="final_render",
    )
    for role, path in (
        ("final_srt", source_srt),
        ("audit_csv", source_report),
        ("qa_json", source_qa_path),
    ):
        issues.extend(validate_artifact_output(source_artifact, role=role, path=path))
    if issues:
        raise ValueError("invalid source final_render artifact: " + "; ".join(issues))

    config = source_artifact.get("normalized_config")
    evidence = source_artifact.get("evidence")
    if not isinstance(config, dict) or not isinstance(evidence, dict):
        raise ValueError("source final_render artifact has invalid config/evidence")
    if str(config.get("segmentation_authority") or "") != _PRODUCTION_SEGMENTATION_AUTHORITY:
        raise ValueError("display policy requires editor_reconciled production source")
    if config.get("production_authority_granted") is not True:
        raise ValueError("display policy requires production_authority_granted=true")
    if str(evidence.get("segmentation_authority") or "") != _PRODUCTION_SEGMENTATION_AUTHORITY:
        raise ValueError("source final_render evidence authority mismatch")
    if evidence.get("publish_ready") is not True:
        raise ValueError("display policy requires publish-ready source render")
    if str(evidence.get("release_blocked_reason") or "").strip():
        raise ValueError("display policy refuses source render with release blocker")

    profile_id = str(config.get("calibration_profile_id") or "").strip()
    profile_version = str(config.get("calibration_profile_version") or "").strip()
    if not profile_id or not profile_version:
        raise ValueError("source final_render is missing calibration profile identity")
    source_qa = validate_qa_payload(
        source_qa_path,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_calibration_profile_id=profile_id,
        expected_calibration_profile_version=profile_version,
    )
    if str(source_qa.get("segmentation_authority") or "") != _PRODUCTION_SEGMENTATION_AUTHORITY:
        raise ValueError("source QA authority mismatch")
    if str(source_qa.get("release_blocked_reason") or "").strip():
        raise ValueError("source QA still records a release blocker")
    if source_qa.get("display_text_policy_applied") is True:
        raise ValueError("display policy cannot be stacked on an already display-processed render")

    validate_srt_report_binding(
        source_srt,
        source_report,
        expected_task_fingerprint=fingerprint,
    )
    return config, source_qa, str(source_artifact["artifact_id"])


def _format_time(value: int) -> str:
    if value < 0:
        raise ValueError("SRT time must be non-negative")
    hour, remain = divmod(value, 3_600_000)
    minute, remain = divmod(remain, 60_000)
    second, millis = divmod(remain, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _atomic_write_text(path: Path, text: str, *, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _write_srt(path: Path, cues: list[Cue]) -> None:
    blocks = [
        f"{cue.number}\n{_format_time(cue.start_ms)} --> {_format_time(cue.end_ms)}\n{cue.text}"
        for cue in cues
    ]
    _atomic_write_text(path, "\n\n".join(blocks) + "\n", encoding="utf-8-sig")


def _write_audit(path: Path, *, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _int_field(row: dict[str, str], key: str, *, position: int) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"audit row {position} has invalid {key}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--source-qa", required=True, type=Path)
    parser.add_argument("--source-render-artifact", required=True, type=Path)
    parser.add_argument("--display-policy", required=True, type=Path)
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

        protected_inputs = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected_inputs.update(
            {
                "source_srt": args.source_srt,
                "source_report": args.source_report,
                "source_qa": args.source_qa,
                "source_render_artifact": args.source_render_artifact,
                "display_policy": args.display_policy,
            }
        )
        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={
                "final_srt": args.final_srt,
                "final_report": args.final_report,
                "final_qa": args.final_qa,
                "display_render_artifact": args.artifact_out,
            },
        )

        source_artifact = _load_json(args.source_render_artifact)
        source_config, source_qa, source_artifact_id = _validate_source_render(
            fingerprint=fingerprint,
            source_srt=args.source_srt,
            source_report=args.source_report,
            source_qa_path=args.source_qa,
            source_artifact=source_artifact,
        )
        policy = load_display_policy(
            args.display_policy,
            expected_task_fingerprint=fingerprint,
        )

        source_cues = parse_srt_strict(args.source_srt)
        source_rows = read_audit_rows(args.source_report)
        if len(source_cues) != len(source_rows):
            raise ValueError("source SRT/audit row count mismatch")

        final_cues: list[Cue] = []
        final_rows: list[dict[str, str]] = []
        override_counts = {key: 0 for key in policy.overrides}
        changed_count = 0
        model_override_count = 0
        sensitive_mask_count = 0
        timing_changed_count = 0

        for position, (cue, source_row) in enumerate(zip(source_cues, source_rows), start=1):
            occurrence_id = str(source_row.get("occurrence_id") or "").strip()
            track_id = str(source_row.get("track_id") or "").strip()
            if not occurrence_id or not track_id:
                raise ValueError(f"audit row {position} lacks occurrence/track identity")
            canonical_line_index = _int_field(
                source_row,
                "canonical_line_index",
                position=position,
            )
            result = apply_display_policy(
                cue.text,
                occurrence_id=occurrence_id,
                track_id=track_id,
                canonical_line_index=canonical_line_index,
                policy=policy,
            )
            end_basis = str(source_row.get("end_basis") or "").strip()
            timing_result = apply_display_timing_policy(
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                end_basis=end_basis,
                policy=policy,
            )
            key = (occurrence_id, track_id, canonical_line_index)
            if result.override_applied:
                override_counts[key] += 1
                model_override_count += 1
            if result.changed:
                changed_count += 1
            if timing_result.changed:
                timing_changed_count += 1
            sensitive_mask_count += result.sensitive_mask_count

            final_cue = Cue(
                number=cue.number,
                start_ms=timing_result.start_ms,
                end_ms=timing_result.end_ms,
                text=result.text,
            )
            final_cues.append(final_cue)

            row = dict(source_row)
            canonical_text = str(source_row.get("text") or "")
            row["canonical_text"] = canonical_text
            row["canonical_text_sha256"] = text_sha256(canonical_text)
            row["source_start_ms"] = str(cue.start_ms)
            row["source_end_ms"] = str(cue.end_ms)
            row["start_ms"] = str(final_cue.start_ms)
            row["end_ms"] = str(final_cue.end_ms)
            row["display_start_ms"] = str(final_cue.start_ms)
            row["display_end_ms"] = str(final_cue.end_ms)
            row["display_timing_changed"] = "true" if timing_result.changed else "false"
            row["display_timing_change_reasons"] = ";".join(timing_result.reasons)
            row["text"] = result.text
            row["display_text"] = result.text
            row["display_text_changed"] = "true" if result.changed else "false"
            row["display_change_reasons"] = ";".join(result.reasons)
            row["display_policy_id"] = policy.policy_id
            row["display_reviewer_model"] = policy.reviewer_model
            row["text_sha256"] = text_sha256(result.text)
            row["cue_id"] = cue_id(position, final_cue)
            final_rows.append(row)

        invalid_override_counts = {
            key: count for key, count in override_counts.items() if count != 1
        }
        if invalid_override_counts:
            raise DisplayPolicyError(
                "every explicit display override must match exactly one final cue: "
                + repr(invalid_override_counts)
            )

        fieldnames = list(source_rows[0].keys())
        for name in (
            "canonical_text",
            "canonical_text_sha256",
            "source_start_ms",
            "source_end_ms",
            "display_start_ms",
            "display_end_ms",
            "display_timing_changed",
            "display_timing_change_reasons",
            "display_text",
            "display_text_changed",
            "display_change_reasons",
            "display_policy_id",
            "display_reviewer_model",
        ):
            if name not in fieldnames:
                fieldnames.append(name)

        _write_srt(args.final_srt, final_cues)
        _write_audit(args.final_report, fieldnames=fieldnames, rows=final_rows)
        binding = validate_srt_report_binding(
            args.final_srt,
            args.final_report,
            expected_task_fingerprint=fingerprint,
        )

        timing_policy = policy.timing_policy
        production_qa = {
            **source_qa,
            "display_text_policy_applied": True,
            "display_text_policy_id": policy.policy_id,
            "display_text_mask_profile": policy.mask_profile,
            "display_text_changed_count": changed_count,
            "display_text_model_override_count": model_override_count,
            "display_text_sensitive_mask_count": sensitive_mask_count,
            "display_text_canonical_preserved_in_audit": True,
            "display_timing_policy_applied": timing_policy is not None,
            "display_timing_policy_mode": timing_policy.mode if timing_policy else "",
            "display_timing_changed_count": timing_changed_count,
            "display_timing_source_preserved_in_audit": True,
            "display_timing_source_duration_at_least_ms": (
                timing_policy.source_duration_at_least_ms if timing_policy else 0
            ),
            "display_timing_max_hold_ms": (
                timing_policy.max_display_hold_ms if timing_policy else 0
            ),
            "display_materialization_mode": _DISPLAY_MATERIALIZATION_MODE,
            "source_production_render_artifact_id": source_artifact_id,
        }
        atomic_write_json(args.final_qa, production_qa)

        source_evidence = source_artifact.get("evidence")
        assert isinstance(source_evidence, dict)
        display_artifact = build_artifact_manifest(
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
                "display_text_policy_applied": True,
                "display_text_policy_id": policy.policy_id,
                "display_text_mask_profile": policy.mask_profile,
                "display_timing_policy_applied": timing_policy is not None,
                "display_timing_policy_mode": timing_policy.mode if timing_policy else "",
                "display_timing_source_duration_at_least_ms": (
                    timing_policy.source_duration_at_least_ms if timing_policy else 0
                ),
                "display_timing_max_hold_ms": (
                    timing_policy.max_display_hold_ms if timing_policy else 0
                ),
                "display_materialization_mode": _DISPLAY_MATERIALIZATION_MODE,
                "source_production_render_artifact_id": source_artifact_id,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(source_artifact_id,),
            evidence={
                **source_evidence,
                "cue_count": binding["cue_count"],
                "display_text_policy_applied": True,
                "display_text_policy_id": policy.policy_id,
                "display_text_changed_count": changed_count,
                "display_text_model_override_count": model_override_count,
                "display_text_sensitive_mask_count": sensitive_mask_count,
                "display_text_canonical_preserved_in_audit": True,
                "display_timing_policy_applied": timing_policy is not None,
                "display_timing_policy_mode": timing_policy.mode if timing_policy else "",
                "display_timing_changed_count": timing_changed_count,
                "display_timing_source_preserved_in_audit": True,
                "display_timing_source_duration_at_least_ms": (
                    timing_policy.source_duration_at_least_ms if timing_policy else 0
                ),
                "display_timing_max_hold_ms": (
                    timing_policy.max_display_hold_ms if timing_policy else 0
                ),
                "display_materialization_mode": _DISPLAY_MATERIALIZATION_MODE,
            },
        )
        atomic_write_json(args.artifact_out, display_artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        FinalIntegrityError,
        DisplayPolicyError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "artifact_id": display_artifact["artifact_id"],
                "cue_count": len(final_cues),
                "display_text_policy_id": policy.policy_id,
                "display_text_changed_count": changed_count,
                "display_text_model_override_count": model_override_count,
                "display_text_sensitive_mask_count": sensitive_mask_count,
                "display_timing_changed_count": timing_changed_count,
                "final_srt": str(args.final_srt),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
