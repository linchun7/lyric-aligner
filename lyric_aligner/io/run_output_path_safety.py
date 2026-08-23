"""Fail-closed output-tree ownership preflight for v4 orchestration entrypoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from lyric_aligner.io.path_safety import validate_artifact_output_tree
from lyric_aligner.io.task_path_safety import protected_task_input_paths


class RunOutputPathSafetyError(ValueError):
    """Raised when a v4 run cannot prove exclusive ownership of its output tree."""


_PATH_FLAGS = (
    "--task-manifest",
    "--out-dir",
    "--profile",
    "--language-map",
    "--middle-cut-map",
    "--lyric-role-map",
)
_DIRECT_INPUT_FLAGS = (
    "--profile",
    "--language-map",
    "--middle-cut-map",
    "--lyric-role-map",
)


def _path_arguments(argv: Sequence[str]) -> dict[str, Path]:
    values: dict[str, Path] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        matched = False
        for flag in _PATH_FLAGS:
            if token == flag:
                if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                    raise RunOutputPathSafetyError(f"{flag} requires a path value")
                values[flag] = Path(argv[index + 1])
                index += 2
                matched = True
                break
            prefix = flag + "="
            if token.startswith(prefix):
                value = token[len(prefix) :]
                if not value:
                    raise RunOutputPathSafetyError(f"{flag} requires a path value")
                values[flag] = Path(value)
                index += 1
                matched = True
                break
        if not matched:
            index += 1
    return values


def _load_manifest_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunOutputPathSafetyError(f"cannot read task manifest: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunOutputPathSafetyError("task manifest must contain a JSON object")
    return payload


def validate_run_output_tree_from_argv(argv: Sequence[str]) -> None:
    """Reject unsafe run ownership before lock/cache/stage directories are created."""

    if any(flag in argv for flag in ("-h", "--help")):
        return

    values = _path_arguments(argv)
    output_dir = values.get("--out-dir")
    if output_dir is None:
        return
    manifest_path = values.get("--task-manifest")
    if manifest_path is None:
        raise RunOutputPathSafetyError(
            "--out-dir requires --task-manifest before output-tree ownership can be proven"
        )

    manifest = _load_manifest_object(manifest_path)
    protected = protected_task_input_paths(
        manifest_path=manifest_path,
        manifest=manifest,
    )
    for flag in _DIRECT_INPUT_FLAGS:
        path = values.get(flag)
        if path is not None:
            protected[f"cli_{flag[2:].replace('-', '_')}"] = path

    validate_artifact_output_tree(
        inputs=protected,
        output_dir=output_dir,
    )
