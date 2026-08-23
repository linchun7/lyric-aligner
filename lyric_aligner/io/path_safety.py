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
    """Fail closed when outputs collide with source inputs or each other.

    Existing directory inputs protect their entire subtree, including output
    filenames that do not exist yet. This prevents artifact writers from adding
    a new file to a fingerprinted input directory and silently invalidating the
    task manifest after verification.
    """

    resolved_inputs = {label: _resolved(path) for label, path in inputs.items()}
    resolved_outputs = {
        label: _resolved(path)
        for label, path in outputs.items()
        if path is not None
    }
    input_directories = {
        label: resolved_inputs[label]
        for label, original in inputs.items()
        if Path(original).expanduser().is_dir()
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
        for input_label, directory in input_directories.items():
            if path != directory and path.is_relative_to(directory):
                collisions.append(
                    f"output {', '.join(sorted(output_labels))} is inside input directory "
                    f"{input_label}: {directory}"
                )
        if len(output_labels) > 1:
            collisions.append(
                f"outputs {', '.join(sorted(output_labels))} share the same path: {path}"
            )

    if collisions:
        raise PathCollisionError("; ".join(collisions))


def validate_artifact_output_tree(
    *,
    inputs: Mapping[str, Path],
    output_dir: Path,
    outputs: Mapping[str, Path | None] | None = None,
) -> None:
    """Require one materialization tree to be disjoint from every protected input.

    ``validate_separate_artifact_paths`` prevents an output from overwriting an
    input or being created inside an existing input directory. Materializers also
    create dynamic descendants below ``output_dir``; therefore the tree itself
    must not contain any protected input path. Direct outputs may live inside the
    output tree, but still may not collide with protected inputs or each other.
    """

    direct_outputs = dict(outputs or {})
    validate_separate_artifact_paths(
        inputs=inputs,
        outputs={**direct_outputs, "materialization_tree": output_dir},
    )

    tree = _resolved(output_dir)
    collisions: list[str] = []
    for label, path in inputs.items():
        resolved = _resolved(path)
        if resolved != tree and resolved.is_relative_to(tree):
            collisions.append(
                f"materialization tree contains input {label}: {resolved}"
            )
    if collisions:
        raise PathCollisionError("; ".join(collisions))
