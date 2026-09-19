#!/usr/bin/env python3
"""Scaffold and inspect private calibration/blind-test datasets without fake data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.readiness import (
    DatasetReadinessError,
    clone_candidate_manifest,
    default_policy,
    inspect_dataset_readiness,
    scaffold_manifest,
    write_scaffold_directories,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _require_new(path: Path) -> None:
    if path.exists():
        raise DatasetReadinessError(f"refusing to overwrite existing path: {path}")


def command_scaffold(args: argparse.Namespace) -> int:
    out_dir = args.out_dir.resolve()
    manifest_path = out_dir / f"{args.candidate_id}.dataset.json"
    calibration_policy = out_dir / "calibration-policy.template.json"
    blind_policy = out_dir / "blind-policy.template.json"
    for path in (manifest_path, calibration_policy, blind_policy):
        _require_new(path)

    payload = scaffold_manifest(
        dataset=args.dataset,
        dataset_revision=args.dataset_revision,
        candidate_id=args.candidate_id,
        train_cases=args.train_cases,
        calibration_cases=args.calibration_cases,
        blind_cases=args.blind_cases,
    )
    write_scaffold_directories(out_dir, payload)
    _write_json(manifest_path, payload)
    _write_json(calibration_policy, default_policy(split="calibration"))
    _write_json(blind_policy, default_policy(split="blind_test"))
    report = inspect_dataset_readiness(manifest_path)
    report_path = out_dir / "READINESS.json"
    _write_json(report_path, report)
    print(
        json.dumps(
            {
                "status": "scaffold_created_not_ready",
                "manifest": str(manifest_path),
                "readiness": str(report_path),
                "notice": "No SRT, QA, prediction or accuracy result was fabricated.",
            },
            ensure_ascii=False,
        )
    )
    return 0


def command_clone(args: argparse.Namespace) -> int:
    source = json.loads(args.source.read_text(encoding="utf-8-sig"))
    if not isinstance(source, dict):
        raise DatasetReadinessError("source manifest must be a JSON object")
    output = args.out.resolve()
    _require_new(output)
    payload = clone_candidate_manifest(source, candidate_id=args.candidate_id)
    write_scaffold_directories(output.parent, payload)
    _write_json(output, payload)
    report = inspect_dataset_readiness(output)
    report_path = output.with_name(output.stem + ".readiness.json")
    _write_json(report_path, report)
    print(
        json.dumps(
            {
                "status": "candidate_manifest_cloned_not_assumed_ready",
                "manifest": str(output),
                "readiness": str(report_path),
            }
        )
    )
    return 0


def _required_ready(report: dict, split: str | None, require: str) -> bool:
    splits = report["splits"]
    if split is not None:
        rows = [splits[split]]
    else:
        rows = list(splits.values())
    if require == "metadata":
        return bool(report["metadata_ready"])
    if require == "references":
        return all(bool(row["reference_ready"]) for row in rows)
    if require == "predictions":
        return all(bool(row["prediction_files_ready"]) for row in rows)
    if require == "evaluation":
        return all(bool(row["evaluation_ready"]) for row in rows)
    raise DatasetReadinessError(f"unknown readiness requirement {require}")


def command_check(args: argparse.Namespace) -> int:
    report = inspect_dataset_readiness(args.dataset.resolve(), split=args.split)
    if args.out:
        _write_json(args.out.resolve(), report)
    passed = _required_ready(report, args.split, args.require)
    print(
        json.dumps(
            {
                "passed": passed,
                "require": args.require,
                "split": args.split or "all",
                "dataset": report["dataset"],
                "dataset_revision": report["dataset_revision"],
                "splits": report["splits"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if passed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser(
        "scaffold", help="create empty private-dataset paths and policy templates"
    )
    scaffold.add_argument("--out-dir", required=True, type=Path)
    scaffold.add_argument("--dataset", required=True)
    scaffold.add_argument("--dataset-revision", required=True)
    scaffold.add_argument("--candidate-id", default="baseline")
    scaffold.add_argument("--train-cases", type=int, default=0)
    scaffold.add_argument("--calibration-cases", type=int, default=6)
    scaffold.add_argument("--blind-cases", type=int, default=6)
    scaffold.set_defaults(func=command_scaffold)

    clone = subparsers.add_parser(
        "clone-candidate",
        help="preserve ground truth while rewriting prediction/QA destinations",
    )
    clone.add_argument("--source", required=True, type=Path)
    clone.add_argument("--candidate-id", required=True)
    clone.add_argument("--out", required=True, type=Path)
    clone.set_defaults(func=command_clone)

    check = subparsers.add_parser(
        "check", help="report metadata/reference/prediction/runtime readiness"
    )
    check.add_argument("--dataset", required=True, type=Path)
    check.add_argument(
        "--split", choices=("train", "calibration", "blind_test")
    )
    check.add_argument(
        "--require",
        choices=("metadata", "references", "predictions", "evaluation"),
        default="metadata",
    )
    check.add_argument("--out", type=Path)
    check.set_defaults(func=command_check)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError, DatasetReadinessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
