"""Fail-closed path preflight for directly executable V4 primary stage writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.io.materializer_path_safety import declared_input_paths
from lyric_aligner.io.path_safety import (
    validate_artifact_output_tree,
    validate_separate_artifact_paths,
)
from lyric_aligner.io.task_path_safety import protected_task_input_paths


class StageWriterPathSafetyError(ValueError):
    """Raised when a primary stage writer cannot prove output ownership."""


_STAGE_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "resolve_assets": {
        "direct_inputs": (
            "--song-list",
            "--lyrics-dir",
            "--source-dir",
            "--profile",
            "--language-map",
            "--middle-cut-map",
            "--lyric-role-map",
        ),
        "lineage_json": (),
    },
    "coarse_align": {
        "direct_inputs": (
            "--mix-audio",
            "--track-assets",
            "--asset-artifact",
        ),
        "lineage_json": (
            "--track-assets",
            "--asset-artifact",
        ),
    },
    "fine_align": {
        "direct_inputs": (
            "--mix-audio",
            "--track-assets",
            "--asset-artifact",
            "--coarse",
            "--coarse-artifact",
        ),
        "lineage_json": (
            "--track-assets",
            "--asset-artifact",
            "--coarse",
            "--coarse-artifact",
        ),
    },
    "probe_transition": {
        "direct_inputs": (
            "--track-assets",
            "--asset-artifact",
            "--left-coarse",
            "--left-artifact",
            "--right-coarse",
            "--right-artifact",
        ),
        "lineage_json": (
            "--track-assets",
            "--asset-artifact",
            "--left-coarse",
            "--left-artifact",
            "--right-coarse",
            "--right-artifact",
        ),
    },
}

_COMMON_PATH_FLAGS = (
    "--task-manifest",
    "--out",
    "--artifact-out",
    "--feature-cache-dir",
)


def _flag_label(flag: str) -> str:
    return flag[2:].replace("-", "_")


def _path_arguments(argv: Sequence[str], flags: Sequence[str]) -> dict[str, Path]:
    wanted = tuple(dict.fromkeys(flags))
    values: dict[str, Path] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        matched = False
        for flag in wanted:
            if token == flag:
                if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                    raise StageWriterPathSafetyError(f"{flag} requires a path value")
                values[flag] = Path(argv[index + 1])
                index += 2
                matched = True
                break
            prefix = flag + "="
            if token.startswith(prefix):
                value = token[len(prefix) :]
                if not value:
                    raise StageWriterPathSafetyError(f"{flag} requires a path value")
                values[flag] = Path(value)
                index += 1
                matched = True
                break
        if not matched:
            index += 1
    return values


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageWriterPathSafetyError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StageWriterPathSafetyError(f"{label} must contain a JSON object")
    return payload


def _default_coarse_feature_cache_dir(out_path: Path) -> Path | None:
    resolved = Path(out_path).expanduser().resolve(strict=False)
    for parent in resolved.parents:
        if parent.name in {"primary", "transitions"}:
            return parent.parent / "cache" / "features"
    return None


def _validate_outputs(
    *,
    protected: Mapping[str, Path],
    out_path: Path,
    artifact_out: Path,
    cache_dir: Path | None,
) -> None:
    outputs = {
        "stage_output": out_path,
        "stage_artifact": artifact_out,
    }
    if cache_dir is None:
        validate_separate_artifact_paths(inputs=protected, outputs=outputs)
        return
    validate_artifact_output_tree(
        inputs=protected,
        output_dir=cache_dir,
        outputs=outputs,
    )


def validate_primary_stage_writer_from_argv(
    argv: Sequence[str],
    *,
    stage: str,
) -> None:
    """Reject unsafe primary-stage writes before the unchanged implementation runs."""

    if any(flag in argv for flag in ("-h", "--help")):
        return
    spec = _STAGE_SPECS.get(stage)
    if spec is None:
        raise StageWriterPathSafetyError(f"unsupported primary stage: {stage}")

    direct_input_flags = spec["direct_inputs"]
    lineage_flags = spec["lineage_json"]
    flags = _COMMON_PATH_FLAGS + direct_input_flags + lineage_flags
    values = _path_arguments(argv, flags)

    manifest_path = values.get("--task-manifest")
    out_path = values.get("--out")
    artifact_out = values.get("--artifact-out")
    if manifest_path is None or out_path is None or artifact_out is None:
        # Preserve argparse as the authority for ordinary missing-argument errors.
        return

    manifest = _load_json_object(manifest_path, label="task manifest")
    protected = protected_task_input_paths(
        manifest_path=manifest_path,
        manifest=manifest,
    )
    for flag in direct_input_flags:
        path = values.get(flag)
        if path is not None:
            protected[f"cli_{_flag_label(flag)}"] = path

    cache_dir: Path | None = None
    if stage == "coarse_align":
        cache_dir = values.get("--feature-cache-dir") or _default_coarse_feature_cache_dir(out_path)

    # Check task/direct-input ownership first. This guarantees common collisions
    # fail before we even need to parse upstream lineage payloads.
    _validate_outputs(
        protected=protected,
        out_path=out_path,
        artifact_out=artifact_out,
        cache_dir=cache_dir,
    )

    lineage_payloads: dict[str, Mapping[str, Any]] = {}
    for flag in lineage_flags:
        path = values.get(flag)
        if path is not None:
            lineage_payloads[f"cli_{_flag_label(flag)}"] = _load_json_object(
                path,
                label=flag,
            )
    protected.update(declared_input_paths(lineage_payloads))

    _validate_outputs(
        protected=protected,
        out_path=out_path,
        artifact_out=artifact_out,
        cache_dir=cache_dir,
    )
