#!/usr/bin/env python3
"""Fail-closed v4 release guard for final SRT/audit/QA artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.qa.final_integrity import FinalIntegrityError, build_release_artifact_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--algorithm-version", required=True)
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--out-manifest", required=True, type=Path)
    args = parser.parse_args()

    try:
        task = json.loads(args.task_manifest.read_text(encoding="utf-8-sig"))
        fingerprint = str(task["task_fingerprint_sha256"])
        manifest = build_release_artifact_manifest(
            final_srt=args.final_srt,
            audit_csv=args.report,
            qa_json=args.qa_json,
            task_fingerprint_sha256=fingerprint,
            algorithm_version=args.algorithm_version,
            git_commit=args.git_commit,
        )
        atomic_write_json(args.out_manifest, manifest)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, FinalIntegrityError) as exc:
        parser.error(str(exc))

    print(json.dumps({"artifact_id": manifest["artifact_id"], "release_status": "ready"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
