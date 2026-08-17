"""Truthful runtime capability checks for optional ASR/alignment backends.

Availability means only that the local Python package/command is discoverable.
It does *not* mean a model is downloaded, licensed for a particular use, or
validated on singing. Executors must still validate model/config prerequisites.
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable


class BackendCapability(str, Enum):
    MIX_ASR = "mix_asr"
    WORD_TIMESTAMPS = "word_timestamps"
    CTC_ALIGNMENT = "ctc_alignment"
    SOURCE_FORCED_ALIGNMENT = "source_forced_alignment"


@dataclass(frozen=True)
class BackendStatus:
    backend_id: str
    kind: str
    available: bool
    capabilities: tuple[str, ...]
    discovery: str
    detail: str
    execution_ready: bool
    missing_execution_requirements: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _package_available(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _faster_whisper_status(model_id: str | None) -> BackendStatus:
    installed = _package_available("faster_whisper")
    missing: list[str] = []
    if not installed:
        missing.append("python_package:faster_whisper")
    if not str(model_id or "").strip():
        missing.append("model_id")
    return BackendStatus(
        backend_id="faster_whisper",
        kind="asr",
        available=installed,
        capabilities=(
            BackendCapability.MIX_ASR.value,
            BackendCapability.WORD_TIMESTAMPS.value,
        ),
        discovery="python:faster_whisper",
        detail=(
            "package importable; model/config still required"
            if installed
            else "faster_whisper package not importable in this environment"
        ),
        execution_ready=not missing,
        missing_execution_requirements=tuple(missing),
    )


def _whisperx_status(model_id: str | None, align_model_id: str | None) -> BackendStatus:
    installed = _package_available("whisperx")
    missing: list[str] = []
    if not installed:
        missing.append("python_package:whisperx")
    if not str(model_id or "").strip():
        missing.append("model_id")
    if not str(align_model_id or "").strip():
        missing.append("align_model_id")
    return BackendStatus(
        backend_id="whisperx",
        kind="asr_ctc_alignment",
        available=installed,
        capabilities=(
            BackendCapability.MIX_ASR.value,
            BackendCapability.WORD_TIMESTAMPS.value,
            BackendCapability.CTC_ALIGNMENT.value,
        ),
        discovery="python:whisperx",
        detail=(
            "package importable; explicit ASR/alignment model IDs still required"
            if installed
            else "whisperx package not importable in this environment"
        ),
        execution_ready=not missing,
        missing_execution_requirements=tuple(missing),
    )


def _command_argv(command: str) -> list[str]:
    try:
        return [
            str(value)
            for value in shlex.split(str(command or "").strip(), posix=os.name != "nt")
            if str(value)
        ]
    except ValueError:
        return []


def _external_forced_aligner_status(command: str | None) -> BackendStatus:
    command = str(command or "").strip()
    argv = _command_argv(command)
    executable = argv[0] if argv else ""
    resolved = shutil.which(executable) if executable else None
    missing: list[str] = []
    if not command:
        missing.append("external_command")
    elif not argv:
        missing.append("external_command_parse_error")
    elif resolved is None:
        missing.append(f"command_not_found:{executable}")
    return BackendStatus(
        backend_id="external_forced_aligner",
        kind="forced_alignment",
        available=resolved is not None,
        capabilities=(BackendCapability.SOURCE_FORCED_ALIGNMENT.value,),
        discovery=f"command:{command or '<not-configured>'}",
        detail=(
            f"configured executable resolved to {resolved}; arguments preserved for runtime"
            if resolved is not None
            else "no configured forced-aligner executable is discoverable"
        ),
        execution_ready=not missing,
        missing_execution_requirements=tuple(missing),
    )


def inspect_backends(
    *,
    faster_whisper_model_id: str | None = None,
    whisperx_model_id: str | None = None,
    whisperx_align_model_id: str | None = None,
    external_forced_aligner_command: str | None = None,
) -> list[BackendStatus]:
    """Inspect optional backends without importing/loading models."""

    return [
        _faster_whisper_status(faster_whisper_model_id),
        _whisperx_status(whisperx_model_id, whisperx_align_model_id),
        _external_forced_aligner_status(external_forced_aligner_command),
    ]


def capability_available(
    statuses: Iterable[BackendStatus],
    capability: BackendCapability | str,
    *,
    require_execution_ready: bool = False,
) -> bool:
    value = capability.value if isinstance(capability, BackendCapability) else str(capability)
    return any(
        value in status.capabilities
        and status.available
        and (status.execution_ready or not require_execution_ready)
        for status in statuses
    )
