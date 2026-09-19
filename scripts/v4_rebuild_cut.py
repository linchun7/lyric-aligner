#!/usr/bin/env python3
"""Safely enter the confirmed-cut materializer."""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.io.materializer_path_safety import (
    MaterializerPathSafetyError,
    load_json_object,
    validate_materializer_preflight,
)
from task_contract import load_task_manifest, verify_manifest_inputs


_IMPL_PATH = Path(__file__).with_name("_v4_rebuild_cut_impl.txt")


def _implementation_main() -> int:
    namespace = runpy.run_path(
        str(_IMPL_PATH),
        run_name="_lyric_aligner_v4_rebuild_cut_impl",
    )
    implementation = namespace.get("main")
    if not callable(implementation):
        raise RuntimeError("cut materializer implementation has no main()")
    return int(implementation())


def _preflight(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    args, _ = parser.parse_known_args(argv)

    task = load_task_manifest(args.task_manifest)
    issues = verify_manifest_inputs(args.task_manifest, task)
    if issues:
        raise MaterializerPathSafetyError(
            "task manifest validation failed: " + "; ".join(issues)
        )
    run = load_json_object(args.run, label="reviewed run")
    validate_materializer_preflight(
        manifest_path=args.task_manifest,
        manifest=task,
        direct_inputs={
            "reviewed_run": args.run,
            "review_artifact": args.run_artifact,
            "track_assets": args.track_assets,
            "asset_artifact": args.asset_artifact,
        },
        lineage_payloads={"reviewed_run": run},
        output_dir=args.out_dir,
        outputs={
            "cut_rebuilt_run": args.out,
            "cut_rebuild_artifact": args.artifact_out,
        },
    )


def main() -> int:
    if any(flag in sys.argv[1:] for flag in ("-h", "--help")):
        return _implementation_main()
    try:
        _preflight(sys.argv[1:])
    except (OSError, KeyError, ValueError, MaterializerPathSafetyError) as exc:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.error(str(exc))
    return _implementation_main()


if __name__ == "__main__":
    raise SystemExit(main())
