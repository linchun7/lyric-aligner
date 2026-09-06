"""External direct-final-mix executor for subtitle cue start/end boundaries.

The executor is deliberately non-authoritative.  It derives a deterministic
full-sequence alignment window from the owning editor cue, verifies the final-mix
SHA and backend/profile identity, and returns raw evidence with calibration disabled.
Only the boundary-refinement authority layer may later attach a matching production
human-gold calibration artifact and decide whether timing may change.
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
from lyric_aligner.audio.forced_alignment import padded_window_edge_clamp_reason
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    AlignmentLexicalError,
    alignment_units,
)
from lyric_aligner.timeline.boundary_refinement import BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION
from lyric_aligner.timeline.boundary_authority import DIRECT_FINAL_MIX_AUDIO_FAMILIES


BOUNDARY_ALIGNMENT_PROTOCOL_VERSION = "final-mix-boundary-alignment-1.0"
BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION = "boundary-alignment-backend-run-1.0"


class BoundaryAlignmentExecutionError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExternalBoundaryAlignmentConfig:
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
        values = asdict(self)
        values.pop("timeout_seconds", None)
        values.pop("implementation_revision", None)
        values.pop("adapter_contract_revision", None)
        if any(not str(value or "").strip() for value in values.values()):
            raise BoundaryAlignmentExecutionError("boundary alignment config identity is incomplete")
        provenance = (self.implementation_revision, self.adapter_contract_revision)
        if any(provenance) and not all(
            len(str(value or "")) == 64
            and all(ch in "0123456789abcdef" for ch in str(value or ""))
            for value in provenance
        ):
            raise BoundaryAlignmentExecutionError("boundary alignment provenance revisions must be paired lowercase SHA-256 values")
        if self.family not in DIRECT_FINAL_MIX_AUDIO_FAMILIES:
            raise BoundaryAlignmentExecutionError("outer boundary aligner must be a direct final-mix family")
        if not math.isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise BoundaryAlignmentExecutionError("timeout_seconds must be finite and > 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_command(command: str) -> list[str]:
    try:
        argv = split_external_command(command)
    except CommandLineParseError as exc:
        raise BoundaryAlignmentExecutionError("external boundary-aligner command cannot be parsed") from exc
    if not argv:
        raise BoundaryAlignmentExecutionError("external boundary-aligner command is empty")
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise BoundaryAlignmentExecutionError(f"external boundary-aligner executable not found: {argv[0]}")
    return [resolved, *argv[1:]]


def _nonnegative_ms(value: Any, *, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or number < 0:
        raise BoundaryAlignmentExecutionError(f"{label} must be finite/nonnegative")
    return int(round(number))


def _probability(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BoundaryAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise BoundaryAlignmentExecutionError(f"{label} must be within [0,1]")
    return number


def _jobs(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if plan.get("schema_version") != BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION:
        raise BoundaryAlignmentExecutionError("unsupported boundary refinement plan schema")
    if plan.get("mode") != "plan_only" or plan.get("backend_execution_performed") is not False:
        raise BoundaryAlignmentExecutionError("boundary refinement plan is not an unexecuted plan")
    raw = plan.get("jobs")
    if not isinstance(raw, list):
        raise BoundaryAlignmentExecutionError("boundary refinement plan jobs must be a list")
    output: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for job in raw:
        if not isinstance(job, Mapping):
            raise BoundaryAlignmentExecutionError("boundary alignment job must be an object")
        boundary_id = str(job.get("boundary_id") or "").strip()
        if not boundary_id or boundary_id in seen:
            raise BoundaryAlignmentExecutionError("boundary IDs must be unique/non-empty")
        seen.add(boundary_id)
        if str(job.get("boundary_kind") or "") not in {"start", "end"}:
            raise BoundaryAlignmentExecutionError("outer boundary job must be start or end")
        cue_interval = job.get("cue_interval_ms")
        if not isinstance(cue_interval, list) or len(cue_interval) != 2:
            raise BoundaryAlignmentExecutionError("outer boundary job has no valid cue_interval_ms")
        start = _nonnegative_ms(cue_interval[0], label="cue start")
        end = _nonnegative_ms(cue_interval[1], label="cue end")
        if end <= start:
            raise BoundaryAlignmentExecutionError("outer boundary owning cue interval is invalid")
        if not str(job.get("canonical_text") or "").strip():
            raise BoundaryAlignmentExecutionError("outer boundary job has no canonical text")
        output.append(job)
    return output


def _validate_response(
    payload: Mapping[str, Any],
    *,
    job: Mapping[str, Any],
    config: ExternalBoundaryAlignmentConfig,
    lexical_units_sha256: str,
    requested_window_ms: list[int],
) -> dict[str, Any]:
    expected = {
        "protocol_version": BOUNDARY_ALIGNMENT_PROTOCOL_VERSION,
        "boundary_id": str(job["boundary_id"]),
        "boundary_kind": str(job["boundary_kind"]),
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
    for key, value in expected.items():
        if str(payload.get(key) or "") != str(value):
            raise BoundaryAlignmentExecutionError(f"boundary-aligner response {key} mismatch")
    if payload.get("status") != "aligned":
        raise BoundaryAlignmentExecutionError("boundary-aligner did not return aligned status")
    if payload.get("mix_window_ms") != requested_window_ms:
        raise BoundaryAlignmentExecutionError("boundary-aligner response window mismatch")
    try:
        match_count = int(payload.get("target_match_count"))
    except (TypeError, ValueError) as exc:
        raise BoundaryAlignmentExecutionError("boundary-aligner target_match_count is invalid") from exc
    if match_count != 1:
        raise BoundaryAlignmentExecutionError("outer boundary lexical target must match exactly once")
    boundary_ms = _nonnegative_ms(payload.get("boundary_ms"), label="boundary_ms")
    if not requested_window_ms[0] <= boundary_ms <= requested_window_ms[1]:
        raise BoundaryAlignmentExecutionError("outer boundary lies outside requested final-mix window")
    edge_reason = padded_window_edge_clamp_reason(
        boundary_ms,
        mix_window_ms=requested_window_ms,
        row_interval_ms=job["cue_interval_ms"],
        boundary_kind=str(job["boundary_kind"]),
    )
    if edge_reason:
        raise BoundaryAlignmentExecutionError("boundary-aligner prediction rejected: " + edge_reason)
    uncertainty_ms = _nonnegative_ms(payload.get("uncertainty_ms", 0), label="uncertainty_ms")
    raw_confidence = _probability(payload.get("raw_confidence"), label="raw_confidence")
    evidence_sha = str(payload.get("evidence_sha256") or "").strip()
    if len(evidence_sha) != 64:
        raise BoundaryAlignmentExecutionError("boundary-aligner evidence SHA is invalid")
    point = {
        "family": config.family,
        "correlation_group": config.correlation_group,
        "boundary_ms": boundary_ms,
        "confidence": 0.0 if raw_confidence is None else raw_confidence,
        "raw_confidence": raw_confidence,
        "uncertainty_ms": uncertainty_ms,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "backend_profile_id": config.backend_profile_id,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "lexical_units_sha256": lexical_units_sha256,
        "audio_basis": "final_mix",
        "language": config.language,
        "window_policy_id": config.window_policy_id,
        "evidence_sha256": evidence_sha,
        "calibration_passed": False,
        "calibration_sha256": "",
    }
    if config.implementation_revision and config.adapter_contract_revision:
        point["implementation_revision"] = config.implementation_revision
        point["adapter_contract_revision"] = config.adapter_contract_revision
    return point


def execute_external_boundary_alignment_jobs(
    *,
    plan: Mapping[str, Any],
    final_audio_path: str | Path,
    final_audio_sha256: str,
    config: ExternalBoundaryAlignmentConfig,
    selected_boundary_ids: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    config.validate()
    jobs = _jobs(plan)
    plan_audio_sha = str(plan.get("final_audio_sha256") or "")
    if str(final_audio_sha256 or "") != plan_audio_sha:
        raise BoundaryAlignmentExecutionError("final audio SHA does not match boundary plan")
    audio_path = Path(final_audio_path)
    if not audio_path.is_file() or sha256_file(audio_path) != plan_audio_sha:
        raise BoundaryAlignmentExecutionError("final mix audio is missing or changed")
    info = sf.info(str(audio_path))
    if info.samplerate <= 0 or info.frames <= 0:
        raise BoundaryAlignmentExecutionError("final mix audio metadata is invalid")
    mix_duration_ms = int(round(info.frames * 1000.0 / info.samplerate))

    by_id = {str(job["boundary_id"]): job for job in jobs}
    if selected_boundary_ids is not None:
        requested = [str(value or "").strip() for value in selected_boundary_ids]
        if any(not value for value in requested) or len(set(requested)) != len(requested):
            raise BoundaryAlignmentExecutionError("selected boundary IDs must be unique/non-empty")
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise BoundaryAlignmentExecutionError("selected boundary is not in plan")
        wanted = set(requested)
        jobs = [job for job in jobs if str(job["boundary_id"]) in wanted]

    base = {
        "schema_version": BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION,
        "protocol_version": BOUNDARY_ALIGNMENT_PROTOCOL_VERSION,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "backend_profile_id": config.backend_profile_id,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "family": config.family,
        "correlation_group": config.correlation_group,
        "language": config.language,
        "window_policy_id": config.window_policy_id,
        "final_audio_sha256": plan_audio_sha,
        "plan_sha256": _sha_json(plan),
        "timing_authority": "diagnostic_uncalibrated_direct_final_mix_evidence",
    }
    if config.implementation_revision and config.adapter_contract_revision:
        base["implementation_revision"] = config.implementation_revision
        base["adapter_contract_revision"] = config.adapter_contract_revision
    if not jobs:
        return {**base, "command_invoked": False, "job_count": 0, "jobs": []}

    argv = _resolve_command(config.command)
    runner = runner or subprocess.run
    results: list[dict[str, Any]] = []
    for job in jobs:
        boundary_id = str(job["boundary_id"])
        try:
            lexical_units = alignment_units(config.language, str(job["canonical_text"]))
        except AlignmentLexicalError as exc:
            raise BoundaryAlignmentExecutionError(
                f"canonical lexical preparation failed for {boundary_id}: {exc}"
            ) from exc
        segment_lexical_units = [lexical_units]
        lexical_units_sha256 = _sha_json(segment_lexical_units)
        cue_start, cue_end = [int(value) for value in job["cue_interval_ms"]]
        requested_window_ms = full_sequence_alignment_window_ms(
            cue_start_ms=cue_start,
            cue_end_ms=cue_end,
            mix_duration_ms=mix_duration_ms,
        )
        request = {
            "protocol_version": BOUNDARY_ALIGNMENT_PROTOCOL_VERSION,
            "boundary_id": boundary_id,
            "boundary_kind": str(job["boundary_kind"]),
            "cue_number": int(job["cue_number"]),
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "backend_profile_id": config.backend_profile_id,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "family": config.family,
            "correlation_group": config.correlation_group,
            "audio_basis": "final_mix",
            "language": config.language,
            "window_policy_id": config.window_policy_id,
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            "lexical_units_sha256": lexical_units_sha256,
            "segment_lexical_units": segment_lexical_units,
            "final_audio_path": str(audio_path),
            "final_audio_sha256": plan_audio_sha,
            "mix_window_ms": requested_window_ms,
            "cue_interval_ms": [cue_start, cue_end],
            "canonical_text_sha256": str(job["canonical_text_sha256"]),
            "response_contract": {
                "timebase": "absolute_final_mix_milliseconds",
                "target_match_count": 1,
                "status": "aligned",
            },
        }
        with tempfile.TemporaryDirectory(prefix="lyric-aligner-boundary-") as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            response_path = root / "response.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            try:
                completed = runner(
                    [*argv, "--request", str(request_path), "--response", str(response_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=config.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise BoundaryAlignmentExecutionError(f"boundary-aligner timed out for {boundary_id}") from exc
            except OSError as exc:
                raise BoundaryAlignmentExecutionError(f"boundary-aligner could not start for {boundary_id}") from exc
            if int(getattr(completed, "returncode", -1)) != 0:
                raise BoundaryAlignmentExecutionError(
                    f"boundary-aligner exited nonzero for {boundary_id}: {getattr(completed, 'returncode', -1)}"
                )
            if not response_path.is_file():
                raise BoundaryAlignmentExecutionError("boundary-aligner produced no response")
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BoundaryAlignmentExecutionError("boundary-aligner response is invalid JSON") from exc
            if not isinstance(payload, Mapping):
                raise BoundaryAlignmentExecutionError("boundary-aligner response must be an object")
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
                    "boundary_kind": str(job["boundary_kind"]),
                    "point": point,
                }
            )
    return {
        **base,
        "command_invoked": True,
        "job_count": len(results),
        "jobs": results,
        "privacy": "canonical text exists only in ephemeral local request files; persisted run stores lexical identity, timing, and backend provenance",
    }
