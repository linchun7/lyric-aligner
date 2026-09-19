#!/usr/bin/env python3
"""Create or intentionally replace the task-local Full V4 semantic run config."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.contracts.run_config import (
    RUN_CONFIG_FILENAME,
    build_run_config,
    default_run_config_path,
    load_run_config,
    write_run_config_atomic,
)
from task_contract import load_task_manifest, manifest_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--language-map", type=Path)
    parser.add_argument("--middle-cut-map", type=Path)
    parser.add_argument("--lyric-role-map", type=Path)
    parser.add_argument(
        "--replace",
        action="store_true",
        help=f"Replace an existing {RUN_CONFIG_FILENAME} when its semantic fingerprint differs.",
    )
    args = parser.parse_args()

    try:
        manifest_path = args.task_manifest.resolve()
        manifest = load_task_manifest(manifest_path)
        repository_root = manifest_root(manifest_path)
        output_path = default_run_config_path(manifest_path)
        config = build_run_config(
            repository_root,
            str(manifest["task_fingerprint_sha256"]),
            profile=args.profile.resolve() if args.profile else None,
            language_map=args.language_map.resolve() if args.language_map else None,
            middle_cut_map=args.middle_cut_map.resolve() if args.middle_cut_map else None,
            lyric_role_map=args.lyric_role_map.resolve() if args.lyric_role_map else None,
        )
        for role, value in (
            ("profile", args.profile),
            ("language_map", args.language_map),
            ("middle_cut_map", args.middle_cut_map),
            ("lyric_role_map", args.lyric_role_map),
        ):
            if value is not None and value.resolve() == output_path:
                raise ValueError(f"{role} cannot be the run config output itself")

        changed = True
        if output_path.exists() and not args.replace:
            existing = load_run_config(
                output_path,
                repository_root=repository_root,
                expected_task_fingerprint_sha256=str(manifest["task_fingerprint_sha256"]),
            )
            if (
                existing["run_config_fingerprint_sha256"]
                == config["run_config_fingerprint_sha256"]
            ):
                changed = False
            else:
                raise ValueError(
                    f"existing {RUN_CONFIG_FILENAME} has different semantic inputs; rerun with --replace for an intentional config migration"
                )
        if changed:
            write_run_config_atomic(output_path, config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "run_config": str(output_path),
                "task_fingerprint_sha256": config["task_fingerprint_sha256"],
                "run_config_fingerprint_sha256": config[
                    "run_config_fingerprint_sha256"
                ],
                "changed": changed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
