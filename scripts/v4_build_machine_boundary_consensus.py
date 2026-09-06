#!/usr/bin/env python3
"""Build machine-only 90-point boundary candidates from the locked v4 benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.machine_boundary_consensus import (
    DEFAULT_PROFILE_IDS,
    build_machine_boundary_consensus,
)
from scripts.v4_ingest_human_boundary_gold import verify_lock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--backend-profile", action="append", dest="backend_profiles")
    args = parser.parse_args()

    if args.out.exists():
        raise FileExistsError("machine consensus output must be a new path")
    lock = json.loads(args.lock.read_text(encoding="utf-8-sig"))
    if not isinstance(lock, dict):
        raise ValueError("selection lock must be a JSON object")
    verify_lock(lock, args.lock)
    artifact = build_machine_boundary_consensus(
        full_lock=lock,
        repository_root=REPOSITORY_ROOT,
        profile_ids=tuple(args.backend_profiles or DEFAULT_PROFILE_IDS),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "record_count": artifact["record_count"],
                "status_counts": artifact["status_counts"],
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
