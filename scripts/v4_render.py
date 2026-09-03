#!/usr/bin/env python3
"""Render one effective review-free v4 run into evaluation SRT/audit/QA artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
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
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.srt import Cue, cue_id, text_sha256
from lyric_aligner.timeline.composer import (
    TimelineComposeError,
    compose_canonical_timelines,
)
from task_contract import load_task_manifest, verify_manifest_inputs


_SEGMENTATION_AUTHORITY = "canonical_line_evaluation_only"
_RELEASE_BLOCKED_REASON = "editor_cue_reconciliation_required"


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


def _atomic_write_text(path: Path, text: str, *, encoding: str, newline: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_write_csv(
    path: Path,
    *,
    fieldnames: list[str],
    rows: list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _write_srt(path: Path, cues) -> None:
    blocks = [
        f"{cue.number}\n{_format_time(cue.start_ms)} --> "
        f"{_format_time(cue.end_ms)}\n{cue.text}"
        for cue in cues
    ]
    _atomic_write_text(
        path,
        "\n\n".join(blocks) + "\n",
        encoding="utf-8-sig",
        newline="\n",
    )


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


def _artifact_config(artifact: dict, *, label: str) -> dict:
    config = artifact.get("normalized_config")
    if not isinstance(config, dict):
        raise ValueError(f"{label} has invalid normalized_config")
    return config


def _json_int(
    payload: dict,
    key: str,
    *,
    label: str,
    minimum: int | None = None,
) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} {key} must be a JSON integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} {key} must be >= {minimum}")
    return value


def _validate_run_artifact(
    artifact: dict,
    *,
    fingerprint: str,
    output_path: Path,
) -> str:
    stage = str(artifact.get("stage") or "")
    roles = {
        "production_orchestration": "v4_production_run",
        "review_resolution": "v4_reviewed_run",
        "overlap_recomposition": "v4_recomposed_run",
        "cut_rebuild": "v4_cut_rebuilt_run",
        "combined_recomposition": "v4_combined_run",
        "reference_retime": "v4_reference_retimed_run",
    }
    role = roles.get(stage)
    if role is None:
        raise ValueError(
            "run artifact must be production_orchestration, review_resolution, "
            "overlap_recomposition, cut_rebuild, combined_recomposition, or reference_retime"
        )
    _validate_artifact(
        artifact,
        fingerprint=fingerprint,
        stage=stage,
        role=role,
        output_path=output_path,
    )
    _artifact_config(artifact, label="run artifact")
    return stage


def _unique_ids(rows: object, *, label: str, required: bool) -> set[str]:
    if rows is None and not required:
        return set()
    if not isinstance(rows, list) or (required and not rows):
        raise ValueError(f"{label} must be a non-empty list")
    values = {str(value).strip() for value in rows if str(value).strip()}
    if len(values) != len(rows):
        raise ValueError(f"{label} contains missing/duplicate artifact IDs")
    return values


def _validate_review_only(run: dict, artifact: dict, upstreams: set[str]) -> None:
    resolution = run.get("review_resolution")
    if not isinstance(resolution, dict):
        raise ValueError("reviewed run is missing review_resolution metadata")
    base_run_artifact_id = str(resolution.get("base_run_artifact_id") or "")
    if not base_run_artifact_id or base_run_artifact_id not in upstreams:
        raise ValueError("reviewed run is not upstream-bound to its base production run")
    if _json_int(
        resolution,
        "remaining_issue_count",
        label="review_resolution",
        minimum=0,
    ) != 0:
        raise ValueError("reviewed run still records unresolved review issues")
    config_base_id = str(
        _artifact_config(artifact, label="review artifact").get("base_run_artifact_id")
        or ""
    )
    if config_base_id != base_run_artifact_id:
        raise ValueError("review artifact base-run identity mismatch")


def _validate_overlap_metadata(
    run: dict,
    artifact: dict,
    upstreams: set[str],
    *,
    require_resolved: bool,
) -> tuple[dict, list[dict], set[str]]:
    metadata = run.get("overlap_recomposition")
    if not isinstance(metadata, dict):
        raise ValueError("run is missing overlap_recomposition metadata")
    source_review_id = str(metadata.get("source_review_artifact_id") or "")
    if not source_review_id or source_review_id not in upstreams:
        raise ValueError("overlap recomposition is not bound to its review artifact")
    if require_resolved and _json_int(
        metadata,
        "remaining_issue_count",
        label="overlap_recomposition",
        minimum=0,
    ) != 0:
        raise ValueError("overlap recomposition still records unresolved review issues")
    config_review_id = str(
        _artifact_config(artifact, label="overlap artifact").get(
            "source_review_artifact_id"
        )
        or ""
    )
    if config_review_id != source_review_id:
        raise ValueError("overlap artifact source-review identity mismatch")
    regions = run.get("confirmed_overlap_regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("overlap recomposition has no confirmed_overlap_regions")
    timeline_ids = _unique_ids(
        metadata.get("new_timeline_artifact_ids"),
        label="overlap new_timeline_artifact_ids",
        required=True,
    )
    if timeline_ids - upstreams:
        raise ValueError("overlap timeline artifacts are not upstream of run")
    return metadata, regions, timeline_ids


def _validate_cut_metadata(
    run: dict,
    artifact: dict,
    upstreams: set[str],
    *,
    require_resolved: bool,
) -> tuple[dict, set[str], set[str]]:
    metadata = run.get("cut_rebuild")
    if not isinstance(metadata, dict):
        raise ValueError("run is missing cut_rebuild metadata")
    source_review_id = str(metadata.get("source_review_artifact_id") or "")
    if not source_review_id or source_review_id not in upstreams:
        raise ValueError("cut rebuild is not bound to its review artifact")
    if require_resolved and _json_int(
        metadata,
        "remaining_issue_count",
        label="cut_rebuild",
        minimum=0,
    ) != 0:
        raise ValueError("cut rebuild still records unresolved review issues")
    if _json_int(
        metadata,
        "canonical_fragment_issue_count",
        label="cut_rebuild",
        minimum=0,
    ) != 0:
        raise ValueError("cut rebuild still contains unresolved canonical fragments")
    if _json_int(
        metadata,
        "rebuilt_occurrence_count",
        label="cut_rebuild",
        minimum=0,
    ) < 1:
        raise ValueError("cut rebuild has no rebuilt occurrence")
    config_review_id = str(
        _artifact_config(artifact, label="cut-rebuild artifact").get(
            "source_review_artifact_id"
        )
        or ""
    )
    if config_review_id != source_review_id:
        raise ValueError("cut-rebuild artifact source-review identity mismatch")
    mapping_ids = _unique_ids(
        metadata.get("new_mapping_artifact_ids"),
        label="cut new_mapping_artifact_ids",
        required=True,
    )
    timeline_ids = _unique_ids(
        metadata.get("new_timeline_artifact_ids"),
        label="cut new_timeline_artifact_ids",
        required=True,
    )
    if (mapping_ids | timeline_ids) - upstreams:
        raise ValueError("cut mapping/timeline artifacts are not upstream of run")
    return metadata, mapping_ids, timeline_ids


def _validate_combined_metadata(
    run: dict,
    artifact: dict,
    upstreams: set[str],
    *,
    source_review_id: str,
) -> tuple[dict, set[str]]:
    metadata = run.get("combined_recomposition")
    if not isinstance(metadata, dict):
        raise ValueError("combined run is missing combined_recomposition metadata")
    if _json_int(
        metadata,
        "remaining_issue_count",
        label="combined_recomposition",
        minimum=0,
    ) != 0:
        raise ValueError("combined recomposition still records unresolved review issues")
    if str(metadata.get("source_review_artifact_id") or "") != source_review_id:
        raise ValueError("combined recomposition source-review identity mismatch")
    source_cut_id = str(metadata.get("source_cut_artifact_id") or "")
    source_overlap_id = str(metadata.get("source_overlap_artifact_id") or "")
    if not source_cut_id or source_cut_id not in upstreams:
        raise ValueError("combined run is not upstream-bound to cut_rebuild")
    if not source_overlap_id or source_overlap_id not in upstreams:
        raise ValueError("combined run is not upstream-bound to overlap_recomposition")
    config = _artifact_config(artifact, label="combined artifact")
    if str(config.get("source_cut_artifact_id") or "") != source_cut_id:
        raise ValueError("combined artifact cut source identity mismatch")
    if str(config.get("source_overlap_artifact_id") or "") != source_overlap_id:
        raise ValueError("combined artifact overlap source identity mismatch")
    if str(config.get("source_review_artifact_id") or "") != source_review_id:
        raise ValueError("combined artifact review source identity mismatch")
    timeline_ids = _unique_ids(
        metadata.get("new_timeline_artifact_ids"),
        label="combined new_timeline_artifact_ids",
        required=False,
    )
    if timeline_ids - upstreams:
        raise ValueError("combined timeline artifacts are not upstream of run")
    expected_count = _json_int(
        metadata,
        "combined_occurrence_count",
        label="combined_recomposition",
        minimum=0,
    )
    if expected_count != len(timeline_ids):
        raise ValueError("combined occurrence count differs from combined timeline IDs")
    return metadata, timeline_ids


def _validate_reference_retime_metadata(
    run: dict,
    artifact: dict,
    upstreams: set[str],
) -> tuple[dict, set[str]]:
    metadata = run.get("reference_retime")
    if not isinstance(metadata, dict):
        raise ValueError("reference-retimed run is missing reference_retime metadata")
    source_run_id = str(metadata.get("source_run_artifact_id") or "")
    if not source_run_id or source_run_id not in upstreams:
        raise ValueError("reference retime is not upstream-bound to its source run")
    source_run_stage = str(metadata.get("source_run_stage") or "")
    if source_run_stage not in {"review_resolution", "overlap_recomposition"}:
        raise ValueError(
            "reference retime requires a review_resolution or overlap_recomposition source run"
        )
    source_review_id = str(metadata.get("source_review_artifact_id") or "")
    if not source_review_id:
        raise ValueError("reference retime is missing source review lineage")
    if source_run_stage == "review_resolution":
        if source_review_id != source_run_id:
            raise ValueError(
                "reference retime review_resolution source must identify itself as source review artifact"
            )
    else:
        overlap_metadata = run.get("overlap_recomposition")
        if not isinstance(overlap_metadata, dict):
            raise ValueError("reference retime is missing source overlap lineage")
        if str(overlap_metadata.get("source_review_artifact_id") or "") != source_review_id:
            raise ValueError("reference retime source-review identity differs from overlap metadata")
    reference_fp = str(metadata.get("reference_task_fingerprint_sha256") or "")
    reference_timeline_id = str(metadata.get("reference_timeline_artifact_id") or "")
    spec_sha = str(metadata.get("retime_spec_sha256") or "")
    if not reference_fp or not reference_timeline_id or not spec_sha:
        raise ValueError("reference retime metadata is missing reference lineage identity")
    if reference_timeline_id not in upstreams:
        raise ValueError("reference timeline artifact is not upstream of reference-retimed run")
    count = _json_int(
        metadata,
        "retimed_occurrence_count",
        label="reference_retime",
        minimum=1,
    )
    timeline_ids = _unique_ids(
        metadata.get("timeline_artifact_ids"),
        label="reference retime timeline_artifact_ids",
        required=True,
    )
    if count != len(timeline_ids):
        raise ValueError("reference retime occurrence count differs from timeline IDs")
    if timeline_ids - upstreams:
        raise ValueError("reference-retimed timeline artifacts are not upstream of run")
    config = _artifact_config(artifact, label="reference-retime artifact")
    expected = {
        "source_run_artifact_id": source_run_id,
        "source_run_stage": source_run_stage,
        "source_review_artifact_id": source_review_id,
        "reference_task_fingerprint_sha256": reference_fp,
        "reference_timeline_artifact_id": reference_timeline_id,
        "retime_spec_sha256": spec_sha,
    }
    for key, value in expected.items():
        if str(config.get(key) or "") != value:
            raise ValueError(f"reference-retime artifact {key} mismatch")
    if _json_int(
        config,
        "retimed_occurrence_count",
        label="reference-retime artifact",
        minimum=1,
    ) != count:
        raise ValueError("reference-retime artifact occurrence count mismatch")
    return metadata, timeline_ids


def _reference_retime_materialization_stage(metadata: dict) -> str:
    source_stage = str(metadata.get("source_run_stage") or "")
    if source_stage not in {"review_resolution", "overlap_recomposition"}:
        raise ValueError("reference retime has unsupported materialization source stage")
    return source_stage


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

        protected_inputs = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected_inputs.update(
            {
                "run": args.run,
                "run_artifact": args.run_artifact,
                "track_assets": args.track_assets,
                "asset_artifact": args.asset_artifact,
            }
        )

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = _validate_run_artifact(
            run_artifact,
            fingerprint=fingerprint,
            output_path=args.run,
        )
        if run.get("algorithm_version") != __version__:
            raise ValueError("run algorithm version mismatch; rerun the current v4 chain")
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("run belongs to another task")
        if run.get("status") != "ready_for_render":
            raise ValueError("run is not ready_for_render; resolve/rebuild review issues first")
        if run.get("issues") not in ([], None):
            raise ValueError("ready_for_render run unexpectedly contains review issues")
        if run.get("legacy_fallback_used") is not False:
            raise ValueError("final v4 render refuses a run that used legacy fallback")
        run_upstreams = {
            str(value) for value in run_artifact.get("upstream_artifact_ids", [])
        }

        reference_retime_metadata: dict = {}
        expected_reference_retime_timeline_ids: set[str] = set()
        materialization_stage = run_stage
        if run_stage == "reference_retime":
            (
                reference_retime_metadata,
                expected_reference_retime_timeline_ids,
            ) = _validate_reference_retime_metadata(run, run_artifact, run_upstreams)
            materialization_stage = _reference_retime_materialization_stage(reference_retime_metadata)

        if run_stage == "review_resolution":
            _validate_review_only(run, run_artifact, run_upstreams)

        overlap_metadata: dict = {}
        confirmed_overlap_regions: list[dict] = []
        expected_overlap_timeline_ids: set[str] = set()
        if materialization_stage in {"overlap_recomposition", "combined_recomposition"}:
            (
                overlap_metadata,
                confirmed_overlap_regions,
                expected_overlap_timeline_ids,
            ) = _validate_overlap_metadata(
                run,
                run_artifact,
                run_upstreams,
                require_resolved=(materialization_stage == "overlap_recomposition"),
            )

        cut_metadata: dict = {}
        expected_cut_mapping_ids: set[str] = set()
        expected_cut_timeline_ids: set[str] = set()
        if materialization_stage in {"cut_rebuild", "combined_recomposition"}:
            (
                cut_metadata,
                expected_cut_mapping_ids,
                expected_cut_timeline_ids,
            ) = _validate_cut_metadata(
                run,
                run_artifact,
                run_upstreams,
                require_resolved=(materialization_stage == "cut_rebuild"),
            )

        combined_metadata: dict = {}
        expected_combined_timeline_ids: set[str] = set()
        if materialization_stage == "combined_recomposition":
            cut_review_id = str(cut_metadata.get("source_review_artifact_id") or "")
            overlap_review_id = str(
                overlap_metadata.get("source_review_artifact_id") or ""
            )
            if not cut_review_id or cut_review_id != overlap_review_id:
                raise ValueError("combined run cut/overlap review identities differ")
            combined_metadata, expected_combined_timeline_ids = _validate_combined_metadata(
                run,
                run_artifact,
                run_upstreams,
                source_review_id=cut_review_id,
            )

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
            raise ValueError("supplied TrackAsset artifact is not upstream of this run")
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=track_assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        if str(run.get("calibration_profile_id") or "") != context.calibration_profile_id:
            raise ValueError("run calibration profile differs from TrackAssets")
        if str(run.get("calibration_profile_version") or "") != (
            context.calibration_profile_version
        ):
            raise ValueError("run calibration profile version differs from TrackAssets")

        timeline_payloads: list[dict] = []
        timeline_artifact_ids: list[str] = []
        occurrence_rows = run.get("occurrences")
        if not isinstance(occurrence_rows, list) or not occurrence_rows:
            raise ValueError("run has no occurrence summaries")
        seen_occurrences: set[str] = set()
        cut_rebuilt_occurrence_count = 0
        overlap_recomposed_occurrence_count = 0
        combined_occurrence_count = 0

        for occurrence in occurrence_rows:
            if not isinstance(occurrence, dict):
                raise ValueError("run occurrence summary must be an object")
            occurrence_id = str(occurrence.get("occurrence_id") or "")
            if not occurrence_id or occurrence_id in seen_occurrences:
                raise ValueError("run occurrence identity is missing/duplicated")
            seen_occurrences.add(occurrence_id)
            binding = context.binding_by_occurrence_id.get(occurrence_id)
            if binding is None:
                raise ValueError("run occurrence is missing from TrackAssets")
            if bool(occurrence.get("mapping_blocked")):
                raise ValueError("run contains a blocked occurrence")

            timeline_path = Path(str(occurrence.get("timeline_path") or ""))
            timeline_artifact_path = Path(
                str(occurrence.get("timeline_artifact_path") or "")
            )
            if not timeline_path.is_file() or not timeline_artifact_path.is_file():
                raise ValueError("renderable occurrence is missing canonical timeline artifact")
            protected_inputs[f"timeline_{occurrence_id}"] = timeline_path
            protected_inputs[f"timeline_artifact_{occurrence_id}"] = timeline_artifact_path
            timeline = _load(timeline_path)
            timeline_artifact = _load(timeline_artifact_path)
            timeline_stage = str(
                occurrence.get("timeline_stage") or "canonical_timeline_projection"
            )
            if timeline_stage not in {
                "canonical_timeline_projection",
                "overlap_timeline_recomposition",
                "cut_timeline_rebuild",
                "combined_timeline_recomposition",
                "reference_timeline_retime",
            }:
                raise ValueError("run occurrence uses an unsupported timeline stage")
            _validate_artifact(
                timeline_artifact,
                fingerprint=fingerprint,
                stage=timeline_stage,
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
            if str(timeline.get("calibration_profile_id") or "") != (
                context.calibration_profile_id
            ):
                raise ValueError("canonical timeline calibration profile mismatch")

            result = timeline.get("result")
            if not isinstance(result, dict):
                raise ValueError("canonical timeline result is invalid")
            if str(result.get("occurrence_id") or "") != binding.occurrence_id:
                raise ValueError("canonical timeline occurrence differs from TrackAsset binding")
            if str(result.get("track_id") or "") != binding.track_id:
                raise ValueError("canonical timeline track differs from TrackAsset binding")
            if str(result.get("canonical_selection_sha256") or "") != (
                binding.canonical_selection_sha256
            ):
                raise ValueError("canonical timeline lyric selection differs from TrackAsset binding")
            if int(result.get("ordinal", -1)) != binding.ordinal:
                raise ValueError("canonical timeline ordinal differs from TrackOccurrence")

            cut_rebuilt = bool(occurrence.get("cut_rebuilt"))
            overlap_recomposed = bool(occurrence.get("overlap_recomposed"))
            combined_recomposed = bool(occurrence.get("combined_recomposed"))
            cut_rebuilt_occurrence_count += int(cut_rebuilt)
            overlap_recomposed_occurrence_count += int(overlap_recomposed)
            combined_occurrence_count += int(combined_recomposed)

            if timeline_stage == "reference_timeline_retime":
                if run_stage != "reference_retime" or occurrence.get("reference_retimed") is not True:
                    raise ValueError("reference-retimed timeline requires reference_retime run/occurrence")
                if timeline_artifact_id not in expected_reference_retime_timeline_ids:
                    raise ValueError("reference-retimed timeline artifact is not declared by run metadata")
                if result.get("reference_retimed") is not True:
                    raise ValueError("reference-retimed timeline result is not marked reference_retimed")
                if result.get("projection_issues") not in ([], None):
                    raise ValueError("reference-retimed timeline still contains projection issues")
                source_target_timeline_id = str(
                    timeline.get("source_target_timeline_artifact_id") or ""
                )
                source_reference_timeline_id = str(
                    timeline.get("source_reference_timeline_artifact_id") or ""
                )
                if not source_target_timeline_id or source_target_timeline_id not in timeline_upstreams:
                    raise ValueError("reference-retimed timeline is not bound to target source timeline")
                if not source_reference_timeline_id or source_reference_timeline_id not in timeline_upstreams:
                    raise ValueError("reference-retimed timeline is not bound to reference source timeline")
                if source_reference_timeline_id != str(
                    reference_retime_metadata.get("reference_timeline_artifact_id") or ""
                ):
                    raise ValueError("reference-retimed timeline reference identity mismatch")
                if str(timeline.get("reference_task_fingerprint_sha256") or "") != str(
                    reference_retime_metadata.get("reference_task_fingerprint_sha256") or ""
                ):
                    raise ValueError("reference-retimed timeline reference task mismatch")
                if str(timeline.get("retime_spec_sha256") or "") != str(
                    reference_retime_metadata.get("retime_spec_sha256") or ""
                ):
                    raise ValueError("reference-retimed timeline spec identity mismatch")
            elif timeline_stage == "cut_timeline_rebuild":
                if materialization_stage not in {"cut_rebuild", "combined_recomposition"} or not cut_rebuilt:
                    raise ValueError("cut timeline requires a cut-capable run/occurrence")
                if overlap_recomposed:
                    raise ValueError("overlap-recomposed occurrence cannot end on cut-only timeline")
                if result.get("cut_aware") is not True:
                    raise ValueError("cut canonical timeline is not marked cut_aware")
                if result.get("projection_issues") not in ([], None):
                    raise ValueError("cut canonical timeline still contains projection issues")
                if timeline_artifact_id not in expected_cut_timeline_ids:
                    raise ValueError("cut timeline artifact is not declared by cut metadata")
                mapping_artifact_id = str(timeline.get("cut_mapping_artifact_id") or "")
                if not mapping_artifact_id:
                    raise ValueError("cut timeline has no cut mapping artifact identity")
                if mapping_artifact_id not in timeline_upstreams:
                    raise ValueError("cut timeline is not upstream-bound to its cut mapping")
                if mapping_artifact_id not in expected_cut_mapping_ids:
                    raise ValueError("cut mapping artifact is not declared by cut metadata")
            elif timeline_stage == "overlap_timeline_recomposition":
                if materialization_stage not in {"overlap_recomposition", "combined_recomposition"}:
                    raise ValueError("overlap timeline requires an overlap-capable run")
                if cut_rebuilt:
                    raise ValueError("cut-rebuilt occurrence requires combined timeline after overlap")
                if not overlap_recomposed:
                    raise ValueError("overlap timeline occurrence is not marked overlap_recomposed")
                if timeline_artifact_id not in expected_overlap_timeline_ids:
                    raise ValueError("overlap timeline artifact is not declared by overlap metadata")
            elif timeline_stage == "combined_timeline_recomposition":
                if materialization_stage != "combined_recomposition":
                    raise ValueError("combined timeline requires combined_recomposition run")
                if not (cut_rebuilt and overlap_recomposed and combined_recomposed):
                    raise ValueError("combined timeline occurrence is missing materialization flags")
                if result.get("cut_aware") is not True:
                    raise ValueError("combined timeline lost cut-aware state")
                if result.get("projection_issues") not in ([], None):
                    raise ValueError("combined timeline still contains projection issues")
                if timeline_artifact_id not in expected_combined_timeline_ids:
                    raise ValueError("combined timeline artifact is not declared by combined metadata")
                source_cut_timeline_id = str(
                    timeline.get("source_cut_timeline_artifact_id") or ""
                )
                source_overlap_timeline_id = str(
                    timeline.get("source_overlap_timeline_artifact_id") or ""
                )
                if not source_cut_timeline_id or source_cut_timeline_id not in timeline_upstreams:
                    raise ValueError("combined timeline is not bound to source cut timeline")
                if not source_overlap_timeline_id or source_overlap_timeline_id not in timeline_upstreams:
                    raise ValueError("combined timeline is not bound to source overlap timeline")
                if source_cut_timeline_id not in expected_cut_timeline_ids:
                    raise ValueError("combined timeline source cut timeline is not declared by cut metadata")
                if source_overlap_timeline_id not in expected_overlap_timeline_ids:
                    raise ValueError(
                        "combined timeline source overlap timeline is not declared by overlap metadata"
                    )
            else:
                if cut_rebuilt or overlap_recomposed or combined_recomposed:
                    raise ValueError("materialized occurrence cannot fall back to primary timeline")

            timeline_payloads.append(timeline)
            timeline_artifact_ids.append(timeline_artifact_id)

        if set(context.binding_by_occurrence_id) != seen_occurrences:
            raise ValueError("run does not contain exactly all resolved TrackOccurrences")
        if cut_metadata and cut_rebuilt_occurrence_count != _json_int(
            cut_metadata,
            "rebuilt_occurrence_count",
            label="cut_rebuild",
            minimum=0,
        ):
            raise ValueError("cut-rebuild occurrence count differs from cut metadata")
        if overlap_metadata and overlap_recomposed_occurrence_count < 1:
            raise ValueError("overlap metadata exists but no occurrence is overlap_recomposed")
        if combined_metadata and combined_occurrence_count != _json_int(
            combined_metadata,
            "combined_occurrence_count",
            label="combined_recomposition",
            minimum=0,
        ):
            raise ValueError("combined occurrence count differs from combined metadata")

        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={
                "final_srt": args.final_srt,
                "audit_csv": args.report,
                "qa_json": args.qa_json,
                "render_artifact": args.artifact_out,
            },
        )

        cues = compose_canonical_timelines(
            timeline_payloads,
            config=context.profile.render,
            confirmed_overlap_regions=confirmed_overlap_regions,
        )
        _write_srt(args.final_srt, cues)

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
        audit_rows: list[dict[str, object]] = []
        for position, rendered in enumerate(cues, start=1):
            cue = Cue(
                number=rendered.number,
                start_ms=rendered.start_ms,
                end_ms=rendered.end_ms,
                text=rendered.text,
            )
            audit_rows.append(
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
        _atomic_write_csv(args.report, fieldnames=fieldnames, rows=audit_rows)

        rebuilt_cut_count = (
            _json_int(
                cut_metadata,
                "rebuilt_occurrence_count",
                label="cut_rebuild",
                minimum=0,
            )
            if cut_metadata
            else 0
        )
        combined_count = (
            _json_int(
                combined_metadata,
                "combined_occurrence_count",
                label="combined_recomposition",
                minimum=0,
            )
            if combined_metadata
            else 0
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
            "publish_ready": False,
            "segmentation_authority": _SEGMENTATION_AUTHORITY,
            "release_blocked_reason": _RELEASE_BLOCKED_REASON,
            "review_candidate_count": 0,
            "cue_count": len(cues),
            "confirmed_overlap_region_count": len(confirmed_overlap_regions),
            "rebuilt_cut_occurrence_count": rebuilt_cut_count,
            "combined_recomposition_occurrence_count": combined_count,
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
                "confirmed_overlap_region_count": len(confirmed_overlap_regions),
                "rebuilt_cut_occurrence_count": rebuilt_cut_count,
                "combined_recomposition_occurrence_count": combined_count,
                "segmentation_authority": _SEGMENTATION_AUTHORITY,
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
                "confirmed_overlap_region_count": len(confirmed_overlap_regions),
                "rebuilt_cut_occurrence_count": rebuilt_cut_count,
                "combined_recomposition_occurrence_count": combined_count,
                "source_run_stage": run_stage,
                "publish_ready": False,
                "segmentation_authority": _SEGMENTATION_AUTHORITY,
                "release_blocked_reason": _RELEASE_BLOCKED_REASON,
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
                "publish_ready": False,
                "segmentation_authority": _SEGMENTATION_AUTHORITY,
                "release_blocked_reason": _RELEASE_BLOCKED_REASON,
                "source_run_stage": run_stage,
                "confirmed_overlap_regions": len(confirmed_overlap_regions),
                "rebuilt_cut_occurrences": rebuilt_cut_count,
                "combined_recomposition_occurrences": combined_count,
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
