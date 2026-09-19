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


def _manifest_root(path: Path) -> Path:
    resolved = Path(path).resolve()
    if (
        resolved.name == "task_manifest.json"
        and resolved.parent.name == "qa"
        and resolved.parent.parent.parent.name == "private"
    ):
        return resolved.parent.parent.parent.parent
    raise TaskPathSafetyError(
        "task manifest must be stored at private/<task>/qa/task_manifest.json"
    )


def protected_task_input_paths(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    repository_root: Path | None = None,
) -> dict[str, Path]:
    """Return every manifest-bound path that artifact writers must not overwrite.

    The manifest location is the path authority, matching the task-manifest
    contract. ``repository_root`` is accepted only so the first callers from the
    same maintenance series remain source-compatible; it is deliberately not a
    second root source and is ignored.

    A directory input is itself the protected root. The shared path-safety
    guard protects the entire subtree of an existing directory input; expanding
    fingerprinted members additionally keeps exact files visible for precise
    diagnostics and lineage checks. Callers should validate the task manifest
    before invoking this helper; malformed records still fail closed.
    """

    del repository_root
    resolved_manifest = Path(manifest_path).resolve()
    protected: dict[str, Path] = {"task_manifest": resolved_manifest}
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise TaskPathSafetyError("task manifest inputs must be an object")

    root = _manifest_root(resolved_manifest)
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
