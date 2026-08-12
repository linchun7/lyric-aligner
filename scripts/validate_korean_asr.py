#!/usr/bin/env python3
"""Compatibility wrapper for Korean jobs; prefer validate_multilingual_asr.py."""

from __future__ import annotations

import json

from validate_multilingual_asr import build_parser, run


def main() -> int:
    parser = build_parser()
    parser.description = __doc__
    args = parser.parse_args()
    args.default_language = "ko"
    try:
        return run(args, allow_legacy_jobs=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
