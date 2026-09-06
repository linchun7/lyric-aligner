"""Batched direct-final-mix execution for production boundary evidence.

The batch executor preserves the existing outer/internal run artifact schemas so the
calibration/adjudication/materialization authority chain does not fork.  It changes
only execution mechanics: one backend process receives many jobs and may mark an
individual job ``unaligned`` without failing unrelated jobs.  Unaligned jobs never
produce a point and therefore cannot vote for a timing mutation.
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

from lyric_aligner.alignment.boundary_executor import BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION
from lyric_aligner.alignment.internal_executor import INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION
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
    segment_units_from_canonical_segments,
)
from lyric_aligner.timeline.boundary_authority import DIRECT_FINAL_MIX_AUDIO_FAMILIES
from lyric_aligner.timeline.boundary_refinement import BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION
from lyric_aligner.timeline.internal_segmentation import INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION


PRODUCTION_ALIGNMENT_BATCH_PROTOCOL_VERSION = "final-mix-alignment-batch-1.0"


class BatchAlignmentExecutionError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _nonnegative_ms(value: Any, *, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BatchAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or number < 0:
        raise BatchAlignmentExecutionError(f"{label} must be finite/nonnegative")
    return int(round(number))


def _probability(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BatchAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise BatchAlignmentExecutionError(f"{label} must be within [0,1]")
    return number


def _resolve_command(command: str) -> list[str]:
    try:
        argv = split_external_command(command)
    except CommandLineParseError as exc:
        raise BatchAlignmentExecutionError("external batch-aligner command cannot be parsed") from exc
    if not argv:
        raise BatchAlignmentExecutionError("external batch-aligner command is empty")
    executable = shutil.which(argv[0])
    if executable is None:
        raise BatchAlignmentExecutionError(f"external batch-aligner executable not found: {argv[0]}")
    return [executable, *argv[1:]]


@dataclass(frozen=True)
class ExternalBatchAlignmentConfig:
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
    timeout_seconds: float = 1800.0

    def validate(self) -> None:
        values = asdict(self)
        values.pop("timeout_seconds", None)
        values.pop("implementation_revision", None)
        values.pop("adapter_contract_revision", None)
        if any(not str(value or "").strip() for value in values.values()):
            raise BatchAlignmentExecutionError("batch alignment config identity is incomplete")
        provenance = (self.implementation_revision, self.adapter_contract_revision)
        if any(provenance) and not all(
            len(str(value or "")) == 64
            and all(ch in "0123456789abcdef" for ch in str(value or ""))
            for value in provenance
        ):
            raise BatchAlignmentExecutionError("batch alignment provenance revisions must be paired lowercase SHA-256 values")
        if self.family not in DIRECT_FINAL_MIX_AUDIO_FAMILIES:
            raise BatchAlignmentExecutionError("batch aligner must be a direct final-mix family")
        if not math.isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise BatchAlignmentExecutionError("timeout_seconds must be finite and > 0")


def _plan_jobs(plan: Mapping[str, Any], *, mode: str) -> list[Mapping[str, Any]]:
    expected_schema = (
        BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION
        if mode == "outer"
        else INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION
    )
    if plan.get("schema_version") != expected_schema:
        raise BatchAlignmentExecutionError("alignment plan schema does not match batch mode")
    raw = plan.get("jobs")
    if not isinstance(raw, list):
        raise BatchAlignmentExecutionError("alignment plan jobs must be a list")
    output: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for job in raw:
        if not isinstance(job, Mapping):
            raise BatchAlignmentExecutionError("alignment job must be an object")
        boundary_id = str(job.get("boundary_id") or "").strip()
        if not boundary_id or boundary_id in seen:
            raise BatchAlignmentExecutionError("alignment boundary IDs must be unique/non-empty")
        seen.add(boundary_id)
        if mode == "outer" and str(job.get("boundary_kind") or "") not in {"start", "end"}:
            raise BatchAlignmentExecutionError("outer batch job must be start or end")
        if mode == "internal" and str(job.get("boundary_kind") or "") != "internal":
            raise BatchAlignmentExecutionError("internal batch job must be internal")
        output.append(job)
    return output


def _prepare_record(
    job: Mapping[str, Any],
    *,
    mode: str,
    language: str,
    mix_duration_ms: int,
) -> dict[str, Any]:
    boundary_id = str(job["boundary_id"])
    try:
        if mode == "outer":
            segments = [alignment_units(language, str(job["canonical_text"]))]
            interval = job.get("cue_interval_ms")
            boundary_index = None
        else:
            segments = segment_units_from_canonical_segments(job["canonical_segments"], language=language)
            interval = job.get("row_interval_ms")
            boundary_index = int(job["boundary_index"])
    except (AlignmentLexicalError, KeyError, TypeError, ValueError) as exc:
        raise BatchAlignmentExecutionError(
            f"canonical lexical preparation failed for {boundary_id}: {exc}"
        ) from exc
    if not isinstance(interval, list) or len(interval) != 2:
        raise BatchAlignmentExecutionError(f"alignment interval is invalid for {boundary_id}")
    start = _nonnegative_ms(interval[0], label="owning interval start")
    end = _nonnegative_ms(interval[1], label="owning interval end")
    if end <= start:
        raise BatchAlignmentExecutionError(f"alignment interval is non-monotonic for {boundary_id}")
    window = full_sequence_alignment_window_ms(
        cue_start_ms=start,
        cue_end_ms=end,
        mix_duration_ms=mix_duration_ms,
    )
    record = {
        "boundary_id": boundary_id,
        "boundary_kind": str(job["boundary_kind"]),
        "cue_number": int(job["cue_number"]),
        "row_interval_ms": [start, end],
        "mix_window_ms": window,
        "segment_lexical_units": segments,
        "lexical_units_sha256": _sha_json(segments),
        "canonical_text_sha256": str(job.get("canonical_text_sha256") or ""),
    }
    if mode == "internal":
        record["boundary_index"] = boundary_index
    return record


def _validate_response_record(
    raw: Mapping[str, Any],
    *,
    request_record: Mapping[str, Any],
    config: ExternalBatchAlignmentConfig,
) -> dict[str, Any]:
    for key in ("boundary_id", "boundary_kind", "lexical_units_sha256"):
        if str(raw.get(key) or "") != str(request_record.get(key) or ""):
            raise BatchAlignmentExecutionError(f"batch response record {key} mismatch")
    if raw.get("mix_window_ms") != request_record.get("mix_window_ms"):
        raise BatchAlignmentExecutionError("batch response record window mismatch")
    status = str(raw.get("status") or "")
    if status == "unaligned":
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise BatchAlignmentExecutionError("unaligned batch record has no reason")
        if raw.get("boundary_ms") not in (None, ""):
            raise BatchAlignmentExecutionError("unaligned batch record must not carry boundary_ms")
        return {
            "status": "unaligned",
            "reason": reason,
            "point": None,
        }
    if status != "aligned":
        raise BatchAlignmentExecutionError("batch response record status is invalid")
    try:
        match_count = int(raw.get("target_match_count", 1))
    except (TypeError, ValueError) as exc:
        raise BatchAlignmentExecutionError("batch response target_match_count is invalid") from exc
    if match_count != 1:
        raise BatchAlignmentExecutionError("batch aligned lexical target must match exactly once")
    boundary_ms = _nonnegative_ms(raw.get("boundary_ms"), label="batch boundary_ms")
    window = [int(value) for value in request_record["mix_window_ms"]]
    if not window[0] <= boundary_ms <= window[1]:
        raise BatchAlignmentExecutionError("batch boundary lies outside requested final-mix window")
    if str(request_record["boundary_kind"]) in {"start", "end"}:
        edge_reason = padded_window_edge_clamp_reason(
            boundary_ms,
            mix_window_ms=request_record["mix_window_ms"],
            row_interval_ms=request_record["row_interval_ms"],
            boundary_kind=str(request_record["boundary_kind"]),
        )
        if edge_reason:
            return {"status": "unaligned", "reason": edge_reason, "point": None}
    uncertainty_ms = _nonnegative_ms(raw.get("uncertainty_ms", 0), label="batch uncertainty_ms")
    raw_confidence = _probability(raw.get("raw_confidence"), label="batch raw_confidence")
    evidence_sha = str(raw.get("evidence_sha256") or "").strip()
    if len(evidence_sha) != 64:
        raise BatchAlignmentExecutionError("batch evidence SHA is invalid")
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
        "lexical_units_sha256": str(request_record["lexical_units_sha256"]),
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
    return {"status": "aligned", "reason": "", "point": point}


def execute_external_alignment_batch(
    *,
    plan: Mapping[str, Any],
    mode: str,
    final_audio_path: str | Path,
    final_audio_sha256: str,
    config: ExternalBatchAlignmentConfig,
    selected_boundary_ids: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if mode not in {"outer", "internal"}:
        raise BatchAlignmentExecutionError("batch alignment mode must be outer or internal")
    config.validate()
    jobs = _plan_jobs(plan, mode=mode)
    plan_audio_sha = str(plan.get("final_audio_sha256") or "")
    if str(final_audio_sha256 or "") != plan_audio_sha:
        raise BatchAlignmentExecutionError("final audio SHA does not match alignment plan")
    audio_path = Path(final_audio_path)
    if not audio_path.is_file() or sha256_file(audio_path) != plan_audio_sha:
        raise BatchAlignmentExecutionError("final mix audio is missing or changed")
    info = sf.info(str(audio_path))
    if info.samplerate <= 0 or info.frames <= 0:
        raise BatchAlignmentExecutionError("final mix audio metadata is invalid")
    mix_duration_ms = int(round(info.frames * 1000.0 / info.samplerate))

    by_id = {str(job["boundary_id"]): job for job in jobs}
    if selected_boundary_ids is not None:
        requested_ids = [str(value or "").strip() for value in selected_boundary_ids]
        if any(not value for value in requested_ids) or len(set(requested_ids)) != len(requested_ids):
            raise BatchAlignmentExecutionError("selected boundary IDs must be unique/non-empty")
        unknown = sorted(set(requested_ids) - set(by_id))
        if unknown:
            raise BatchAlignmentExecutionError("selected boundary is not in plan")
        wanted = set(requested_ids)
        jobs = [job for job in jobs if str(job["boundary_id"]) in wanted]

    schema = BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION if mode == "outer" else INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION
    base = {
        "schema_version": schema,
        "protocol_version": PRODUCTION_ALIGNMENT_BATCH_PROTOCOL_VERSION,
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
        "execution_strategy": "batch",
    }
    if config.implementation_revision and config.adapter_contract_revision:
        base["implementation_revision"] = config.implementation_revision
        base["adapter_contract_revision"] = config.adapter_contract_revision
    if not jobs:
        return {
            **base,
            "command_invoked": False,
            "job_count": 0,
            "aligned_job_count": 0,
            "unaligned_job_count": 0,
            "jobs": [],
        }

    records = [
        _prepare_record(job, mode=mode, language=config.language, mix_duration_ms=mix_duration_ms)
        for job in jobs
    ]
    request = {
        "protocol_version": PRODUCTION_ALIGNMENT_BATCH_PROTOCOL_VERSION,
        "mode": mode,
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
        "final_audio_path": str(audio_path),
        "final_audio_sha256": plan_audio_sha,
        "record_count": len(records),
        "records": records,
    }

    argv = _resolve_command(config.command)
    runner = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="lyric-aligner-production-batch-") as temporary:
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
            raise BatchAlignmentExecutionError("batch aligner timed out") from exc
        except OSError as exc:
            raise BatchAlignmentExecutionError("batch aligner could not start") from exc
        if int(getattr(completed, "returncode", -1)) != 0:
            detail = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", ""))[-3000:]
            raise BatchAlignmentExecutionError(
                f"batch aligner exited nonzero: {getattr(completed, 'returncode', -1)}: {detail}"
            )
        if not response_path.is_file():
            raise BatchAlignmentExecutionError("batch aligner produced no response")
        try:
            payload = json.loads(response_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchAlignmentExecutionError("batch aligner response is invalid JSON") from exc

    if not isinstance(payload, Mapping):
        raise BatchAlignmentExecutionError("batch aligner response must be an object")
    expected_identity = {
        "protocol_version": PRODUCTION_ALIGNMENT_BATCH_PROTOCOL_VERSION,
        "mode": mode,
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
        "final_audio_sha256": plan_audio_sha,
    }
    for key, value in expected_identity.items():
        if str(payload.get(key) or "") != str(value):
            raise BatchAlignmentExecutionError(f"batch aligner response {key} mismatch")
    response_records = payload.get("records")
    if not isinstance(response_records, list) or int(payload.get("record_count") or -1) != len(response_records):
        raise BatchAlignmentExecutionError("batch response record_count mismatch")
    if len(response_records) != len(records):
        raise BatchAlignmentExecutionError("batch response does not cover every requested job")
    response_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in response_records:
        if not isinstance(raw, Mapping):
            raise BatchAlignmentExecutionError("batch response record must be an object")
        boundary_id = str(raw.get("boundary_id") or "").strip()
        if not boundary_id or boundary_id in response_by_id:
            raise BatchAlignmentExecutionError("batch response boundary IDs are invalid/duplicated")
        response_by_id[boundary_id] = raw
    request_by_id = {str(row["boundary_id"]): row for row in records}
    if set(response_by_id) != set(request_by_id):
        raise BatchAlignmentExecutionError("batch response boundary set differs from request")

    results: list[dict[str, Any]] = []
    aligned_count = 0
    unaligned_count = 0
    job_by_id = {str(job["boundary_id"]): job for job in jobs}
    for record in records:
        boundary_id = str(record["boundary_id"])
        normalized = _validate_response_record(
            response_by_id[boundary_id],
            request_record=record,
            config=config,
        )
        if normalized["status"] == "aligned":
            aligned_count += 1
        else:
            unaligned_count += 1
        job = job_by_id[boundary_id]
        results.append(
            {
                "boundary_id": boundary_id,
                "cue_number": int(job["cue_number"]),
                "boundary_kind": str(job["boundary_kind"]),
                "status": normalized["status"],
                "reason": normalized["reason"],
                "point": normalized["point"],
            }
        )
    return {
        **base,
        "command_invoked": True,
        "job_count": len(results),
        "aligned_job_count": aligned_count,
        "unaligned_job_count": unaligned_count,
        "jobs": results,
        "privacy": "canonical text exists only in ephemeral local batch requests; persisted run stores lexical identity, timing, backend provenance, and per-job alignment status",
    }
