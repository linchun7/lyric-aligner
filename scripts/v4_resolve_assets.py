#!/usr/bin/env python3
"""Resolve canonical LRC/source audio into fingerprinted v4 TrackAssets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import AssetResolutionError, resolve_assets, write_assets_manifest
from lyric_aligner.config import calibration_overrides, load_profile
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest, sha256_file
from task_contract import assert_manifest_paths, load_task_manifest


def _load_json_map(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _load_role_overrides(path: Path | None) -> dict[str, dict[int, int]]:
    payload = _load_json_map(path)
    normalized: dict[str, dict[int, int]] = {}
    for track, values in payload.items():
        if not isinstance(values, dict):
            raise ValueError(f"lyric role overrides for {track!r} must be an object")
        normalized[str(track)] = {int(timestamp): int(index) for timestamp, index in values.items()}
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--song-list", required=True, type=Path)
    parser.add_argument("--lyrics-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--profile", type=Path, help="Complete v4 calibration profile JSON; omit for built-in bootstrap profile.")
    parser.add_argument("--language-map", type=Path)
    parser.add_argument("--middle-cut-map", type=Path)
    parser.add_argument("--lyric-role-map", type=Path)
    parser.add_argument("--min-score", type=float, help="Experimental override; release is blocked until moved into --profile.")
    parser.add_argument("--min-margin", type=float, help="Experimental override; release is blocked until moved into --profile.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        defaults = profile.asset_resolver
        min_score = defaults.min_score if args.min_score is None else args.min_score
        min_margin = defaults.min_margin if args.min_margin is None else args.min_margin
        overrides = calibration_overrides(defaults, {"min_score": min_score, "min_margin": min_margin})
        task = load_task_manifest(args.task_manifest)
        assert_manifest_paths(
            args.task_manifest,
            task,
            {"song_list": args.song_list, "lyrics_dir": args.lyrics_dir, "source_audio_dir": args.source_dir},
        )
        fingerprint = str(task["task_fingerprint_sha256"])
        middle_cut = {int(key): str(value) for key, value in _load_json_map(args.middle_cut_map).items()}
        payload = resolve_assets(
            song_list=args.song_list,
            lyrics_dir=args.lyrics_dir,
            source_audio_dir=args.source_dir,
            language_by_track={str(key): str(value) for key, value in _load_json_map(args.language_map).items()},
            middle_cut_by_occurrence=middle_cut,
            lyric_role_overrides_by_track=_load_role_overrides(args.lyric_role_map),
            min_score=min_score,
            min_margin=min_margin,
        )
        payload["algorithm_version"] = __version__
        payload["task_fingerprint_sha256"] = fingerprint
        payload["calibration_profile_version"] = profile.profile_version
        payload["calibration_profile_id"] = profile.profile_id
        payload["calibration_profile"] = profile.to_dict()
        payload["calibration_overrides"] = overrides
        write_assets_manifest(args.out, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="asset_resolution",
            algorithm_version=__version__,
            outputs=(("track_assets", args.out),),
            normalized_config={
                "calibration_profile_version": profile.profile_version,
                "calibration_profile_id": profile.profile_id,
                "calibration_overrides": overrides,
                "profile_file_sha256": sha256_file(args.profile) if args.profile else None,
                "min_score": min_score,
                "min_margin": min_margin,
                "language_map_sha256": sha256_file(args.language_map) if args.language_map else None,
                "middle_cut_map_sha256": sha256_file(args.middle_cut_map) if args.middle_cut_map else None,
                "lyric_role_map_sha256": sha256_file(args.lyric_role_map) if args.lyric_role_map else None,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            evidence={"asset_count": len(payload["assets"]), "occurrence_count": len(payload["occurrences"])},
        )
        atomic_write_json(args.artifact_out, artifact)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, AssetResolutionError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": payload["status"],
        "assets": len(payload["assets"]),
        "occurrences": len(payload["occurrences"]),
        "calibration_profile_id": profile.profile_id,
        "calibration_override": bool(overrides),
        "artifact_id": artifact["artifact_id"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
