"""External runner for direct-final-mix internal lyric-boundary alignment.

This execution layer is intentionally non-authoritative.  It verifies exact
backend/model/audio/job identity and returns raw evidence points with
``calibration_passed=False``.  Human-gold calibration must be attached later by
``bind_calibration_to_internal_point`` before an internal boundary can vote.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import soundfile as sf

from lyric_aligner.alignment.window_policy import (
    FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
    full_sequence_alignment_window_ms,
)
from lyric_aligner.command_line import CommandLineParseError, split_external_command
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    AlignmentLexicalError,
    segment_units_from_canonical_segments,
)
from lyric_aligner.timeline.internal_segmentation import (
    DIRECT_INTERNAL_ALIGNMENT_FAMILIES,
    INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION,
)


INTERNAL_ALIGNMENT_PROTOCOL_VERSION = "internal-final-mix-alignment-1.0"
INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION = "internal-alignment-backend-run-1.0"


class InternalAlignmentExecutionError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExternalInternalAlignmentConfig:
    command: str
    family: str
    correlation_group: str
    backend_id: str
    backend_version: str
    model_id: str
    model_revision: str
    language: str
    backend_profile_id: str
    implementation_revision: str = ""
    adapter_contract_revision: str = ""
    window_policy_id: str = FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID
    timeout_seconds: float = 180.0

    def validate(self) -> None:
        values = {
            "command": self.command,
            "family": self.family,
            "correlation_group": self.correlation_group,
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "language": self.language,
            "backend_profile_id": self.backend_profile_id,
            "window_policy_id": self.window_policy_id,
        }
        if any(not str(value or "").strip() for value in values.values()):
            raise InternalAlignmentExecutionError("internal alignment config identity is incomplete")
        provenance = (self.implementation_revision, self.adapter_contract_revision)
        if any(provenance) and not all(
            len(str(value or "")) == 64
            and all(ch in "0123456789abcdef" for ch in str(value or ""))
            for value in provenance
        ):
            raise InternalAlignmentExecutionError("internal alignment provenance revisions must be paired lowercase SHA-256 values")
        if self.family not in DIRECT_INTERNAL_ALIGNMENT_FAMILIES:
            raise InternalAlignmentExecutionError("internal alignment family is not an allowed direct family")
        if not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise InternalAlignmentExecutionError("timeout_seconds must be finite and > 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_command(command: str) -> list[str]:
    try:
        argv = split_external_command(command)
    except CommandLineParseError as exc:
        raise InternalAlignmentExecutionError("external internal-aligner command cannot be parsed") from exc
    if not argv:
        raise InternalAlignmentExecutionError("external internal-aligner command is empty")
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise InternalAlignmentExecutionError(f"external internal-aligner executable not found: {argv[0]}")
    return [resolved, *argv[1:]]


def _nonnegative_ms(value: Any, *, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InternalAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or number < 0:
        raise InternalAlignmentExecutionError(f"{label} must be finite/nonnegative")
    return int(round(number))


def _probability(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InternalAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise InternalAlignmentExecutionError(f"{label} must be within [0,1]")
    return number


def _jobs(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if plan.get("schema_version") != INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION:
        raise InternalAlignmentExecutionError("unsupported internal segmentation plan schema")
    if plan.get("mode") != "plan_only":
        raise InternalAlignmentExecutionError("internal segmentation plan must be plan_only")
    raw = plan.get("jobs")
    if not isinstance(raw, list):
        raise InternalAlignmentExecutionError("internal segmentation plan jobs must be a list")
    seen: set[str] = set()
    output: list[Mapping[str, Any]] = []
    for job in raw:
        if not isinstance(job, Mapping):
            raise InternalAlignmentExecutionError("internal alignment job must be an object")
        boundary_id = str(job.get("boundary_id") or "").strip()
        if not boundary_id or boundary_id in seen:
            raise InternalAlignmentExecutionError("internal boundary IDs must be unique/non-empty")
        seen.add(boundary_id)
        if str(job.get("boundary_kind") or "") != "internal":
            raise InternalAlignmentExecutionError("internal alignment job has wrong boundary_kind")
        window = job.get("mix_window_ms")
        if not isinstance(window, list) or len(window) != 2:
            raise InternalAlignmentExecutionError("internal alignment job has no valid mix_window_ms")
        start = _nonnegative_ms(window[0], label="mix window start")
        end = _nonnegative_ms(window[1], label="mix window end")
        if end <= start:
            raise InternalAlignmentExecutionError("internal alignment mix window is invalid")
        if not str(job.get("canonical_text") or "").strip():
            raise InternalAlignmentExecutionError("internal alignment job has no canonical text")
        output.append(job)
    return output


def _validate_response(
    payload: Mapping[str, Any],
    *,
    job: Mapping[str, Any],
    config: ExternalInternalAlignmentConfig,
    lexical_units_sha256: str,
    requested_window_ms: list[int],
) -> dict[str, Any]:
    boundary_id = str(job["boundary_id"])
    expected = {
        "protocol_version": INTERNAL_ALIGNMENT_PROTOCOL_VERSION,
        "boundary_id": boundary_id,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "backend_profile_id": config.backend_profile_id,
        "family": config.family,
        "correlation_group": config.correlation_group,
        "audio_basis": "final_mix",
        "language": config.language,
        "window_policy_id": config.window_policy_id,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "lexical_units_sha256": lexical_units_sha256,
    }
    for key, expected_value in expected.items():
        if str(payload.get(key) or "") != str(expected_value):
            raise InternalAlignmentExecutionError(f"internal-aligner response {key} mismatch")
    if payload.get("status") != "aligned":
        raise InternalAlignmentExecutionError("internal-aligner did not return aligned status")
    if payload.get("mix_window_ms") != [int(value) for value in requested_window_ms]:
        raise InternalAlignmentExecutionError("internal-aligner response window mismatch")
    try:
        match_count = int(payload.get("target_match_count"))
    except (TypeError, ValueError) as exc:
        raise InternalAlignmentExecutionError("internal-aligner target_match_count is invalid") from exc
    if match_count != 1:
        raise InternalAlignmentExecutionError("internal boundary target must match exactly once")

    boundary_ms = _nonnegative_ms(payload.get("boundary_ms"), label="internal boundary_ms")
    window_start, window_end = [int(value) for value in requested_window_ms]
    row_start, row_end = [int(value) for value in job["row_interval_ms"]]
    if not window_start <= boundary_ms <= window_end:
        raise InternalAlignmentExecutionError("internal boundary lies outside requested mix window")
    if not row_start < boundary_ms < row_end:
        raise InternalAlignmentExecutionError("internal boundary lies outside owning cue")
    uncertainty_ms = _nonnegative_ms(payload.get("uncertainty_ms", 0), label="internal uncertainty_ms")
    raw_confidence = _probability(payload.get("raw_confidence"), label="raw_confidence")
    point = {
        "family": config.family,
        "correlation_group": config.correlation_group,
        "boundary_ms": boundary_ms,
        "confidence": 0.0 if raw_confidence is None else raw_confidence,
        "raw_confidence": raw_confidence,
        "uncertainty_ms": uncertainty_ms,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "backend_profile_id": config.backend_profile_id,
        "window_policy_id": config.window_policy_id,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "lexical_units_sha256": lexical_units_sha256,
        "audio_basis": "final_mix",
        "language": config.language,
        "evidence_sha256": str(payload.get("evidence_sha256") or ""),
        "calibration_passed": False,
        "calibration_sha256": "",
    }
    if config.implementation_revision and config.adapter_contract_revision:
        point["implementation_revision"] = config.implementation_revision
        point["adapter_contract_revision"] = config.adapter_contract_revision
    return point


def execute_external_internal_alignment_jobs(
    *,
    plan: Mapping[str, Any],
    final_audio_path: str | Path,
    final_audio_sha256: str,
    config: ExternalInternalAlignmentConfig,
    selected_boundary_ids: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run internal-boundary jobs through an external direct-final-mix aligner."""

    config.validate()
    jobs = _jobs(plan)
    plan_audio_sha = str(plan.get("final_audio_sha256") or "")
    if str(final_audio_sha256 or "") != plan_audio_sha:
        raise InternalAlignmentExecutionError("final audio SHA does not match internal plan")
    audio_path = Path(final_audio_path)
    if not audio_path.is_file():
        raise InternalAlignmentExecutionError("final mix audio is missing")
    if sha256_file(audio_path) != plan_audio_sha:
        raise InternalAlignmentExecutionError("final mix audio hash changed")
    audio_info = sf.info(str(audio_path))
    if audio_info.samplerate <= 0 or audio_info.frames <= 0:
        raise InternalAlignmentExecutionError("final mix audio metadata is invalid")
    mix_duration_ms = int(round(audio_info.frames * 1000.0 / audio_info.samplerate))

    by_id = {str(job["boundary_id"]): job for job in jobs}
    if selected_boundary_ids is not None:
        requested = [str(value or "").strip() for value in selected_boundary_ids]
        if any(not value for value in requested) or len(set(requested)) != len(requested):
            raise InternalAlignmentExecutionError("selected internal boundary IDs must be unique/non-empty")
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise InternalAlignmentExecutionError("selected internal boundary is not in plan")
        wanted = set(requested)
        jobs = [job for job in jobs if str(job["boundary_id"]) in wanted]

    if not jobs:
        return {
            "schema_version": INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION,
            "protocol_version": INTERNAL_ALIGNMENT_PROTOCOL_VERSION,
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "backend_profile_id": config.backend_profile_id,
            "family": config.family,
            "correlation_group": config.correlation_group,
            "language": config.language,
            "window_policy_id": config.window_policy_id,
            "final_audio_sha256": plan_audio_sha,
            "plan_sha256": _sha_json(plan),
            "command_invoked": False,
            "job_count": 0,
            "jobs": [],
            "timing_authority": "diagnostic_uncalibrated_direct_final_mix_evidence",
        }

    argv = _resolve_command(config.command)
    runner = runner or subprocess.run
    results: list[dict[str, Any]] = []
    for job in jobs:
        boundary_id = str(job["boundary_id"])
        try:
            segment_lexical_units = segment_units_from_canonical_segments(
                job["canonical_segments"], language=config.language
            )
        except AlignmentLexicalError as exc:
            raise InternalAlignmentExecutionError(
                f"canonical lexical preparation failed for {boundary_id}: {exc}"
            ) from exc
        lexical_units_sha256 = _sha_json(segment_lexical_units)
        row_start, row_end = [int(value) for value in job["row_interval_ms"]]
        requested_window_ms = full_sequence_alignment_window_ms(
            cue_start_ms=row_start,
            cue_end_ms=row_end,
            mix_duration_ms=mix_duration_ms,
        )
        request = {
            "protocol_version": INTERNAL_ALIGNMENT_PROTOCOL_VERSION,
            "boundary_id": boundary_id,
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "backend_profile_id": config.backend_profile_id,
            "window_policy_id": config.window_policy_id,
            "audio_basis": "final_mix",
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            "lexical_units_sha256": lexical_units_sha256,
            "segment_lexical_units": segment_lexical_units,
            "family": config.family,
            "correlation_group": config.correlation_group,
            "language": config.language,
            "final_audio_path": str(audio_path),
            "final_audio_sha256": plan_audio_sha,
            "mix_window_ms": requested_window_ms,
            "row_interval_ms": [row_start, row_end],
            "boundary_index": int(job["boundary_index"]),
            "canonical_text": str(job["canonical_text"]),
            "canonical_segments": [dict(segment) for segment in job["canonical_segments"]],
            "left_segment": dict(job["left_segment"]),
            "right_segment": dict(job["right_segment"]),
            "response_contract": {
                "timebase": "absolute_final_mix_milliseconds",
                "target_match_count": 1,
                "status": "aligned",
            },
        }
        with tempfile.TemporaryDirectory(prefix="lyric-aligner-internal-") as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            response_path = root / "response.json"
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            try:
                completed = runner(
                    [*argv, "--request", str(request_path), "--response", str(response_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=config.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise InternalAlignmentExecutionError(f"internal-aligner timed out for {boundary_id}") from exc
            except OSError as exc:
                raise InternalAlignmentExecutionError(f"internal-aligner could not start for {boundary_id}") from exc
            if int(getattr(completed, "returncode", -1)) != 0:
                raise InternalAlignmentExecutionError(
                    f"internal-aligner exited nonzero for {boundary_id}: {getattr(completed, 'returncode', -1)}"
                )
            if not response_path.is_file():
                raise InternalAlignmentExecutionError("internal-aligner produced no response")
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise InternalAlignmentExecutionError("internal-aligner response is invalid JSON") from exc
            if not isinstance(payload, Mapping):
                raise InternalAlignmentExecutionError("internal-aligner response must be an object")
            point = _validate_response(
                payload,
                job=job,
                config=config,
                lexical_units_sha256=lexical_units_sha256,
                requested_window_ms=requested_window_ms,
            )
            results.append(
                {
                    "boundary_id": boundary_id,
                    "cue_number": int(job["cue_number"]),
                    "boundary_index": int(job["boundary_index"]),
                    "point": point,
                }
            )

    run = {
        "schema_version": INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION,
        "protocol_version": INTERNAL_ALIGNMENT_PROTOCOL_VERSION,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "backend_profile_id": config.backend_profile_id,
        "family": config.family,
        "correlation_group": config.correlation_group,
        "language": config.language,
        "window_policy_id": config.window_policy_id,
        "final_audio_sha256": plan_audio_sha,
        "plan_sha256": _sha_json(plan),
        "command_invoked": True,
        "job_count": len(results),
        "jobs": results,
        "timing_authority": "diagnostic_uncalibrated_direct_final_mix_evidence",
        "privacy": "canonical text exists only in ephemeral local request files; persisted run stores timing and backend identity",
    }
    if config.implementation_revision and config.adapter_contract_revision:
        run["implementation_revision"] = config.implementation_revision
        run["adapter_contract_revision"] = config.adapter_contract_revision
    return run
