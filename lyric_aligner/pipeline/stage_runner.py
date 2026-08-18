"""Safe resumable subprocess runner for v4 production orchestration.

Only expensive deterministic stages with formal artifact manifests are eligible
for cross-run reuse. Reuse is fail-closed: task/algorithm/stage identity,
producer git commit, exact upstream artifact ids, output SHA-256, current runtime
identity and key stage-specific evidence/config must all match. Any mismatch
simply executes the stage again.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    canonical_json_sha256,
    validate_artifact_output,
    validate_upstream_artifact,
)


_RESUMABLE_SCRIPTS = {
    "v4_coarse_align.py": "coarse_audio_alignment",
    "v4_fine_align.py": "fine_audio_alignment",
    "v4_probe_transition.py": "transition_probe",
}
_RUNTIME_PACKAGES = ("numpy", "scipy", "librosa", "soundfile", "soxr", "numba")


@dataclass(frozen=True)
class StageExecutionSummary:
    resume_enabled: bool
    workers: int
    resume_hits: int
    resume_misses: int
    executed: int
    memo_hits: int
    miss_reasons: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "resume_enabled": self.resume_enabled,
            "workers": self.workers,
            "resume_hits": self.resume_hits,
            "resume_misses": self.resume_misses,
            "executed": self.executed,
            "memo_hits": self.memo_hits,
            "miss_reasons": dict(sorted(self.miss_reasons.items())),
        }


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _argument(command: list[str], flag: str) -> str | None:
    try:
        index = command.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def _same_number(left: object, right: object, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _runtime_identity_sha256() -> str:
    packages: dict[str, str] = {}
    for name in _RUNTIME_PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    libsndfile = "unknown"
    try:
        import soundfile  # type: ignore

        libsndfile = str(getattr(soundfile, "__libsndfile_version__", "unknown"))
    except Exception:
        pass
    return canonical_json_sha256(
        {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "packages": packages,
            "libsndfile": libsndfile,
        }
    )


def _resume_sidecar_path(artifact_path: Path) -> Path:
    return artifact_path.with_name(artifact_path.name + ".resume.json")


class SafeStageRunner:
    """Run independent CLI stages with bounded concurrency and safe reuse."""

    def __init__(
        self,
        *,
        repository_root: Path,
        task_fingerprint_sha256: str,
        git_commit: str,
        workers: int = 2,
        resume: bool = True,
        git_identity_verified: bool = False,
    ) -> None:
        if workers < 1 or workers > 4:
            raise ValueError("workers must be between 1 and 4")
        self.repository_root = repository_root.resolve()
        self.task_fingerprint_sha256 = task_fingerprint_sha256
        self.git_commit = git_commit.strip()
        self.git_identity_verified = bool(git_identity_verified)
        self.workers = workers
        self.resume_enabled = bool(
            resume and self.git_commit and self.git_identity_verified
        )
        self.runtime_identity_sha256 = _runtime_identity_sha256()
        self._memo: dict[tuple[str, ...], str] = {}
        self._lock = threading.Lock()
        self._resume_hits = 0
        self._resume_misses = 0
        self._executed = 0
        self._memo_hits = 0
        self._miss_reasons: dict[str, int] = {}

    def _record_miss(self, reason: str) -> None:
        with self._lock:
            self._resume_misses += 1
            self._miss_reasons[reason] = self._miss_reasons.get(reason, 0) + 1

    def _artifact_identity(
        self,
        artifact_path: Path,
        *,
        expected_stage: str,
        output_role: str,
        output_path: Path,
        expected_upstream_ids: Iterable[str],
    ) -> tuple[dict, str | None]:
        try:
            artifact = _load(artifact_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}, "artifact_unreadable"
        issues = validate_upstream_artifact(
            artifact,
            expected_task_fingerprint=self.task_fingerprint_sha256,
            expected_algorithm_version=__version__,
            expected_stage=expected_stage,
        )
        if issues:
            return artifact, "artifact_identity_mismatch"
        producer = artifact.get("producer")
        if not isinstance(producer, dict) or str(producer.get("git_commit") or "") != self.git_commit:
            return artifact, "producer_git_commit_mismatch"
        actual_upstream = sorted(str(item) for item in artifact.get("upstream_artifact_ids", []))
        expected_upstream = sorted(str(item) for item in expected_upstream_ids)
        if actual_upstream != expected_upstream:
            return artifact, "upstream_artifact_mismatch"
        output_issues = validate_artifact_output(
            artifact,
            role=output_role,
            path=output_path,
        )
        if output_issues:
            return artifact, "output_digest_mismatch"
        return artifact, None

    def _resume_sidecar_matches(self, artifact_path: Path, artifact: dict) -> bool:
        try:
            payload = _load(_resume_sidecar_path(artifact_path))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        record_id = str(payload.get("record_id") or "")
        unsigned = {key: value for key, value in payload.items() if key != "record_id"}
        if not record_id or record_id != canonical_json_sha256(unsigned):
            return False
        return (
            payload.get("schema_version") == "1.0"
            and str(payload.get("artifact_id") or "") == str(artifact.get("artifact_id") or "")
            and str(payload.get("runtime_identity_sha256") or "") == self.runtime_identity_sha256
            and str(payload.get("git_commit") or "") == self.git_commit
        )

    def _write_resume_sidecar(self, command: list[str]) -> None:
        if not self.git_commit or not self.git_identity_verified or len(command) < 2:
            return
        script = Path(command[1]).name
        if script not in _RESUMABLE_SCRIPTS:
            return
        artifact_value = _argument(command, "--artifact-out")
        if not artifact_value:
            return
        artifact_path = Path(artifact_value)
        try:
            artifact = _load(artifact_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if str(artifact.get("stage") or "") != _RESUMABLE_SCRIPTS[script]:
            return
        core = {
            "schema_version": "1.0",
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "runtime_identity_sha256": self.runtime_identity_sha256,
            "git_commit": self.git_commit,
        }
        atomic_write_json(
            _resume_sidecar_path(artifact_path),
            {**core, "record_id": canonical_json_sha256(core)},
        )

    def _check_reusable(self, command: list[str]) -> tuple[bool, str]:
        if not self.resume_enabled:
            return False, "resume_disabled"
        if len(command) < 2:
            return False, "unsupported_command"
        script = Path(command[1]).name
        if script not in _RESUMABLE_SCRIPTS:
            return False, "unsupported_stage"
        out_value = _argument(command, "--out")
        artifact_value = _argument(command, "--artifact-out")
        if not out_value or not artifact_value:
            return False, "missing_output_paths"
        out_path = Path(out_value)
        artifact_path = Path(artifact_value)
        if not out_path.is_file() or not artifact_path.is_file():
            return False, "outputs_missing"

        try:
            payload = _load(out_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False, "output_unreadable"
        if str(payload.get("task_fingerprint_sha256") or "") != self.task_fingerprint_sha256:
            return False, "output_task_fingerprint_mismatch"
        if str(payload.get("algorithm_version") or "") != __version__:
            return False, "output_algorithm_version_mismatch"

        if script == "v4_coarse_align.py":
            asset_artifact_value = _argument(command, "--asset-artifact")
            occurrence_id = _argument(command, "--occurrence-id")
            if not asset_artifact_value or not occurrence_id:
                return False, "coarse_command_incomplete"
            try:
                asset_id = str(_load(Path(asset_artifact_value))["artifact_id"])
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                return False, "asset_artifact_unreadable"
            artifact, reason = self._artifact_identity(
                artifact_path,
                expected_stage="coarse_audio_alignment",
                output_role="coarse_alignment",
                output_path=out_path,
                expected_upstream_ids=(asset_id,),
            )
            if reason:
                return False, reason
            if not self._resume_sidecar_matches(artifact_path, artifact):
                return False, "runtime_resume_identity_mismatch"
            if str(payload.get("occurrence_id") or "") != occurrence_id:
                return False, "coarse_occurrence_mismatch"
            evidence = artifact.get("evidence", {})
            if str(evidence.get("occurrence_id") or "") != occurrence_id:
                return False, "coarse_evidence_mismatch"
            config = artifact.get("normalized_config", {})
            if str(config.get("asset_artifact_id") or "") != asset_id:
                return False, "coarse_asset_config_mismatch"
            mix_start = _argument(command, "--mix-start")
            mix_end = _argument(command, "--mix-end")
            if mix_start is not None and not _same_number(config.get("mix_start"), mix_start):
                return False, "coarse_mix_start_mismatch"
            if mix_end is not None and not _same_number(config.get("mix_end"), mix_end):
                return False, "coarse_mix_end_mismatch"
            return True, "hit"

        if script == "v4_fine_align.py":
            asset_artifact_value = _argument(command, "--asset-artifact")
            coarse_artifact_value = _argument(command, "--coarse-artifact")
            coarse_value = _argument(command, "--coarse")
            if not asset_artifact_value or not coarse_artifact_value or not coarse_value:
                return False, "fine_command_incomplete"
            try:
                asset_id = str(_load(Path(asset_artifact_value))["artifact_id"])
                coarse_artifact = _load(Path(coarse_artifact_value))
                coarse_id = str(coarse_artifact["artifact_id"])
                coarse_payload = _load(Path(coarse_value))
                occurrence_id = str(coarse_payload["occurrence_id"])
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                return False, "fine_upstream_unreadable"
            artifact, reason = self._artifact_identity(
                artifact_path,
                expected_stage="fine_audio_alignment",
                output_role="fine_alignment",
                output_path=out_path,
                expected_upstream_ids=(asset_id, coarse_id),
            )
            if reason:
                return False, reason
            if not self._resume_sidecar_matches(artifact_path, artifact):
                return False, "runtime_resume_identity_mismatch"
            if str(payload.get("occurrence_id") or "") != occurrence_id:
                return False, "fine_occurrence_mismatch"
            evidence = artifact.get("evidence", {})
            if str(evidence.get("occurrence_id") or "") != occurrence_id:
                return False, "fine_evidence_mismatch"
            return True, "hit"

        asset_artifact_value = _argument(command, "--asset-artifact")
        left_artifact_value = _argument(command, "--left-artifact")
        right_artifact_value = _argument(command, "--right-artifact")
        left_value = _argument(command, "--left-coarse")
        right_value = _argument(command, "--right-coarse")
        if not all(
            (asset_artifact_value, left_artifact_value, right_artifact_value, left_value, right_value)
        ):
            return False, "transition_command_incomplete"
        try:
            asset_id = str(_load(Path(str(asset_artifact_value)))["artifact_id"])
            left_id = str(_load(Path(str(left_artifact_value)))["artifact_id"])
            right_id = str(_load(Path(str(right_artifact_value)))["artifact_id"])
            left_occurrence = str(_load(Path(str(left_value)))["occurrence_id"])
            right_occurrence = str(_load(Path(str(right_value)))["occurrence_id"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return False, "transition_upstream_unreadable"
        artifact, reason = self._artifact_identity(
            artifact_path,
            expected_stage="transition_probe",
            output_role="transition_probe",
            output_path=out_path,
            expected_upstream_ids=(asset_id, left_id, right_id),
        )
        if reason:
            return False, reason
        if not self._resume_sidecar_matches(artifact_path, artifact):
            return False, "runtime_resume_identity_mismatch"
        if str(payload.get("left_occurrence_id") or "") != left_occurrence:
            return False, "transition_left_occurrence_mismatch"
        if str(payload.get("right_occurrence_id") or "") != right_occurrence:
            return False, "transition_right_occurrence_mismatch"
        evidence = artifact.get("evidence", {})
        if str(evidence.get("left_occurrence_id") or "") != left_occurrence:
            return False, "transition_evidence_mismatch"
        if str(evidence.get("right_occurrence_id") or "") != right_occurrence:
            return False, "transition_evidence_mismatch"
        return True, "hit"

    def run(self, command: list[str], *, allow_resume: bool = True) -> str:
        key = tuple(command)
        with self._lock:
            memo = self._memo.get(key)
            if memo is not None:
                self._memo_hits += 1
                return memo

        if allow_resume:
            reusable, reason = self._check_reusable(command)
            if reusable:
                with self._lock:
                    self._resume_hits += 1
                    self._memo[key] = ""
                return ""
            if self.resume_enabled and reason not in {"unsupported_stage", "resume_disabled"}:
                self._record_miss(reason)

        completed = subprocess.run(
            command,
            cwd=self.repository_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"stage failed ({' '.join(command[:2])}): {detail}")
        output = completed.stdout.strip()
        if output:
            print(output, file=sys.stderr)
        self._write_resume_sidecar(command)
        with self._lock:
            self._executed += 1
            self._memo[key] = output
        return output

    def run_many(self, commands: Iterable[list[str]], *, allow_resume: bool = True) -> None:
        command_list = list(commands)
        if not command_list:
            return
        if self.workers == 1 or len(command_list) == 1:
            for command in command_list:
                self.run(command, allow_resume=allow_resume)
            return
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(command_list))) as executor:
            future_map = {
                executor.submit(self.run, command, allow_resume=allow_resume): command
                for command in command_list
            }
            for future in as_completed(future_map):
                command = future_map[future]
                try:
                    future.result()
                except Exception as exc:  # preserve all completed reusable artifacts
                    failures.append(f"{Path(command[1]).name}: {exc}")
        if failures:
            raise RuntimeError("parallel stage failure: " + "; ".join(failures))

    def summary(self) -> StageExecutionSummary:
        with self._lock:
            return StageExecutionSummary(
                resume_enabled=self.resume_enabled,
                workers=self.workers,
                resume_hits=self._resume_hits,
                resume_misses=self._resume_misses,
                executed=self._executed,
                memo_hits=self._memo_hits,
                miss_reasons=dict(self._miss_reasons),
            )
