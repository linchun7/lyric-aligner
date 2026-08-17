#!/usr/bin/env python3
"""Export or validate a complete Lyric Aligner v4 calibration profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.config import DEFAULT_V4_PROFILE, load_profile, write_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write-default", type=Path)
    group.add_argument("--validate", type=Path)
    args = parser.parse_args()
    try:
        if args.write_default:
            write_profile(args.write_default, DEFAULT_V4_PROFILE)
            profile = DEFAULT_V4_PROFILE
            path = args.write_default
        else:
            profile = load_profile(args.validate)
            path = args.validate
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "path": str(path),
        "profile_version": profile.profile_version,
        "profile_id": profile.profile_id,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
