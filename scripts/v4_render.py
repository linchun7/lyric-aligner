#!/usr/bin/env python3
"""Render one review-free v4 run into final SRT/audit/QA artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
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
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.srt import Cue, cue_id, text_sha256
from lyric_aligner.timeline.composer import TimelineComposeError, compose_canonical_timelines
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _format_time(value: int) -> str:
    if value < 0:
        raise ValueError("SRT time must be non-negative")
    hour, remain = divmod(value, 3_600_000)
    minute, remain = divmod(remain, 60_000)
    second, millis = divmod(remain, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _write_srt(path: Path, cues) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        f"{cue.number}\n{_format_time(cue.start_ms)} --> {_format_time(cue.end_ms)}\n{cue.text}"
        for cue in cues
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig", newline="\n")


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
    output_path: Path,
) -> str:
    stage = str(artifact.get("stage") or "")
    if stage == "production_orchestration":
        role = "v4_production_run"
    elif stage == "review_resolution":
        role = "v4_reviewed_run"
    else:
        raise ValueError(
            "run artifact must be production_orchestration or review_resolution"
        )
    _validate_artifact(
        artifact,
        fingerprint=fingerprint,
        stage=stage,
        role=role,
        output_path=output_path,
    )
    return stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        task_issues = verify_manifest_inputs(args.task_manifest, task)
        if task_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(task_issues))
        fingerprint = str(task["task_fingerprint_sha256"])

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = _validate_run_artifact(
            run_artifact,
            fingerprint=fingerprint,
            output_path=args.run,
        )
        if run.get("algorithm_version") != __version__:
            raise ValueError("run algorithm version mismatch; rerun v4_run/review")
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("run belongs to another task")
        if run.get("status") != "ready_for_render":
            raise ValueError("run is not ready_for_render; resolve review issues first")
        if run.get("issues") not in ([], None):
            raise ValueError("ready_for_render run unexpectedly contains review issues")
        if run.get("legacy_fallback_used") is not False:
            raise ValueError("final v4 render refuses a run that used legacy fallback")
        run_upstreams = {str(value) for value in run_artifact.get("upstream_artifact_ids", [])}

        if run_stage == "review_resolution":
            resolution = run.get("review_resolution")
            if not isinstance(resolution, dict):
                raise ValueError("reviewed run is missing review_resolution metadata")
            base_run_artifact_id = str(resolution.get("base_run_artifact_id") or "")
            if not base_run_artifact_id or base_run_artifact_id not in run_upstreams:
                raise ValueError("reviewed run is not upstream-bound to its base production run")
            if int(resolution.get("remaining_issue_count", -1)) != 0:
                raise ValueError("reviewed run still records unresolved review issues")
            config_base_id = str(
                run_artifact.get("normalized_config", {}).get("base_run_artifact_id") or ""
            )
            if config_base_id != base_run_artifact_id:
                raise ValueError("review artifact base-run identity mismatch")

        track_assets = _load(args.track_assets)
        asset_artifact = _load(args.asset_artifact)
        _validate_artifact(
            asset_artifact,
            fingerprint=fingerprint,
            stage="asset_resolution",
            role="track_assets",
            output_path=args.track_assets,
        )
        asset_artifact_id = str(asset_artifact["artifact_id"])
        if asset_artifact_id not in run_upstreams:
            raise ValueError(
                "supplied TrackAsset artifact is not upstream of this run"
            )
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=track_assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        if str(run.get("calibration_profile_id") or "") != context.calibration_profile_id:
            raise ValueError("run calibration profile differs from TrackAssets")
        if str(run.get("calibration_profile_version") or "") != context.calibration_profile_version:
            raise ValueError("run calibration profile version differs from TrackAssets")

        timeline_payloads: list[dict] = []
        timeline_artifact_ids: list[str] = []
        occurrence_rows = run.get("occurrences")
        if not isinstance(occurrence_rows, list) or not occurrence_rows:
            raise ValueError("run has no occurrence summaries")
        seen_occurrences: set[str] = set()
        for occurrence in occurrence_rows:
            occurrence_id = str(occurrence.get("occurrence_id") or "")
            if not occurrence_id or occurrence_id in seen_occurrences:
                raise ValueError("run occurrence identity is missing/duplicated")
            seen_occurrences.add(occurrence_id)
            binding = context.binding_by_occurrence_id.get(occurrence_id)
            if binding is None:
                raise ValueError("run occurrence is missing from TrackAssets")
            if bool(occurrence.get("mapping_blocked")):
                raise ValueError("run contains a blocked occurrence")

            timeline_raw = occurrence.get("timeline_path")
            artifact_raw = occurrence.get("timeline_artifact_path")
            if not timeline_raw or not artifact_raw:
                raise ValueError("renderable occurrence is missing canonical timeline artifact")
            timeline_path = Path(str(timeline_raw))
            timeline_artifact_path = Path(str(artifact_raw))
            timeline = _load(timeline_path)
            timeline_artifact = _load(timeline_artifact_path)
            _validate_artifact(
                timeline_artifact,
                fingerprint=fingerprint,
                stage="canonical_timeline_projection",
                role="canonical_timeline",
                output_path=timeline_path,
            )
            timeline_artifact_id = str(timeline_artifact["artifact_id"])
            if timeline_artifact_id not in run_upstreams:
                raise ValueError("canonical timeline artifact is not upstream of run")
            timeline_upstreams = {
                str(value) for value in timeline_artifact.get("upstream_artifact_ids", [])
            }
            if asset_artifact_id not in timeline_upstreams:
                raise ValueError("canonical timeline is not derived from supplied TrackAssets")
            if timeline.get("algorithm_version") != __version__:
                raise ValueError("canonical timeline algorithm version mismatch")
            if timeline.get("task_fingerprint_sha256") != fingerprint:
                raise ValueError("canonical timeline belongs to another task")
            if str(timeline.get("calibration_profile_id") or "") != context.calibration_profile_id:
                raise ValueError("canonical timeline calibration profile mismatch")

            timeline_result = timeline.get("result")
            if not isinstance(timeline_result, dict):
                raise ValueError("canonical timeline result is invalid")
            if str(timeline_result.get("occurrence_id") or "") != binding.occurrence_id:
                raise ValueError("canonical timeline occurrence differs from TrackAsset binding")
            if str(timeline_result.get("track_id") or "") != binding.track_id:
                raise ValueError("canonical timeline track differs from TrackAsset binding")
            if str(timeline_result.get("canonical_selection_sha256") or "") != binding.canonical_selection_sha256:
                raise ValueError(
                    "canonical timeline lyric selection differs from TrackAsset binding"
                )
            if int(timeline_result.get("ordinal", -1)) != binding.ordinal:
                raise ValueError("canonical timeline ordinal differs from TrackOccurrence")

            timeline_payloads.append(timeline)
            timeline_artifact_ids.append(timeline_artifact_id)

        if set(context.binding_by_occurrence_id) != seen_occurrences:
            raise ValueError("run does not contain exactly all resolved TrackOccurrences")

        cues = compose_canonical_timelines(
            timeline_payloads,
            config=context.profile.render,
        )
        _write_srt(args.final_srt, cues)

        args.report.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "position",
            "cue_number",
            "start_ms",
            "end_ms",
            "text",
            "occurrence_id",
            "track_id",
            "ordinal",
            "canonical_line_index",
            "timing_format",
            "end_basis",
            "task_fingerprint_sha256",
            "cue_id",
            "text_sha256",
        ]
        with args.report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for position, rendered in enumerate(cues, start=1):
                cue = Cue(
                    number=rendered.number,
                    start_ms=rendered.start_ms,
                    end_ms=rendered.end_ms,
                    text=rendered.text,
                )
                writer.writerow(
                    {
                        "position": position,
                        "cue_number": rendered.number,
                        "start_ms": rendered.start_ms,
                        "end_ms": rendered.end_ms,
                        "text": rendered.text,
                        "occurrence_id": rendered.occurrence_id,
                        "track_id": rendered.track_id,
                        "ordinal": rendered.ordinal,
                        "canonical_line_index": rendered.canonical_line_index,
                        "timing_format": rendered.timing_format,
                        "end_basis": rendered.end_basis,
                        "task_fingerprint_sha256": fingerprint,
                        "cue_id": cue_id(position, cue),
                        "text_sha256": text_sha256(rendered.text),
                    }
                )

        qa = {
            "schema_version": "1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "calibration_profile_version": context.calibration_profile_version,
            "calibration_profile_id": context.calibration_profile_id,
            "source_run_artifact_id": str(run_artifact["artifact_id"]),
            "source_run_stage": run_stage,
            "source_asset_artifact_id": asset_artifact_id,
            "passed": True,
            "structurally_valid": True,
            "fully_reviewed": True,
            "publish_ready": True,
            "review_candidate_count": 0,
            "cue_count": len(cues),
            "render_config": asdict(context.profile.render),
        }
        atomic_write_json(args.qa_json, qa)

        render_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="final_render",
            algorithm_version=__version__,
            outputs=(
                ("final_srt", args.final_srt),
                ("audit_csv", args.report),
                ("qa_json", args.qa_json),
            ),
            normalized_config={
                **context.artifact_config(),
                "render": asdict(context.profile.render),
                "source_run_stage": run_stage,
                "legacy_fallback": False,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(
                asset_artifact_id,
                str(run_artifact["artifact_id"]),
                *timeline_artifact_ids,
            ),
            evidence={
                "cue_count": len(cues),
                "occurrence_count": len(occurrence_rows),
                "review_candidate_count": 0,
                "source_run_stage": run_stage,
            },
        )
        atomic_write_json(args.artifact_out, render_artifact)
    except (
        OSError,
        KeyError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        TimelineComposeError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "algorithm_version": __version__,
                "cue_count": len(cues),
                "publish_ready": True,
                "source_run_stage": run_stage,
                "artifact_id": render_artifact["artifact_id"],
                "final_srt": str(args.final_srt),
                "report": str(args.report),
                "qa_json": str(args.qa_json),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
