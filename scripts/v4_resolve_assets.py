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
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest
from task_contract import assert_manifest_paths, load_task_manifest


def _load_json_map(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--song-list", required=True, type=Path)
    parser.add_argument("--lyrics-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--language-map", type=Path)
    parser.add_argument("--middle-cut-map", type=Path)
    parser.add_argument("--min-score", type=float, default=0.76)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()
    try:
        task = load_task_manifest(args.task_manifest)
        assert_manifest_paths(
            args.task_manifest,
            task,
            {
                "song_list": args.song_list,
                "lyrics_dir": args.lyrics_dir,
                "source_audio_dir": args.source_dir,
            },
        )
        fingerprint = str(task["task_fingerprint_sha256"])
        middle_cut = {
            int(key): str(value)
            for key, value in _load_json_map(args.middle_cut_map).items()
        }
        payload = resolve_assets(
            song_list=args.song_list,
            lyrics_dir=args.lyrics_dir,
            source_audio_dir=args.source_dir,
            language_by_track={
                str(key): str(value)
                for key, value in _load_json_map(args.language_map).items()
            },
            middle_cut_by_occurrence=middle_cut,
            min_score=args.min_score,
            min_margin=args.min_margin,
        )
        payload["algorithm_version"] = __version__
        payload["task_fingerprint_sha256"] = fingerprint
        write_assets_manifest(args.out, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="asset_resolution",
            algorithm_version=__version__,
            outputs=(("track_assets", args.out),),
            normalized_config={
                "min_score": args.min_score,
                "min_margin": args.min_margin,
                "language_map": bool(args.language_map),
                "middle_cut_map": bool(args.middle_cut_map),
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            evidence={
                "asset_count": len(payload["assets"]),
                "occurrence_count": len(payload["occurrences"]),
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, AssetResolutionError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": payload["status"],
                "assets": len(payload["assets"]),
                "occurrences": len(payload["occurrences"]),
                "artifact_id": artifact["artifact_id"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
