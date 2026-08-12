#!/usr/bin/env python3
"""Check the local runtime required by the lyric-aligner Skill."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys


BASE_MODULES = ("numpy", "scipy", "librosa")
ASR_MODULES = ("faster_whisper",)


def check_environment(require_asr: bool = False) -> dict[str, object]:
    required_modules = BASE_MODULES + (ASR_MODULES if require_asr else ())
    modules = {
        name: importlib.util.find_spec(name) is not None for name in required_modules
    }
    tools = {name: shutil.which(name) is not None for name in ("ffprobe",)}
    python_ok = sys.version_info >= (3, 10)
    missing_modules = [name for name, available in modules.items() if not available]
    missing_tools = [name for name, available in tools.items() if not available]
    return {
        "python": sys.version.split()[0],
        "python_ok": python_ok,
        "modules": modules,
        "tools": tools,
        "missing_modules": missing_modules,
        "missing_tools": missing_tools,
        "asr_requested": require_asr,
        "ok": python_ok and not missing_modules and not missing_tools,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asr",
        action="store_true",
        help="Also require faster-whisper for ASR stages.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    result = check_environment(args.asr)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Python {result['python']}: {'OK' if result['python_ok'] else 'need 3.10+'}")
        for name, available in {**result["modules"], **result["tools"]}.items():
            print(f"{name}: {'OK' if available else 'MISSING'}")
    if not result["ok"]:
        print(
            "Install base dependencies with: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        if args.asr and "faster_whisper" in result["missing_modules"]:
            print(
                "Install ASR dependencies with: python -m pip install -r requirements-asr.txt",
                file=sys.stderr,
            )
        print("Install ffprobe through an FFmpeg distribution and add it to PATH.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
