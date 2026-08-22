"""Shared task-manifest path protection for production artifact writers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


class TaskPathSafetyError(ValueError):
    """Raised when manifest-bound protected paths are malformed."""


def _safe_relative(value: object, *, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise TaskPathSafetyError(f"{label} has a blank path")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise TaskPathSafetyError(f"{label} has an unsafe repository-relative path")
    return path


def protected_task_input_paths(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    repository_root: Path,
) -> dict[str, Path]:
    """Return every manifest-bound path that artifact writers must not overwrite.

    Directory inputs are expanded to every fingerprinted file member because
    the generic path-collision guard compares concrete paths rather than doing
    ancestor/descendant checks. Callers are expected to validate the task
    manifest before invoking this helper; this function still fails closed on
    malformed records instead of trusting that precondition implicitly.
    """

    protected: dict[str, Path] = {"task_manifest": Path(manifest_path)}
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise TaskPathSafetyError("task manifest inputs must be an object")

    root = Path(repository_root)
    for role, record in sorted(inputs.items(), key=lambda item: str(item[0])):
        if record is None:
            continue
        if not isinstance(record, Mapping):
            raise TaskPathSafetyError(f"task manifest input {role} must be an object")
        role_path = root / _safe_relative(
            record.get("path"), label=f"task manifest input {role}"
        )
        protected[f"task_{role}"] = role_path

        kind = record.get("kind")
        if kind == "file":
            continue
        if kind != "directory":
            raise TaskPathSafetyError(f"task manifest input {role} has invalid kind")

        rows = record.get("files")
        if not isinstance(rows, list):
            raise TaskPathSafetyError(
                f"task manifest directory input {role} has invalid files"
            )
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                raise TaskPathSafetyError(
                    f"task manifest directory input {role} has invalid file entry"
                )
            relative = _safe_relative(
                item.get("path"),
                label=f"task manifest directory input {role} file {index}",
            )
            protected[f"task_{role}_{index}"] = role_path / relative

    return protected
