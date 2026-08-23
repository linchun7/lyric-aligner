#!/usr/bin/env python3
"""Production v4 entrypoint with safe resume and bounded execution workers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.io.run_output_path_safety import validate_run_output_tree_from_argv
from lyric_aligner.pipeline.run_lock import OutputRunLock, OutputRunLockError


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CORE = _load("_v4_run_legacy_public", SCRIPTS / "v4_run_legacy.py")
_OPTIMIZED = _load("_v4_run_optimized", SCRIPTS / "v4_run_optimized.py")

# Preserve helpers imported by existing tests/tools, while keeping optimized
# execution as the public CLI main(). Module-level fallback avoids turning this
# performance refactor into an accidental helper-import compatibility break.
_forward_discontinuity_issue = _CORE._forward_discontinuity_issue
_effective_timewarp_payload = _CORE._effective_timewarp_payload


def _out_dir_from_argv(argv: list[str]) -> Path | None:
    for index, value in enumerate(argv):
        if value == "--out-dir" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if value.startswith("--out-dir="):
            return Path(value.split("=", 1)[1])
    return None


def main() -> int:
    argv = sys.argv[1:]
    if any(flag in argv for flag in ("-h", "--help")):
        return _OPTIMIZED.main()
    try:
        validate_run_output_tree_from_argv(argv)
    except (OSError, ValueError) as exc:
        print(f"v4_run.py: error: {exc}", file=sys.stderr)
        return 2

    out_dir = _out_dir_from_argv(argv)
    if out_dir is None:
        return _OPTIMIZED.main()
    try:
        with OutputRunLock(out_dir):
            return _OPTIMIZED.main()
    except OutputRunLockError as exc:
        print(f"v4_run.py: error: {exc}", file=sys.stderr)
        return 2


def __getattr__(name: str):
    return getattr(_CORE, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_CORE)))


if __name__ == "__main__":
    raise SystemExit(main())
