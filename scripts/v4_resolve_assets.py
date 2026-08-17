#!/usr/bin/env python3
"""Resolve canonical LRC/source audio into explicit v4 TrackAssets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lyric_aligner.assets.resolver import AssetResolutionError, resolve_assets, write_assets_manifest


def _load_json_map(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-list", required=True, type=Path)
    parser.add_argument("--lyrics-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--language-map", type=Path)
    parser.add_argument("--middle-cut-map", type=Path)
    parser.add_argument("--min-score", type=float, default=0.76)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
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
        write_assets_manifest(args.out, payload)
    except (OSError, ValueError, json.JSONDecodeError, AssetResolutionError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": payload["status"],
                "assets": len(payload["assets"]),
                "occurrences": len(payload["occurrences"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
