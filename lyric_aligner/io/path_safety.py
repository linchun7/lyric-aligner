"""Path collision guards for production artifact writers."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping


class PathCollisionError(ValueError):
    """Raised when an output would overwrite an input or another output."""


def _resolved(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def validate_separate_artifact_paths(
    *,
    inputs: Mapping[str, Path],
    outputs: Mapping[str, Path | None],
) -> None:
    """Fail closed when outputs collide with source inputs or each other."""

    resolved_inputs = {
        label: _resolved(path)
        for label, path in inputs.items()
    }
    resolved_outputs = {
        label: _resolved(path)
        for label, path in outputs.items()
        if path is not None
    }

    input_by_path: dict[Path, list[str]] = {}
    for label, path in resolved_inputs.items():
        input_by_path.setdefault(path, []).append(label)

    output_by_path: dict[Path, list[str]] = {}
    for label, path in resolved_outputs.items():
        output_by_path.setdefault(path, []).append(label)

    collisions = []
    for path, output_labels in output_by_path.items():
        input_labels = input_by_path.get(path)
        if input_labels:
            collisions.append(
                f"output {', '.join(sorted(output_labels))} collides with input "
                f"{', '.join(sorted(input_labels))}: {path}"
            )
        if len(output_labels) > 1:
            collisions.append(
                f"outputs {', '.join(sorted(output_labels))} share the same path: {path}"
            )

    if collisions:
        raise PathCollisionError("; ".join(collisions))
