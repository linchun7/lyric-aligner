#!/usr/bin/env python3
"""Production v4 entrypoint with safe resume and bounded execution workers."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CORE = _load("_v4_run_legacy_public", SCRIPTS / "v4_run_legacy.py")
_OPTIMIZED = _load("_v4_run_optimized", SCRIPTS / "v4_run_optimized.py")

# Preserve helpers imported by existing tests/tools.
_forward_discontinuity_issue = _CORE._forward_discontinuity_issue
_effective_timewarp_payload = _CORE._effective_timewarp_payload
main = _OPTIMIZED.main


if __name__ == "__main__":
    raise SystemExit(main())
