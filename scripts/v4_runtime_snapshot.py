#!/usr/bin/env python3
"""Capture privacy-safe runtime identity for reproducible v4 calibration runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.runtime_snapshot import build_runtime_snapshot


def _parse_models(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--model must use FAMILY=MODEL_ID")
        family, model_id = value.split("=", 1)
        family = family.strip()
        model_id = model_id.strip()
        if not family or not model_id:
            raise ValueError("--model must use non-empty FAMILY=MODEL_ID")
        if family in result:
            raise ValueError(f"duplicate model family: {family}")
        result[family] = model_id
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--model", action="append", default=[], metavar="FAMILY=MODEL_ID")
    parser.add_argument("--external-forced-aligner-command")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        models = _parse_models(args.model)
        payload = build_runtime_snapshot(
            repo_root=args.repo_root,
            models=models,
            external_forced_aligner_command=args.external_forced_aligner_command,
            device=args.device,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "runtime_identity_sha256": payload["runtime_identity_sha256"],
                "git": payload["git"],
                "device_requested": payload["device_requested"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
