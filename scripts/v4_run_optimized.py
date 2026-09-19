#!/usr/bin/env python3
"""Safely enter the unchanged v4 optimized production orchestrator."""

from __future__ import annotations

import sys
from pathlib import Path

_IMPL_PATH = Path(__file__).with_name("_v4_run_optimized_impl.txt")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _execute_implementation() -> None:
    module_name = globals()["__name__"]
    globals()["__name__"] = "_lyric_aligner_v4_run_optimized_impl"
    try:
        source = _IMPL_PATH.read_text(encoding="utf-8")
        exec(compile(source, str(_IMPL_PATH), "exec"), globals(), globals())
    finally:
        globals()["__name__"] = module_name


_execute_implementation()
del _execute_implementation
_IMPLEMENTATION_MAIN = main

from lyric_aligner.contracts.run_config import (
    expand_run_config_argv,
    strip_run_config_control_argv,
)
from lyric_aligner.io.run_output_path_safety import validate_run_output_tree_from_argv


def main() -> int:
    original_argv = sys.argv
    try:
        try:
            argv = expand_run_config_argv(
                sys.argv[1:], repository_root=_REPOSITORY_ROOT
            )
            validate_run_output_tree_from_argv(argv)
        except (OSError, ValueError) as exc:
            print(f"v4_run_optimized.py: error: {exc}", file=sys.stderr)
            return 2
        sys.argv = [original_argv[0], *strip_run_config_control_argv(argv)]
        return _IMPLEMENTATION_MAIN()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
