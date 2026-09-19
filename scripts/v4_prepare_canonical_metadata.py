#!/usr/bin/env python3
"""Prepare a separate line-LRC input using a hash-bound performer-prefix map."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.text.canonical_metadata import prepare_canonical_metadata


def _publish_preparation(output: Path, report_path: Path, prepared: str, report: dict) -> None:
    report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    owned: list[Path] = []
    try:
        # Publish the audit first so usable prepared lyrics never precede their
        # evidence. Exclusive creation prevents a repeated/concurrent run from
        # truncating somebody else's output. Roll back only files created here.
        for path, text in ((report_path, report_text), (output, prepared)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="") as handle:
                owned.append(path)
                handle.write(text)
    except BaseException:
        for path in reversed(owned):
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--role-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_separate_artifact_paths(
            inputs={"source": args.source, "role_map": args.role_map},
            outputs={"output": args.output, "report": args.report},
        )
        mapping = json.loads(args.role_map.read_text(encoding="utf-8-sig"))
        if not isinstance(mapping, dict):
            raise ValueError("role map must be a JSON object")
        prepared, report = prepare_canonical_metadata(args.source, mapping)
        for path in (args.output, args.report):
            if path.exists():
                raise ValueError("preparation output already exists; use a fresh path")
        _publish_preparation(args.output, args.report, prepared, report)
    except (OSError, ValueError, UnicodeError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "removed_prefix_count": report["removed_prefix_count"],
                "timestamps_immutable": True,
                "policy_id": report["policy_id"],
                "output": str(args.output),
                "report": str(args.report),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
