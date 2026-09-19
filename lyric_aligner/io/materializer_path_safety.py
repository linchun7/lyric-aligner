"""Fail-closed preflight for Max materializers that own an output directory tree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.io.path_safety import validate_artifact_output_tree
from lyric_aligner.io.task_path_safety import protected_task_input_paths


class MaterializerPathSafetyError(ValueError):
    """Raised when a materializer cannot prove safe output-tree ownership."""


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterializerPathSafetyError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MaterializerPathSafetyError(f"{label} must contain a JSON object")
    return payload


def _collect_declared_paths(
    value: object,
    *,
    label: str,
    output: dict[str, Path],
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child_label = f"{label}.{key_text}"
            if key_text.endswith("_path") and isinstance(item, str) and item.strip():
                output[child_label] = Path(item)
            else:
                _collect_declared_paths(item, label=child_label, output=output)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _collect_declared_paths(
                item,
                label=f"{label}[{index}]",
                output=output,
            )


def declared_input_paths(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Path]:
    """Collect all lineage-bearing ``*_path`` values from input payloads."""

    output: dict[str, Path] = {}
    for label, payload in payloads.items():
        _collect_declared_paths(payload, label=label, output=output)
    return output


def validate_materializer_preflight(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    direct_inputs: Mapping[str, Path],
    lineage_payloads: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
    outputs: Mapping[str, Path | None],
) -> None:
    """Protect task/direct/lineage inputs before a materializer creates anything."""

    protected = protected_task_input_paths(
        manifest_path=manifest_path,
        manifest=manifest,
    )
    protected.update({label: Path(path) for label, path in direct_inputs.items()})
    protected.update(declared_input_paths(lineage_payloads))
    validate_artifact_output_tree(
        inputs=protected,
        output_dir=output_dir,
        outputs=outputs,
    )
