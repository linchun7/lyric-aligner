#!/usr/bin/env python3
"""Fail-closed v4 release guard for final SRT/audit/QA artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.qa.final_integrity import FinalIntegrityError, build_release_artifact_manifest
from lyric_aligner.qa.source_clock_authority import (
    SOURCE_CLOCK_SEMANTIC_AUTHORITY_POLICY_ID,
    build_verified_source_clock_authority,
)
from lyric_aligner.qa.semantic_sync import (
    DEFAULT_LARGE_ERROR_MS,
    DEFAULT_MAX_LARGE_ERROR_FRACTION,
    DEFAULT_MAX_MEDIAN_ABS_ERROR_MS,
    DEFAULT_MIN_AUDIO_ANCHOR_FRACTION,
)
from task_contract import (
    load_task_manifest,
    resolve_manifest_record,
    sha256,
    verify_manifest_inputs,
)


_REQUIRED_V4_SEGMENTATION_AUTHORITY = "editor_reconciled"
_SEMANTIC_SYNC_SCHEMA_VERSION = "semantic-sync-qa-1.1"
_SEMANTIC_SYNC_SOURCE_CLOCK_SCHEMA_VERSION = "semantic-sync-qa-1.2"
_SEMANTIC_SYNC_MODE = "independent_audio_semantic_release_gate"
_SEMANTIC_AUDIO_POLICY = "forced_alignment_or_asr_plus_reliable_editor_v1"
_SEMANTIC_AUDIO_SOURCE_CLOCK_POLICY = (
    "verified_source_clock_or_forced_alignment_or_asr_plus_reliable_editor_v2"
)
_VERIFIED_SOURCE_CLOCK_BASIS = "verified_source_clock_final_mix_holdout"
_VERIFIED_SOURCE_CLOCK_POLICY_ID = SOURCE_CLOCK_SEMANTIC_AUTHORITY_POLICY_ID


def _resolve_run_timeline_path(value: object) -> Path:
    path = Path(str(value or ""))
    if not str(path):
        raise ValueError("semantic source-clock run occurrence lacks timeline_path")
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _semantic_sync_required(algorithm_version: str) -> bool:
    """Require semantic timing evidence from a17 onward and for later stable v4."""

    if not algorithm_version.startswith("4."):
        return False
    alpha = re.fullmatch(r"4\.0\.0a(\d+)", algorithm_version)
    if alpha is not None:
        return int(alpha.group(1)) >= 17
    return True


def _validate_semantic_sync_layer(
    layer: object,
    *,
    label: str,
    allowed_evidence_bases: tuple[str, ...] = ("forced_alignment", "asr_plus_reliable_editor"),
) -> dict:
    if not isinstance(layer, dict) or layer.get("passed") is not True:
        raise ValueError(f"release blocked: {label} semantic sync did not pass")
    track_count = layer.get("track_count")
    failed_count = layer.get("failed_track_count")
    if (
        not isinstance(track_count, int)
        or isinstance(track_count, bool)
        or track_count < 1
        or not isinstance(failed_count, int)
        or isinstance(failed_count, bool)
        or failed_count != 0
    ):
        raise ValueError(f"semantic sync {label} track counts are invalid")
    errors = layer.get("errors")
    tracks = layer.get("tracks")
    if errors != [] or not isinstance(tracks, list) or len(tracks) != track_count:
        raise ValueError(f"semantic sync {label} track evidence is invalid")
    for row in tracks:
        if (
            not isinstance(row, dict)
            or row.get("passed") is not True
            or row.get("errors") != []
            or row.get("evidence_basis") not in allowed_evidence_bases
        ):
            raise ValueError(f"semantic sync {label} contains a failed track")
    expected_thresholds = {
        "max_median_abs_error_ms": DEFAULT_MAX_MEDIAN_ABS_ERROR_MS,
        "large_error_ms": DEFAULT_LARGE_ERROR_MS,
        "max_large_error_fraction": DEFAULT_MAX_LARGE_ERROR_FRACTION,
        "min_audio_anchor_fraction": DEFAULT_MIN_AUDIO_ANCHOR_FRACTION,
    }
    if layer.get("thresholds") != expected_thresholds:
        raise ValueError(f"semantic sync {label} thresholds differ from release policy")
    return layer


def _validate_semantic_sync_qa(
    path: Path,
    *,
    run_path: Path,
    final_srt: Path,
    manifest_path: Path,
    manifest: dict,
    algorithm_version: str,
    fusion_path: Path,
    final_report: Path,
    source_clock_map_path: Path | None = None,
    source_clock_promotion_analysis_path: Path | None = None,
    source_clock_promotion_selection_path: Path | None = None,
    source_clock_promotion_protocol_path: Path | None = None,
) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("semantic sync QA must contain a JSON object")
    fingerprint = str(manifest["task_fingerprint_sha256"])
    schema = str(payload.get("schema_version") or "")
    policy = str(payload.get("audio_evidence_policy") or "")
    valid_contracts = {
        (_SEMANTIC_SYNC_SCHEMA_VERSION, _SEMANTIC_AUDIO_POLICY),
        (_SEMANTIC_SYNC_SOURCE_CLOCK_SCHEMA_VERSION, _SEMANTIC_AUDIO_SOURCE_CLOCK_POLICY),
    }
    if (schema, policy) not in valid_contracts:
        raise ValueError("semantic sync QA schema/policy contract mismatch")
    if str(payload.get("algorithm_version") or "") != algorithm_version:
        raise ValueError("semantic sync QA algorithm_version mismatch")
    if str(payload.get("task_fingerprint_sha256") or "") != fingerprint:
        raise ValueError("semantic sync QA belongs to another task")
    if str(payload.get("mode") or "") != _SEMANTIC_SYNC_MODE:
        raise ValueError("semantic sync QA mode mismatch")
    if not isinstance(payload.get("editor_witness"), dict) or payload["editor_witness"].get("authority") != "auxiliary_only":
        raise ValueError("semantic sync QA editor_witness must be auxiliary_only")
    if payload.get("passed") is not True:
        raise ValueError("release blocked: semantic sync QA did not pass")
    allowed_bases = ("forced_alignment", "asr_plus_reliable_editor")
    if policy == _SEMANTIC_AUDIO_SOURCE_CLOCK_POLICY:
        allowed_bases = allowed_bases + (_VERIFIED_SOURCE_CLOCK_BASIS,)
    projection = _validate_semantic_sync_layer(
        payload.get("projection_sync"),
        label="canonical projection",
        allowed_evidence_bases=allowed_bases,
    )
    final = _validate_semantic_sync_layer(
        payload.get("final_sync"),
        label="final subtitle",
        allowed_evidence_bases=allowed_bases,
    )
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("semantic sync QA has invalid bindings")
    inputs = manifest["inputs"]
    source_srt = resolve_manifest_record(manifest_path, inputs["source_srt"])
    audio = resolve_manifest_record(manifest_path, inputs["audio"])
    song_list = resolve_manifest_record(manifest_path, inputs["song_list"])
    expected = {
        "source_srt_sha256": sha256(source_srt),
        "audio_sha256": sha256(audio),
        "song_list_sha256": sha256(song_list),
        "run_sha256": sha256(run_path),
        "final_srt_sha256": sha256(final_srt),
        "fusion_sha256": sha256(fusion_path),
        "final_report_sha256": sha256(final_report),
    }
    source_clock_paths = (
        source_clock_map_path,
        source_clock_promotion_analysis_path,
        source_clock_promotion_selection_path,
        source_clock_promotion_protocol_path,
    )
    if policy == _SEMANTIC_AUDIO_POLICY and any(path is not None for path in source_clock_paths):
        raise ValueError("legacy semantic QA must not receive source-clock authority inputs")
    if policy == _SEMANTIC_AUDIO_SOURCE_CLOCK_POLICY:
        if not all(path is not None for path in source_clock_paths):
            raise ValueError(
                "source-clock semantic release requires source clock map, promotion analysis, promotion selection and promotion protocol"
            )
        expected["source_clock_map_sha256"] = sha256(source_clock_map_path)
        expected["source_clock_promotion_analysis_sha256"] = sha256(
            source_clock_promotion_analysis_path
        )
        expected["source_clock_promotion_selection_sha256"] = sha256(
            source_clock_promotion_selection_path
        )
        expected["source_clock_promotion_protocol_sha256"] = sha256(
            source_clock_promotion_protocol_path
        )
    for key, digest in expected.items():
        if str(bindings.get(key) or "") != digest:
            raise ValueError(f"semantic sync QA binding mismatch: {key}")
    if policy == _SEMANTIC_AUDIO_SOURCE_CLOCK_POLICY:
        authority = payload.get("verified_source_clock_authority")
        if not isinstance(authority, dict):
            raise ValueError("source-clock semantic QA lacks authority metadata")
        ordinals = authority.get("ordinals")
        if (
            not isinstance(ordinals, list)
            or not ordinals
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in ordinals
            )
            or len(ordinals) != len(set(ordinals))
            or authority.get("track_count") != len(ordinals)
            or authority.get("policy_ids") != [_VERIFIED_SOURCE_CLOCK_POLICY_ID]
        ):
            raise ValueError("source-clock semantic QA authority metadata is invalid")
        expected_ordinals = set(ordinals)
        run_payload = json.loads(run_path.read_text(encoding="utf-8-sig"))
        source_clock_map_payload = json.loads(
            source_clock_map_path.read_text(encoding="utf-8-sig")
        )
        promotion_payload = json.loads(
            source_clock_promotion_analysis_path.read_text(encoding="utf-8-sig")
        )
        promotion_selection_payload = json.loads(
            source_clock_promotion_selection_path.read_text(encoding="utf-8-sig")
        )
        promotion_protocol_payload = json.loads(
            source_clock_promotion_protocol_path.read_text(encoding="utf-8-sig")
        )
        if not all(
            isinstance(value, dict)
            for value in (
                run_payload,
                source_clock_map_payload,
                promotion_payload,
                promotion_selection_payload,
                promotion_protocol_payload,
            )
        ):
            raise ValueError("source-clock semantic release inputs must be JSON objects")
        timelines_by_ordinal = {
            int(row["ordinal"]): json.loads(
                _resolve_run_timeline_path(row.get("timeline_path")).read_text(
                    encoding="utf-8-sig"
                )
            )
            for row in run_payload.get("occurrences", [])
            if isinstance(row, dict) and row.get("ordinal") is not None
        }
        replayed_authority = build_verified_source_clock_authority(
            source_clock_map=source_clock_map_payload,
            promotion_analysis=promotion_payload,
            promotion_selection=promotion_selection_payload,
            promotion_protocol=promotion_protocol_payload,
            run=run_payload,
            timelines_by_ordinal=timelines_by_ordinal,
            source_clock_map_sha256=sha256(source_clock_map_path),
            promotion_analysis_sha256=sha256(source_clock_promotion_analysis_path),
            promotion_selection_sha256=sha256(source_clock_promotion_selection_path),
            promotion_protocol_sha256=sha256(source_clock_promotion_protocol_path),
        )
        if set(replayed_authority) != expected_ordinals:
            raise ValueError("source-clock semantic QA authority differs from replayed evidence")
        for label, layer in (("canonical projection", projection), ("final subtitle", final)):
            verified_rows = {
                int(row["ordinal"])
                for row in layer["tracks"]
                if row.get("evidence_basis") == _VERIFIED_SOURCE_CLOCK_BASIS
                and row.get("verified_source_clock_authority") is True
                and row.get("authority_policy_id") == _VERIFIED_SOURCE_CLOCK_POLICY_ID
                and int(row.get("authority_holdout_anchor_count") or 0) >= 3
            }
            for row in layer["tracks"]:
                ordinal = int(row["ordinal"])
                if ordinal not in verified_rows:
                    continue
                replayed = replayed_authority.get(ordinal)
                if replayed is None:
                    raise ValueError(
                        f"source-clock semantic QA {label} declares un-replayed authority"
                    )
                if row.get("authority_timeline_map_binding") != replayed.get(
                    "timeline_map_binding"
                ):
                    raise ValueError(
                        f"source-clock semantic QA {label} timeline-map binding mismatch"
                    )
            if verified_rows != expected_ordinals:
                raise ValueError(
                    f"source-clock semantic QA {label} authority coverage mismatch"
                )
    return payload


def _load_upstream_artifacts(paths: list[Path], *, fingerprint: str) -> tuple[tuple[str, ...], dict]:
    ids: list[str] = []
    profile_ids: set[str] = set()
    profile_versions: set[str] = set()
    algorithm_versions: set[str] = set()
    stages: list[str] = []
    unprofiled_overrides: list[dict] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"upstream artifact {path} must contain a JSON object")
        issues = validate_upstream_artifact(payload, expected_task_fingerprint=fingerprint)
        if issues:
            raise ValueError(f"invalid upstream artifact {path}: " + "; ".join(issues))
        ids.append(str(payload["artifact_id"]))
        stage = str(payload.get("stage", ""))
        stages.append(stage)
        version = str(payload.get("algorithm_version") or "").strip()
        if version:
            algorithm_versions.add(version)
        config = payload.get("normalized_config", {})
        if not isinstance(config, dict):
            raise ValueError(f"upstream artifact {path} has invalid normalized_config")
        profile_id = str(config.get("calibration_profile_id") or "").strip()
        profile_version = str(config.get("calibration_profile_version") or "").strip()
        if profile_id:
            profile_ids.add(profile_id)
        if profile_version:
            profile_versions.add(profile_version)
        overrides = config.get("calibration_overrides") or {}
        if overrides:
            unprofiled_overrides.append({"stage": stage, "overrides": overrides})
    if len(algorithm_versions) > 1:
        raise ValueError("release upstream artifacts use different v4 algorithm_version values")
    if len(profile_ids) > 1:
        raise ValueError("release upstream artifacts use different calibration_profile_id values")
    if len(profile_versions) > 1:
        raise ValueError("release upstream artifacts use different calibration_profile_version values")
    if unprofiled_overrides:
        raise ValueError(
            "release blocked: calibration CLI overrides are not a named profile; "
            "move them into a complete v4 profile and rerun all stages"
        )
    return tuple(ids), {
        "v4_upstream_algorithm_version": next(iter(algorithm_versions), ""),
        "calibration_profile_id": next(iter(profile_ids), ""),
        "calibration_profile_version": next(iter(profile_versions), ""),
        "upstream_stages": stages,
        "calibration_overrides": {},
    }


def _validate_final_render_binding(
    paths: list[Path],
    *,
    fingerprint: str,
    algorithm_version: str,
    final_srt: Path,
    report: Path,
    qa_json: Path,
) -> str:
    """Require one exact final render with internally consistent production authority."""

    matches: list[tuple[Path, dict]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"upstream artifact {path} must contain a JSON object")
        if payload.get("stage") == "final_render":
            matches.append((path, payload))
    if len(matches) != 1:
        raise ValueError(
            "v4 release requires exactly one final_render upstream artifact"
        )

    artifact_path, payload = matches[0]
    issues = validate_upstream_artifact(
        payload,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=algorithm_version,
        expected_stage="final_render",
    )
    issues.extend(validate_artifact_output(payload, role="final_srt", path=final_srt))
    issues.extend(validate_artifact_output(payload, role="audit_csv", path=report))
    issues.extend(validate_artifact_output(payload, role="qa_json", path=qa_json))
    if issues:
        raise ValueError(
            f"final_render artifact {artifact_path} does not bind current final files: "
            + "; ".join(issues)
        )

    config = payload.get("normalized_config")
    if not isinstance(config, dict):
        raise ValueError("final_render artifact has invalid normalized_config")
    segmentation_authority = str(config.get("segmentation_authority") or "").strip()
    if segmentation_authority != _REQUIRED_V4_SEGMENTATION_AUTHORITY:
        raise ValueError(
            "v4 release blocked: final render has no editor-reconciled segmentation "
            "authority; canonical-line rendering is evaluation-only"
        )

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("final_render artifact has invalid evidence")
    if str(evidence.get("segmentation_authority") or "").strip() != (
        _REQUIRED_V4_SEGMENTATION_AUTHORITY
    ):
        raise ValueError(
            "v4 release blocked: final_render artifact evidence does not confirm "
            "editor-reconciled segmentation authority"
        )
    if evidence.get("publish_ready") is not True:
        raise ValueError(
            "v4 release blocked: final_render artifact evidence is not publish_ready"
        )
    if str(evidence.get("release_blocked_reason") or "").strip():
        raise ValueError(
            "v4 release blocked: final_render artifact evidence still records a "
            "release_blocked_reason"
        )

    qa = json.loads(qa_json.read_text(encoding="utf-8-sig"))
    if not isinstance(qa, dict):
        raise ValueError("final render QA must contain a JSON object")
    if str(qa.get("segmentation_authority") or "").strip() != (
        _REQUIRED_V4_SEGMENTATION_AUTHORITY
    ):
        raise ValueError(
            "v4 release blocked: final render QA does not confirm editor-reconciled "
            "segmentation authority"
        )
    if qa.get("publish_ready") is not True:
        raise ValueError("v4 release blocked: final render QA is not publish_ready")
    if str(qa.get("release_blocked_reason") or "").strip():
        raise ValueError(
            "v4 release blocked: final render QA still records a release_blocked_reason"
        )
    return str(payload["artifact_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--semantic-sync-qa", type=Path)
    parser.add_argument("--semantic-sync-fusion", type=Path)
    parser.add_argument("--semantic-source-clock-map", type=Path)
    parser.add_argument("--semantic-source-clock-promotion-analysis", type=Path)
    parser.add_argument("--semantic-source-clock-promotion-selection", type=Path)
    parser.add_argument("--semantic-source-clock-promotion-protocol", type=Path)
    parser.add_argument("--algorithm-version", required=True)
    parser.add_argument(
        "--upstream-artifact",
        type=Path,
        action="append",
        default=[],
        help="Repeat to bind the release to production stage artifacts. v4 requires final_render.",
    )
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--out-manifest", required=True, type=Path)
    args = parser.parse_args()

    try:
        semantic_source_clock_inputs = (
            args.semantic_source_clock_map,
            args.semantic_source_clock_promotion_analysis,
            args.semantic_source_clock_promotion_selection,
            args.semantic_source_clock_promotion_protocol,
        )
        if any(value is not None for value in semantic_source_clock_inputs) and not all(value is not None for value in semantic_source_clock_inputs):
            raise ValueError(
                "semantic source-clock authority requires map, promotion analysis, promotion selection and promotion protocol together"
            )
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
        fingerprint = str(task["task_fingerprint_sha256"])

        protected_inputs = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected_inputs.update(
            {
                "final_srt": args.final_srt,
                "audit_csv": args.report,
                "qa_json": args.qa_json,
                **{
                    f"upstream_artifact_{index}": path
                    for index, path in enumerate(args.upstream_artifact)
                },
            }
        )
        if args.run is not None:
            protected_inputs["run"] = args.run
        if args.semantic_sync_qa is not None:
            protected_inputs["semantic_sync_qa"] = args.semantic_sync_qa
        if args.semantic_sync_fusion is not None:
            protected_inputs["semantic_sync_fusion"] = args.semantic_sync_fusion
        if args.semantic_source_clock_map is not None:
            protected_inputs["semantic_source_clock_map"] = args.semantic_source_clock_map
        if args.semantic_source_clock_promotion_analysis is not None:
            protected_inputs["semantic_source_clock_promotion_analysis"] = (
                args.semantic_source_clock_promotion_analysis
            )
        if args.semantic_source_clock_promotion_selection is not None:
            protected_inputs["semantic_source_clock_promotion_selection"] = (
                args.semantic_source_clock_promotion_selection
            )
        if args.semantic_source_clock_promotion_protocol is not None:
            protected_inputs["semantic_source_clock_promotion_protocol"] = (
                args.semantic_source_clock_promotion_protocol
            )
        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={"release_manifest": args.out_manifest},
        )

        upstream_ids, upstream_metadata = _load_upstream_artifacts(
            args.upstream_artifact, fingerprint=fingerprint
        )
        semantic_sync_payload = None

        if args.algorithm_version.startswith("4."):
            upstream_version = upstream_metadata["v4_upstream_algorithm_version"]
            if not upstream_ids:
                raise ValueError("v4 release requires at least one upstream artifact")
            if upstream_version != args.algorithm_version:
                raise ValueError(
                    "release algorithm version differs from upstream artifacts: "
                    f"release={args.algorithm_version}, upstream={upstream_version or '<missing>'}"
                )
            if "final_render" not in upstream_metadata["upstream_stages"]:
                raise ValueError("v4 release requires a final_render upstream artifact")
            profile_id = upstream_metadata["calibration_profile_id"]
            profile_version = upstream_metadata["calibration_profile_version"]
            if not profile_id or not profile_version:
                raise ValueError("v4 release upstream is missing calibration profile identity")
            final_render_artifact_id = _validate_final_render_binding(
                args.upstream_artifact,
                fingerprint=fingerprint,
                algorithm_version=args.algorithm_version,
                final_srt=args.final_srt,
                report=args.report,
                qa_json=args.qa_json,
            )
            semantic_requested = (
                args.run is not None
                or args.semantic_sync_qa is not None
                or args.semantic_sync_fusion is not None
                or args.semantic_source_clock_map is not None
                or args.semantic_source_clock_promotion_analysis is not None
                or args.semantic_source_clock_promotion_selection is not None
                or args.semantic_source_clock_promotion_protocol is not None
            )
            if _semantic_sync_required(args.algorithm_version) or semantic_requested:
                if args.run is None or args.semantic_sync_qa is None or args.semantic_sync_fusion is None:
                    raise ValueError(
                        "v4 release requires --run, --semantic-sync-qa and --semantic-sync-fusion from a17 onward"
                    )
                semantic_sync_payload = _validate_semantic_sync_qa(
                    args.semantic_sync_qa,
                    run_path=args.run,
                    final_srt=args.final_srt,
                    manifest_path=args.task_manifest,
                    manifest=task,
                    algorithm_version=args.algorithm_version,
                    fusion_path=args.semantic_sync_fusion,
                    final_report=args.report,
                    source_clock_map_path=args.semantic_source_clock_map,
                    source_clock_promotion_analysis_path=(
                        args.semantic_source_clock_promotion_analysis
                    ),
                    source_clock_promotion_selection_path=(
                        args.semantic_source_clock_promotion_selection
                    ),
                    source_clock_promotion_protocol_path=(
                        args.semantic_source_clock_promotion_protocol
                    ),
                )
        else:
            profile_id = upstream_metadata["calibration_profile_id"] or None
            profile_version = upstream_metadata["calibration_profile_version"] or None
            final_render_artifact_id = ""

        manifest = build_release_artifact_manifest(
            final_srt=args.final_srt,
            audit_csv=args.report,
            qa_json=args.qa_json,
            task_fingerprint_sha256=fingerprint,
            algorithm_version=args.algorithm_version,
            git_commit=args.git_commit,
            normalized_config={
                **upstream_metadata,
                "final_render_artifact_id": final_render_artifact_id,
                "semantic_sync_verified": semantic_sync_payload is not None,
                "semantic_sync_qa_sha256": (
                    sha256(args.semantic_sync_qa) if semantic_sync_payload is not None else ""
                ),
                "semantic_sync_run_sha256": (
                    sha256(args.run) if semantic_sync_payload is not None else ""
                ),
                "semantic_sync_fusion_sha256": (
                    sha256(args.semantic_sync_fusion) if semantic_sync_payload is not None else ""
                ),
                "semantic_source_clock_map_sha256": (
                    sha256(args.semantic_source_clock_map)
                    if semantic_sync_payload is not None
                    and args.semantic_source_clock_map is not None
                    else ""
                ),
                "semantic_source_clock_promotion_analysis_sha256": (
                    sha256(args.semantic_source_clock_promotion_analysis)
                    if semantic_sync_payload is not None
                    and args.semantic_source_clock_promotion_analysis is not None
                    else ""
                ),
                "semantic_source_clock_promotion_selection_sha256": (
                    sha256(args.semantic_source_clock_promotion_selection)
                    if semantic_sync_payload is not None
                    and args.semantic_source_clock_promotion_selection is not None
                    else ""
                ),
                "semantic_source_clock_promotion_protocol_sha256": (
                    sha256(args.semantic_source_clock_promotion_protocol)
                    if semantic_sync_payload is not None
                    and args.semantic_source_clock_promotion_protocol is not None
                    else ""
                ),
            },
            upstream_artifact_ids=upstream_ids,
            expected_calibration_profile_id=profile_id,
            expected_calibration_profile_version=profile_version,
        )
        atomic_write_json(args.out_manifest, manifest)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, FinalIntegrityError) as exc:
        parser.error(str(exc))

    print(json.dumps({
        "artifact_id": manifest["artifact_id"],
        "release_status": "ready",
        "upstream_artifact_count": len(upstream_ids),
        "v4_upstream_algorithm_version": upstream_metadata["v4_upstream_algorithm_version"],
        "calibration_profile_id": upstream_metadata["calibration_profile_id"],
        "final_render_artifact_id": final_render_artifact_id,
        "semantic_sync_verified": semantic_sync_payload is not None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
