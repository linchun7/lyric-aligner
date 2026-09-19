#!/usr/bin/env python3
"""Safely enter the unchanged V4 asset-resolution implementation."""

from __future__ import annotations

import sys
from pathlib import Path

_IMPL_PATH = Path(__file__).with_name("_v4_resolve_assets_impl.txt")


def _execute_implementation() -> None:
    module_name = globals()["__name__"]
    globals()["__name__"] = "_lyric_aligner_v4_resolve_assets_impl"
    try:
        source = _IMPL_PATH.read_text(encoding="utf-8")
        exec(compile(source, str(_IMPL_PATH), "exec"), globals(), globals())
    finally:
        globals()["__name__"] = module_name


_execute_implementation()
del _execute_implementation
_IMPLEMENTATION_MAIN = main

from lyric_aligner.io.stage_writer_path_safety import validate_primary_stage_writer_from_argv


def main() -> int:
    try:
        validate_primary_stage_writer_from_argv(sys.argv[1:], stage="resolve_assets")
    except (OSError, ValueError) as exc:
        print(f"v4_resolve_assets.py: error: {exc}", file=sys.stderr)
        return 2
    return _IMPLEMENTATION_MAIN()


if __name__ == "__main__":
    raise SystemExit(main())
