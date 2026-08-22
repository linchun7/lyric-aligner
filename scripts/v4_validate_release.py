#!/usr/bin/env python3
"""Fail-closed v4 release guard for final SRT/audit/QA artifacts."""

from __future__ import annotations

import argparse
import json
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
from task_contract import load_task_manifest, verify_manifest_inputs


_REQUIRED_V4_SEGMENTATION_AUTHORITY = "editor_reconciled"


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
    """Require one exact final render with production-safe segmentation authority."""

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
    return str(payload["artifact_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
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
        validate_separate_artifact_paths(
            inputs=protected_inputs,
            outputs={"release_manifest": args.out_manifest},
        )

        upstream_ids, upstream_metadata = _load_upstream_artifacts(
            args.upstream_artifact, fingerprint=fingerprint
        )

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
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
