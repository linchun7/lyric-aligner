#!/usr/bin/env python3
"""Create an ignored local task workspace and scoped QA file skeletons."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
INVALID_TASK_NAME_CHARACTERS = set('<>:"/\\|?*')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def task_name(value: str) -> str:
    if value != value.strip() or value in {"", ".", ".."}:
        raise ValueError("task name must be a non-empty single directory name")
    if Path(value).name != value or any(
        char in value for char in INVALID_TASK_NAME_CHARACTERS
    ):
        raise ValueError("task name must not contain a path or reserved character")
    if (
        value.endswith((".", " "))
        or value.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("task name is reserved on Windows")
    return value


def validate_existing_scope(path: Path, source_hash: str) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"existing QA file is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"existing QA file must contain a JSON object: {path}")
    existing_hash = str(payload.get("source_srt_sha256", "")).strip().lower()
    if existing_hash != source_hash:
        raise ValueError(
            f"existing QA file belongs to a different source SRT: {path}"
        )


def init_task(root: Path, name: str, source_srt: Path) -> dict[str, str]:
    name = task_name(name)
    if not source_srt.is_file():
        raise FileNotFoundError(f"source SRT does not exist: {source_srt}")
    if source_srt.suffix.lower() != ".srt":
        raise ValueError(f"source file must use the .srt extension: {source_srt}")
    source_hash = sha256(source_srt)
    private_root = root / "private" / name
    input_root = private_root / "input"
    qa_root = private_root / "qa"
    output_root = root / "output" / name
    for directory in (input_root, qa_root, output_root):
        directory.mkdir(parents=True, exist_ok=True)

    metadata = {
        "schema_version": "1.0",
        "project": name,
        "source_srt_sha256": source_hash,
        "scope": "Only this exact Jianying SRT. Never load for another mix.",
    }
    overrides = {
        **metadata,
        "_insertions": [],
        "_cue_splits": [],
        "_timing_overrides": {},
        "_lrc_indices_overrides": {},
        "_confirmed_omitted_lrc_events": [],
        "_confirmed_boundary_pairs": [],
        "_review_notes": {},
    }
    regression = {**metadata, "cases": []}
    overrides_path = qa_root / f"{name}_manual_overrides.json"
    regression_path = qa_root / f"{name}_regression_cases.json"
    validate_existing_scope(overrides_path, source_hash)
    validate_existing_scope(regression_path, source_hash)
    if not overrides_path.exists():
        overrides_path.write_text(
            json.dumps(overrides, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if not regression_path.exists():
        regression_path.write_text(
            json.dumps(regression, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "input": str(input_root),
        "qa": str(qa_root),
        "output": str(output_root),
        "source_srt_sha256": source_hash,
        "manual_overrides": str(overrides_path),
        "regression_cases": str(regression_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Single directory name for the local task.")
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--root", default=Path("."), type=Path)
    args = parser.parse_args()
    try:
        result = init_task(args.root.resolve(), args.task, args.source_srt.resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
