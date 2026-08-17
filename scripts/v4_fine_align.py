#!/usr/bin/env python3
"""Selectively refine one fingerprinted v4 coarse Source-to-Mix alignment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import librosa

from lyric_aligner import __version__
from lyric_aligner.audio.fine_alignment import refine_coarse_mapping
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    sha256_file,
    validate_artifact_output,
    validate_upstream_artifact,
)
from task_contract import assert_manifest_paths, load_task_manifest, resolve_manifest_record


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_stage(
    path: Path,
    artifact_path: Path,
    *,
    fingerprint: str,
    role: str,
    stage: str,
):
    payload = _load(path)
    if payload.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError(f"{role} task fingerprint mismatch")
    if payload.get("algorithm_version") != __version__:
        raise ValueError(f"{role} algorithm version mismatch")
    artifact = _load(artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=path))
    if issues:
        raise ValueError(f"invalid {role} artifact: " + "; ".join(issues))
    return payload, artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--mix-audio", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--coarse", required=True, type=Path)
    parser.add_argument("--coarse-artifact", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sr", type=int, default=16000)
    parser.add_argument("--hop-length", type=int, default=256)
    parser.add_argument("--source-radius-seconds", type=float, default=1.25)
    parser.add_argument("--slope-radius", type=float, default=0.08)
    parser.add_argument("--slope-step", type=float, default=0.02)
    parser.add_argument("--candidate-step-seconds", type=float, default=0.05)
    parser.add_argument("--min-score", type=float, default=0.62)
    parser.add_argument("--min-margin", type=float, default=0.012)
    parser.add_argument("--bpm-prior", type=float)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        assert_manifest_paths(args.task_manifest, task, {"audio": args.mix_audio})
        fingerprint = str(task["task_fingerprint_sha256"])
        track_assets, asset_artifact = _validate_stage(
            args.track_assets,
            args.asset_artifact,
            fingerprint=fingerprint,
            role="track_assets",
            stage="asset_resolution",
        )
        coarse, coarse_artifact = _validate_stage(
            args.coarse,
            args.coarse_artifact,
            fingerprint=fingerprint,
            role="coarse_alignment",
            stage="coarse_audio_alignment",
        )
        if str(coarse.get("upstream_asset_artifact_id")) != str(asset_artifact["artifact_id"]):
            raise ValueError("coarse alignment came from a different asset artifact")
        occurrence_id = str(coarse["occurrence_id"])
        occurrence = next(
            (
                item
                for item in track_assets.get("occurrences", [])
                if str(item.get("occurrence_id")) == occurrence_id
            ),
            None,
        )
        if occurrence is None:
            raise ValueError("coarse occurrence missing from track_assets")
        asset = next(
            (
                item
                for item in track_assets.get("assets", [])
                if str(item.get("track_id")) == str(occurrence["track_id"])
            ),
            None,
        )
        if asset is None:
            raise ValueError("TrackAsset missing for coarse occurrence")
        source_path = Path(str(asset["source_audio_path"]))
        if sha256_file(source_path) != str(asset["source_audio_sha256"]):
            raise ValueError("selected source audio hash differs from TrackAsset")
        source_dir_record = task["inputs"].get("source_audio_dir")
        if source_dir_record is not None:
            source_dir = resolve_manifest_record(args.task_manifest, source_dir_record).resolve()
            try:
                source_path.resolve().relative_to(source_dir)
            except ValueError as exc:
                raise ValueError("TrackAsset source audio is outside task source_audio_dir") from exc

        mix_audio, _ = librosa.load(args.mix_audio, sr=args.sr, mono=True)
        source_audio, _ = librosa.load(source_path, sr=args.sr, mono=True)
        fine = refine_coarse_mapping(
            mix_audio,
            source_audio,
            coarse,
            sr=args.sr,
            force=args.force,
            hop_length=args.hop_length,
            source_radius_seconds=args.source_radius_seconds,
            slope_radius=args.slope_radius,
            slope_step=args.slope_step,
            candidate_step_seconds=args.candidate_step_seconds,
            min_score=args.min_score,
            min_margin=args.min_margin,
            bpm_prior=args.bpm_prior,
            middle_cut=str(occurrence.get("middle_cut", "false")),
        )
        payload = {
            "schema_version": "1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "occurrence_id": occurrence_id,
            "track_id": occurrence["track_id"],
            "mix_audio_sha256": sha256_file(args.mix_audio),
            "source_audio_sha256": sha256_file(source_path),
            "upstream_asset_artifact_id": asset_artifact["artifact_id"],
            "upstream_coarse_artifact_id": coarse_artifact["artifact_id"],
            "result": fine,
        }
        atomic_write_json(args.out, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="fine_audio_alignment",
            algorithm_version=__version__,
            outputs=(("fine_alignment", args.out),),
            normalized_config={
                "force": args.force,
                "sr": args.sr,
                "hop_length": args.hop_length,
                "source_radius_seconds": args.source_radius_seconds,
                "slope_radius": args.slope_radius,
                "slope_step": args.slope_step,
                "candidate_step_seconds": args.candidate_step_seconds,
                "min_score": args.min_score,
                "min_margin": args.min_margin,
                "bpm_prior": args.bpm_prior,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(
                str(asset_artifact["artifact_id"]),
                str(coarse_artifact["artifact_id"]),
            ),
            evidence={
                "occurrence_id": occurrence_id,
                "applied": fine["applied"],
                "status": fine["status"],
                "blocked": bool(fine.get("timewarp", {}).get("blocked", False)),
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "occurrence_id": occurrence_id,
                "applied": fine["applied"],
                "status": fine["status"],
                "artifact_id": artifact["artifact_id"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
