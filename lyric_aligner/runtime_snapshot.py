"""Privacy-safe runtime identity snapshots for reproducible v4 calibration.

The snapshot intentionally omits hostnames, usernames, absolute repository
paths, raw lyrics and full external commands. Its stable identity hash covers
versions/configuration that can materially affect alignment output.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from lyric_aligner.command_line import CommandLineParseError, split_external_command


RUNTIME_SNAPSHOT_SCHEMA_VERSION = "1.0"
RUNTIME_IDENTITY_FIELDS = (
    "schema_version",
    "git",
    "python",
    "platform",
    "binaries",
    "packages",
    "models",
    "external_forced_aligner",
    "device_requested",
)
DEFAULT_PACKAGES = (
    "numpy",
    "scipy",
    "librosa",
    "soundfile",
    "scikit-learn",
    "faster-whisper",
    "whisperx",
    "torch",
    "torchaudio",
    "ctranslate2",
    "transformers",
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha(payload: Any) -> str:
    return _sha(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def runtime_identity_core(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only fields covered by the stable runtime identity."""

    if not isinstance(payload, dict):
        raise ValueError("runtime snapshot must be a JSON object")
    if str(payload.get("schema_version") or "") != RUNTIME_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"runtime snapshot schema_version must be {RUNTIME_SNAPSHOT_SCHEMA_VERSION}"
        )
    missing = [field for field in RUNTIME_IDENTITY_FIELDS if field not in payload]
    if missing:
        raise ValueError(
            "runtime snapshot is missing identity field(s): " + ",".join(missing)
        )
    return {field: payload[field] for field in RUNTIME_IDENTITY_FIELDS}


def validate_runtime_snapshot(payload: dict[str, Any]) -> str:
    """Validate and return the stable identity; reject metadata tampering."""

    core = runtime_identity_core(payload)
    claimed = str(payload.get("runtime_identity_sha256") or "").lower()
    if not _is_sha256(claimed):
        raise ValueError("runtime snapshot identity is invalid")
    expected = _canonical_sha(core)
    if not hmac.compare_digest(claimed, expected):
        raise ValueError("runtime snapshot identity hash does not match content")
    return claimed


def _run_process(
    argv: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _command_output(argv: list[str], *, cwd: Path | None = None) -> str | None:
    completed = _run_process(argv, cwd=cwd)
    if completed is None or completed.returncode != 0:
        return None
    output = str(completed.stdout or "").strip().splitlines()
    return output[0].strip() if output else None


def _git_dirty(repo_root: Path) -> bool | None:
    """Return False for a clean tree instead of conflating empty output/error."""

    completed = _run_process(["git", "status", "--porcelain"], cwd=repo_root)
    if completed is None or completed.returncode != 0:
        return None
    return bool(str(completed.stdout or "").strip())


def _git_identity(repo_root: Path) -> dict[str, Any]:
    commit = _command_output(["git", "rev-parse", "HEAD"], cwd=repo_root)
    branch = _command_output(["git", "branch", "--show-current"], cwd=repo_root)
    if commit is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        commit = None
    return {
        "commit_sha": commit.lower() if commit else None,
        "branch": branch or None,
        "dirty": _git_dirty(repo_root),
    }


def _binary_version(executable: str) -> dict[str, Any]:
    first = _command_output([executable, "-version"])
    return {
        "available": first is not None,
        "version_line": first,
    }


def _package_versions(packages: Iterable[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in packages:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _is_absolute_like(value: str) -> bool:
    return bool(
        value.startswith(("/", "\\\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
    )


def _model_identity(value: str) -> dict[str, str]:
    value = str(value).strip()
    if not value:
        raise ValueError("model identity must be non-empty")
    if _is_absolute_like(value):
        normalized = value.replace("\\", "/").rstrip("/")
        return {
            "kind": "local_path_redacted",
            "basename": normalized.rsplit("/", 1)[-1],
            "value_sha256": _sha(value),
        }
    return {"kind": "logical_id", "id": value}


def _external_command_identity(command: str | None) -> dict[str, Any] | None:
    command = str(command or "").strip()
    if not command:
        return None
    try:
        argv = split_external_command(command)
    except CommandLineParseError:
        argv = []
    executable = str(argv[0]) if argv else ""
    basename = (
        executable.replace("\\", "/").rsplit("/", 1)[-1]
        if executable
        else None
    )
    return {
        "executable_basename": basename,
        "command_sha256": _sha(command),
        "argument_count": max(0, len(argv) - 1),
    }


def build_runtime_snapshot(
    *,
    repo_root: Path,
    models: dict[str, str] | None = None,
    external_forced_aligner_command: str | None = None,
    device: str = "auto",
    packages: Iterable[str] = DEFAULT_PACKAGES,
) -> dict[str, Any]:
    """Collect a stable, non-secret runtime identity without loading ML models."""

    root = repo_root.resolve()
    model_rows = {
        str(family): _model_identity(value)
        for family, value in sorted((models or {}).items())
    }
    core = {
        "schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
        "git": _git_identity(root),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable_basename": Path(sys.executable).name,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "binaries": {
            "ffmpeg": _binary_version("ffmpeg"),
            "ffprobe": _binary_version("ffprobe"),
        },
        "packages": _package_versions(packages),
        "models": model_rows,
        "external_forced_aligner": _external_command_identity(
            external_forced_aligner_command
        ),
        "device_requested": str(device or "auto"),
    }
    payload = {
        **core,
        "runtime_identity_sha256": _canonical_sha(core),
        "privacy": "hostname, username, absolute repo/model paths and full external commands are omitted or hashed",
        "accuracy_boundary": "runtime reproducibility metadata does not establish model quality on singing",
    }
    validate_runtime_snapshot(payload)
    return payload
