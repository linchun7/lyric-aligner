#!/usr/bin/env python3
"""Freeze changed timing decisions and deterministic controls before human gold is read."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.timing_decision_pack import build_timing_decision_pack


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-audit", required=True, type=Path)
    parser.add_argument("--hybrid-audit", required=True, type=Path)
    parser.add_argument("--final-mix", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--min-changed-delta-ms", type=int, default=100)
    parser.add_argument("--unchanged-control-count", type=int, default=20)
    parser.add_argument("--clip-margin-ms", type=int, default=2500)
    args = parser.parse_args()
    try:
        inputs = {args.old_audit.resolve(), args.hybrid_audit.resolve(), args.final_mix.resolve()}
        if args.out.resolve() in inputs:
            raise ValueError("decision-pack output must not overwrite an input")
        for path in inputs:
            if not path.is_file():
                raise ValueError(f"required input does not exist: {path}")
        pack = build_timing_decision_pack(
            old_audit=args.old_audit,
            hybrid_audit=args.hybrid_audit,
            final_mix_sha256=_sha256(args.final_mix),
            min_changed_delta_ms=args.min_changed_delta_ms,
            unchanged_control_count=args.unchanged_control_count,
            clip_margin_ms=args.clip_margin_ms,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "gold_read": pack["gold_read"],
                "shared_unique_identity_count": pack["shared_unique_identity_count"],
                "changed_case_count": pack["changed_case_count"],
                "control_case_count": pack["control_case_count"],
                "selected_case_count": pack["selected_case_count"],
                "selection_lock_sha256": pack["selection_lock_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
