"""Versioned runtime profiles for production boundary alignment sidecars.

These profiles bind logical backend identity to the local isolated runtime layout.
The sidecar model/runtime files remain under ``private/_models`` and are not portable
source.  A profile resolution verifies their presence and derives the deployed model
SHA at runtime; callers must never hand-edit family/correlation identities to pretend
one backend is an independent second observer.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lyric_aligner.alignment.window_policy import FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID


PRODUCTION_BOUNDARY_BACKEND_PROFILE_SCHEMA_VERSION = "production-boundary-backend-profile-1.0"
PRODUCTION_OBSERVER_CONTRACT_RELPATHS = (
    "lyric_aligner/alignment/boundary_executor.py",
    "lyric_aligner/alignment/internal_executor.py",
    "lyric_aligner/alignment/batch_executor.py",
    "lyric_aligner/audio/forced_alignment.py",
    "lyric_aligner/alignment/window_policy.py",
    "lyric_aligner/text/alignment_lexical.py",
    "lyric_aligner/evaluation/human_gold_prediction.py",
    "lyric_aligner/evaluation/production_calibration.py",
    "lyric_aligner/timeline/boundary_calibration.py",
)


class ProductionBackendProfileError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _implementation_revision(root: Path, relpaths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for relpath in relpaths:
        candidate = (root / relpath).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ProductionBackendProfileError("backend implementation path escapes repository") from exc
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(
                path
                for path in candidate.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix.casefold() != ".pyc"
            )
        else:
            raise ProductionBackendProfileError(f"backend implementation path is missing: {candidate}")
    if not files:
        raise ProductionBackendProfileError("backend implementation source set is empty")
    for path in sorted(set(files), key=lambda item: item.as_posix().casefold()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        file_sha = _sha256_file(path).encode("ascii")
        digest.update(file_sha)
    return digest.hexdigest()


def _command_string(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


@dataclass(frozen=True)
class ProductionBoundaryBackendProfile:
    profile_id: str
    backend_id: str
    backend_version: str
    family: str
    correlation_group: str
    model_id: str
    supported_languages: tuple[str, ...]
    sidecar_root_relpath: str
    adapter_relpath: str
    model_relpath: str
    implementation_relpaths: tuple[str, ...] = ()
    contract_relpaths: tuple[str, ...] = ()
    boundary_adapter_relpath: str = ""
    internal_adapter_relpath: str = ""
    batch_adapter_relpath: str = ""

    def public_identity(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = PRODUCTION_BOUNDARY_BACKEND_PROFILE_SCHEMA_VERSION
        return payload

    def resolve(self, repository_root: str | Path, *, language: str) -> dict[str, Any]:
        root = Path(repository_root).resolve()
        code = str(language or "").strip().lower()
        if code not in self.supported_languages:
            raise ProductionBackendProfileError(
                f"backend profile {self.profile_id} does not support language {code!r}"
            )
        sidecar_root = (root / self.sidecar_root_relpath).resolve()
        try:
            sidecar_root.relative_to(root)
        except ValueError as exc:
            raise ProductionBackendProfileError("sidecar root escapes repository") from exc
        adapter = (root / self.adapter_relpath).resolve()
        boundary_adapter = (
            root / (self.boundary_adapter_relpath or self.adapter_relpath)
        ).resolve()
        internal_adapter = (
            root / (self.internal_adapter_relpath or self.adapter_relpath)
        ).resolve()
        batch_adapter = (
            root / (self.batch_adapter_relpath or self.adapter_relpath)
        ).resolve()
        model = (root / self.model_relpath).resolve()
        for label, candidate in (("adapter", adapter), ("boundary adapter", boundary_adapter), ("internal adapter", internal_adapter), ("batch adapter", batch_adapter), ("model", model)):
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ProductionBackendProfileError(f"backend {label} path escapes repository") from exc
        if not adapter.is_file():
            raise ProductionBackendProfileError(f"backend adapter is missing: {adapter}")
        if not boundary_adapter.is_file():
            raise ProductionBackendProfileError(f"backend boundary adapter is missing: {boundary_adapter}")
        if not internal_adapter.is_file():
            raise ProductionBackendProfileError(f"backend internal adapter is missing: {internal_adapter}")
        if not batch_adapter.is_file():
            raise ProductionBackendProfileError(f"backend batch adapter is missing: {batch_adapter}")
        if not model.is_file():
            raise ProductionBackendProfileError(f"backend model is missing: {model}")

        windows_python = sidecar_root / "runtime" / ".venv" / "Scripts" / "python.exe"
        posix_python = sidecar_root / "runtime" / ".venv" / "bin" / "python"
        interpreter = windows_python if windows_python.is_file() else posix_python
        if not interpreter.is_file():
            raise ProductionBackendProfileError(
                f"isolated backend Python runtime is missing under {sidecar_root}"
            )
        model_revision = _sha256_file(model)
        implementation_revision = _implementation_revision(
            root,
            self.implementation_relpaths or (self.adapter_relpath,),
        )
        adapter_revision = _sha256_file(adapter)
        boundary_adapter_revision = _sha256_file(boundary_adapter)
        internal_adapter_revision = _sha256_file(internal_adapter)
        batch_adapter_revision = _sha256_file(batch_adapter)
        adapter_contract_revision = _implementation_revision(
            root,
            (
                self.adapter_relpath,
                self.boundary_adapter_relpath or self.adapter_relpath,
                self.internal_adapter_relpath or self.adapter_relpath,
                self.batch_adapter_relpath or self.adapter_relpath,
                *self.contract_relpaths,
            ),
        )
        return {
            "profile_id": self.profile_id,
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "family": self.family,
            "correlation_group": self.correlation_group,
            "model_id": self.model_id,
            "model_revision": model_revision,
            "implementation_revision": implementation_revision,
            "language": code,
            "audio_basis": "final_mix",
            "window_policy_id": FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
            "command": _command_string([str(interpreter), str(adapter)]),
            "boundary_command": _command_string([str(interpreter), str(boundary_adapter)]),
            "internal_command": _command_string([str(interpreter), str(internal_adapter)]),
            "batch_command": _command_string([str(interpreter), str(batch_adapter)]),
            "interpreter_path": str(interpreter),
            "adapter_path": str(adapter),
            "adapter_revision": adapter_revision,
            "boundary_adapter_path": str(boundary_adapter),
            "boundary_adapter_revision": boundary_adapter_revision,
            "internal_adapter_path": str(internal_adapter),
            "internal_adapter_revision": internal_adapter_revision,
            "batch_adapter_path": str(batch_adapter),
            "batch_adapter_revision": batch_adapter_revision,
            "adapter_contract_revision": adapter_contract_revision,
            "model_path": str(model),
        }


PRODUCTION_BOUNDARY_BACKEND_PROFILES: dict[str, ProductionBoundaryBackendProfile] = {
    "sofa_mandarin_v1": ProductionBoundaryBackendProfile(
        profile_id="sofa_mandarin_v1",
        backend_id="sofa_onnx_mandarin",
        backend_version="full-sequence-alignment-core-1.0",
        family="final_mix_singing_alignment",
        correlation_group="sofa_mandarin_singing_onnx_v1",
        model_id="sofa-mandarin-singing",
        supported_languages=("zh",),
        sidecar_root_relpath="private/_models/sofa",
        adapter_relpath="private/_models/sofa/gold_prediction_adapter.py",
        model_relpath="private/_models/sofa/mandarin_model/model.onnx",
        implementation_relpaths=(
            "private/_models/sofa/SOFA/onnx_infer.py",
            "private/_models/sofa/SOFA/modules/g2p",
            "private/_models/sofa/SOFA/modules/AP_detector",
            "private/_models/sofa/SOFA/modules/utils/export_tool.py",
            "private/_models/sofa/SOFA/modules/utils/post_processing.py",
            "private/_models/sofa/SOFA/dictionary/opencpop-extension.txt",
            "private/_models/sofa/mandarin_model/config.yaml",
        ),
        contract_relpaths=PRODUCTION_OBSERVER_CONTRACT_RELPATHS,
        boundary_adapter_relpath="private/_models/sofa/boundary_adapter.py",
        internal_adapter_relpath="private/_models/sofa/internal_adapter.py",
        batch_adapter_relpath="private/_models/sofa/production_batch_adapter.py",
    ),
    "hubertfa_mandarin_v1": ProductionBoundaryBackendProfile(
        profile_id="hubertfa_mandarin_v1",
        backend_id="hubertfa_onnx",
        backend_version="full-sequence-alignment-core-1.0",
        family="final_mix_forced_alignment",
        correlation_group="hubertfa_onnx_v1",
        model_id="hubertfa-1218",
        supported_languages=("zh",),
        sidecar_root_relpath="private/_models/hubertfa_sidecar",
        adapter_relpath="private/_models/hubertfa_sidecar/gold_prediction_adapter.py",
        model_relpath="private/_models/hubertfa_sidecar/model/model.onnx",
        implementation_relpaths=(
            "private/_models/vocal2midi_probe/inference/HubertFA",
            "private/_models/vocal2midi_probe/inference/device_utils.py",
            "private/_models/hubertfa_sidecar/model/config.json",
            "private/_models/hubertfa_sidecar/model/vocab.json",
            "private/_models/hubertfa_sidecar/model/ds-zh-pinyin-lite.txt",
        ),
        contract_relpaths=PRODUCTION_OBSERVER_CONTRACT_RELPATHS,
        boundary_adapter_relpath="private/_models/hubertfa_sidecar/boundary_adapter.py",
        internal_adapter_relpath="private/_models/hubertfa_sidecar/internal_adapter.py",
        batch_adapter_relpath="private/_models/hubertfa_sidecar/production_batch_adapter.py",
    ),
}


def get_production_boundary_backend_profile(profile_id: str) -> ProductionBoundaryBackendProfile:
    key = str(profile_id or "").strip()
    try:
        return PRODUCTION_BOUNDARY_BACKEND_PROFILES[key]
    except KeyError as exc:
        raise ProductionBackendProfileError(f"unknown production boundary backend profile: {key}") from exc


def validate_profile_registry() -> None:
    profiles = list(PRODUCTION_BOUNDARY_BACKEND_PROFILES.values())
    if not profiles:
        raise ProductionBackendProfileError("production boundary backend registry is empty")
    for key, profile in PRODUCTION_BOUNDARY_BACKEND_PROFILES.items():
        if key != profile.profile_id:
            raise ProductionBackendProfileError("backend profile registry key/id mismatch")
        required = (
            profile.profile_id,
            profile.backend_id,
            profile.backend_version,
            profile.family,
            profile.correlation_group,
            profile.model_id,
            profile.sidecar_root_relpath,
            profile.adapter_relpath,
            profile.model_relpath,
        )
        if any(not value for value in required) or not profile.supported_languages:
            raise ProductionBackendProfileError("backend profile identity is incomplete")
    if len({profile.backend_id for profile in profiles}) != len(profiles):
        raise ProductionBackendProfileError("production backend IDs must be unique")
    if len({profile.correlation_group for profile in profiles}) != len(profiles):
        raise ProductionBackendProfileError("production correlation groups must be unique")
    if len({profile.family for profile in profiles}) != len(profiles):
        raise ProductionBackendProfileError("current production profiles must occupy distinct evidence families")


validate_profile_registry()
