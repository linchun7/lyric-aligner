#!/usr/bin/env python3
"""Materialize candidate-blind audio clips and HTML for a frozen timing decision pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.boundary_promotion_shadow import verify_boundary_promotion_selection
from lyric_aligner.evaluation.timing_decision_review import (
    REVIEW_PARTITIONS,
    build_review_manifest,
    render_review_html,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _validate_boundary_promotion_binding_args(
    selection_path: Path | None,
    partition: str | None,
) -> None:
    if (selection_path is None) != (partition is None):
        raise ValueError("boundary promotion selection and partition must be provided together")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--final-mix", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--boundary-promotion-selection",
        type=Path,
        help="optional pre-gold P1 selection to bind into the blind review manifest",
    )
    parser.add_argument(
        "--boundary-promotion-partition",
        choices=sorted(REVIEW_PARTITIONS),
        help="pre-gold intended partition; required together with --boundary-promotion-selection",
    )
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--sample-rate", type=int, default=16000)
    args = parser.parse_args()
    try:
        if not args.pack.is_file() or not args.final_mix.is_file():
            raise ValueError("pack and final mix must exist")
        if args.out_dir.exists() and any(args.out_dir.iterdir()):
            raise ValueError("review output directory must not already contain files")
        if args.sample_rate < 8000 or args.sample_rate > 48000:
            raise ValueError("sample-rate must be between 8000 and 48000")
        pack = json.loads(args.pack.read_text(encoding="utf-8-sig"))
        if not isinstance(pack, dict):
            raise ValueError("pack must contain one JSON object")
        _validate_boundary_promotion_binding_args(
            args.boundary_promotion_selection,
            args.boundary_promotion_partition,
        )
        boundary_promotion_selection_sha256 = None
        if args.boundary_promotion_selection is not None:
            if not args.boundary_promotion_selection.is_file():
                raise ValueError("boundary promotion selection must exist")
            selection = json.loads(args.boundary_promotion_selection.read_text(encoding="utf-8-sig"))
            if not isinstance(selection, dict):
                raise ValueError("boundary promotion selection must contain one JSON object")
            boundary_promotion_selection_sha256 = verify_boundary_promotion_selection(pack, selection)
        mix_sha = _sha256(args.final_mix)
        if pack.get("final_mix_sha256") != mix_sha:
            raise ValueError("review final mix SHA differs from frozen decision pack")
        manifest = build_review_manifest(
            pack,
            boundary_promotion_selection_sha256=boundary_promotion_selection_sha256,
            boundary_promotion_partition=args.boundary_promotion_partition,
        )
        args.out_dir.mkdir(parents=True, exist_ok=True)
        clips_dir = args.out_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        clip_receipts: list[dict[str, object]] = []
        for case in manifest["cases"]:
            clip_path = args.out_dir / str(case["clip_file"])
            start_ms = int(case["clip_start_ms"])
            duration_ms = int(case["clip_duration_ms"])
            cmd = [
                args.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{start_ms / 1000:.3f}",
                "-t",
                f"{duration_ms / 1000:.3f}",
                "-i",
                str(args.final_mix),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(args.sample_rate),
                "-c:a",
                "pcm_s16le",
                str(clip_path),
            ]
            completed = subprocess.run(
                cmd,
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
            if completed.returncode != 0 or not clip_path.is_file() or clip_path.stat().st_size <= 44:
                raise ValueError(
                    f"ffmpeg clip build failed for {case['id']}: "
                    f"{completed.stderr[-1000:]}"
                )
            clip_receipts.append(
                {
                    "id": case["id"],
                    "file": case["clip_file"],
                    "size": clip_path.stat().st_size,
                    "sha256": _sha256(clip_path),
                }
            )
        _atomic_text(args.out_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        _atomic_text(args.out_dir / "index.html", render_review_html(manifest))
        receipt = {
            "schema_version": "timing-decision-review-materialization-1.0",
            "selection_lock_sha256": manifest["selection_lock_sha256"],
            "review_manifest_sha256": manifest["manifest_sha256"],
            "final_mix_sha256": mix_sha,
            "pack_sha256": _sha256(args.pack),
            "case_count": len(manifest["cases"]),
            "candidate_positions_hidden": True,
            "sample_rate": args.sample_rate,
            "clips": clip_receipts,
        }
        if boundary_promotion_selection_sha256 is not None:
            receipt["boundary_promotion_selection_sha256"] = boundary_promotion_selection_sha256
            receipt["boundary_promotion_partition"] = args.boundary_promotion_partition
        _atomic_text(args.out_dir / "materialization.json", json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "case_count": receipt["case_count"],
                "candidate_positions_hidden": receipt["candidate_positions_hidden"],
                "selection_lock_sha256": receipt["selection_lock_sha256"],
                "review_manifest_sha256": receipt["review_manifest_sha256"],
                "boundary_promotion_selection_sha256": receipt.get(
                    "boundary_promotion_selection_sha256"
                ),
                "boundary_promotion_partition": receipt.get("boundary_promotion_partition"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
