#!/usr/bin/env python3
"""Build Best-Safe 1.1 from a Smart timing floor plus independently authorized local evidence."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.pipeline.best_safe import BestSafeError, build_best_safe


def _canonical_paths(args: argparse.Namespace) -> tuple[list[Path], Path | None]:
    if args.canonical_lyrics:
        return [path.resolve() for path in args.canonical_lyrics], None
    assert args.lyrics_dir is not None
    lyrics_dir = args.lyrics_dir.resolve()
    pattern = re.compile(r"^\d{1,2}:\d{2}\s+(.+)$")
    names = []
    for line in args.song_list.resolve().read_text(encoding="utf-8-sig").splitlines():
        match = pattern.match(line.strip())
        if match:
            names.append(match.group(1).strip())
    if not names:
        raise BestSafeError("song list contains no parseable names for lyrics-dir resolution")
    return [lyrics_dir / f"{name}.lrc" for name in names], lyrics_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smart-srt", required=True, type=Path)
    parser.add_argument("--smart-report", required=True, type=Path)
    lyrics = parser.add_mutually_exclusive_group(required=True)
    lyrics.add_argument("--lyrics-dir", type=Path)
    lyrics.add_argument("--canonical-lyrics", nargs="+", type=Path)
    parser.add_argument("--max-srt", required=True, type=Path)
    parser.add_argument("--max-audit", required=True, type=Path)
    parser.add_argument("--semantic-sync", required=True, type=Path)
    parser.add_argument("--song-list", required=True, type=Path)
    parser.add_argument("--transition-authorization", required=True, type=Path)
    parser.add_argument("--text-adjudication", type=Path)
    parser.add_argument("--mask-profile", choices=("strong_profanity_v1", "none"), default="strong_profanity_v1")
    parser.add_argument("--display-overrides", type=Path)
    parser.add_argument("--timing-truth", type=Path)
    parser.add_argument("--timing-promotions", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--task-fingerprint")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    try:
        canonical, canonical_root = _canonical_paths(args)
        qa = build_best_safe(
            smart_srt=args.smart_srt.resolve(),
            smart_report=args.smart_report.resolve(),
            canonical_lyrics=canonical,
            canonical_lyrics_root=canonical_root,
            max_srt=args.max_srt.resolve(),
            max_audit=args.max_audit.resolve(),
            semantic_sync=args.semantic_sync.resolve(),
            song_list=args.song_list.resolve(),
            transition_authorization=args.transition_authorization.resolve(),
            text_adjudication=(args.text_adjudication.resolve() if args.text_adjudication else None),
            mask_profile=args.mask_profile,
            display_overrides=(args.display_overrides.resolve() if args.display_overrides else None),
            timing_truth=(args.timing_truth.resolve() if args.timing_truth else None),
            timing_promotions=(args.timing_promotions.resolve() if args.timing_promotions else None),
            out_srt=out_dir / "FINAL.srt",
            out_audit=out_dir / "FINAL.audit.csv",
            out_qa=out_dir / "QA.json",
            out_artifact=out_dir / "ARTIFACT.json",
            expected_task_fingerprint=args.task_fingerprint,
        )
    except (OSError, ValueError, BestSafeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
        return 2
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "cue_count": qa["cue_count"],
                "max_candidate_tracks": qa["max_candidate_track_ordinals"],
                "semantic_passed_tracks": qa["semantic_passed_track_ordinals"],
                "semantic_blocked_tracks": qa["semantic_blocked_track_ordinals"],
                "timing_changed_boundary_count": qa["timing_floor"]["timing_changed_boundary_count"],
                "unsupported_timing_change_count": qa["timing_floor"]["unsupported_timing_change_count"],
                "timing_truth_failure_count": qa["timing_floor"]["truth_failure_count"],
                "publish_ready": qa["publish_ready"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
