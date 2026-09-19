"""Batch external source forced alignment with one subprocess invocation.

P7 protocol v1.0 invokes one process per local lyric job. That is truthful but
inefficient for real CTC/singing models because each process may reload the
same checkpoint. Batch protocol v1.1 sends all selected bounded jobs to one
external process so an adapter can cache/load model state once per batch.

The formal evidence contract remains source-side auxiliary evidence. Raw
canonical text and source paths exist only inside the ephemeral batch request.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from lyric_aligner.alignment.forced_executor import (
    FORCED_ALIGNMENT_PROTOCOL_VERSION,
    FORCED_ALIGNMENT_SCHEMA_VERSION,
    ExternalForcedAlignmentConfig,
    ForcedAlignmentExecutionError,
    _binding_index,
    _normalize_response,
    _sha_text,
    _source_jobs,
    resolve_command,
)
from lyric_aligner.assets.bindings import ResolvedAssetBinding
from lyric_aligner.contracts.artifacts import sha256_file


FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION = "1.1"


def _select_jobs(
    plan: dict[str, Any], selected_job_ids: list[str] | None
) -> list[dict[str, Any]]:
    jobs = _source_jobs(plan)
    if selected_job_ids is None:
        return jobs
    requested = [str(value or "").strip() for value in selected_job_ids]
    if any(not value for value in requested) or len(set(requested)) != len(requested):
        raise ForcedAlignmentExecutionError(
            "selected forced-alignment job IDs must be unique/non-empty"
        )
    by_id = {str(job["job_id"]): job for job in jobs}
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        raise ForcedAlignmentExecutionError(
            "selected forced-alignment jobs are not in the source alignment plan"
        )
    requested_set = set(requested)
    return [job for job in jobs if str(job["job_id"]) in requested_set]


def _prepare_request_job(
    *,
    job: dict[str, Any],
    bindings: dict[str, ResolvedAssetBinding],
    canonical_text_by_job_id: dict[str, str],
) -> tuple[dict[str, Any], ResolvedAssetBinding, str]:
    job_id = str(job["job_id"])
    occurrence_id = str(job.get("occurrence_id") or "")
    binding = bindings.get(occurrence_id)
    if binding is None:
        raise ForcedAlignmentExecutionError(
            f"forced-alignment job {job_id} has no resolved source binding"
        )
    if binding.track_id != str(job.get("track_id") or ""):
        raise ForcedAlignmentExecutionError(
            f"forced-alignment job {job_id} track identity mismatch"
        )
    source_path = Path(binding.source_audio_path)
    if not source_path.is_file():
        raise ForcedAlignmentExecutionError(
            f"source audio is missing for forced-alignment job {job_id}"
        )
    if sha256_file(source_path) != binding.source_audio_sha256:
        raise ForcedAlignmentExecutionError(
            f"source audio hash changed for forced-alignment job {job_id}"
        )
    canonical_text = canonical_text_by_job_id.get(job_id)
    if canonical_text is None:
        raise ForcedAlignmentExecutionError(
            f"canonical text is unavailable for forced-alignment job {job_id}"
        )
    expected_sha = str(job.get("canonical_text_sha256") or "")
    if _sha_text(canonical_text) != expected_sha:
        raise ForcedAlignmentExecutionError(
            f"canonical text identity mismatch for forced-alignment job {job_id}"
        )
    request_job = {
        "job_id": job_id,
        "occurrence_id": occurrence_id,
        "track_id": str(job.get("track_id") or ""),
        "canonical_line_index": int(job["canonical_line_index"]),
        "language_profile": str(job.get("language_profile") or "auto"),
        "source_audio_path": str(source_path),
        "source_audio_sha256": binding.source_audio_sha256,
        "source_window_ms": [int(value) for value in job["source_window_ms"]],
        "canonical_text": canonical_text,
        "canonical_text_sha256": expected_sha,
    }
    return request_job, binding, canonical_text


def _validate_batch_response_identity(
    response: dict[str, Any], *, config: ExternalForcedAlignmentConfig
) -> list[dict[str, Any]]:
    expected = {
        "protocol_version": FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
    }
    for key, value in expected.items():
        if str(response.get(key) or "") != str(value):
            raise ForcedAlignmentExecutionError(
                f"forced-aligner batch response {key} mismatch"
            )
    if response.get("status") != "aligned_batch":
        raise ForcedAlignmentExecutionError(
            "forced-aligner batch response status must be aligned_batch"
        )
    rows = response.get("jobs")
    if not isinstance(rows, list):
        raise ForcedAlignmentExecutionError(
            "forced-aligner batch response jobs must be a list"
        )
    return rows


def execute_external_forced_alignment_batch(
    *,
    plan: dict[str, Any],
    bindings: list[ResolvedAssetBinding],
    canonical_text_by_job_id: dict[str, str],
    config: ExternalForcedAlignmentConfig,
    selected_job_ids: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute all selected bounded source jobs in one external subprocess."""

    config.validate_identity()
    jobs = _select_jobs(plan, selected_job_ids)
    if not jobs:
        return {
            "schema_version": FORCED_ALIGNMENT_SCHEMA_VERSION,
            "backend": "external_forced_aligner",
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "execution_mode": "batch_subprocess",
            "batch_protocol_version": FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION,
            "command_invoked": False,
            "command_invocation_count": 0,
            "job_count": 0,
            "jobs": [],
            "canonical_text_authority": "canonical_lyrics_only",
            "timing_authority": "auxiliary_source_forced_alignment_evidence",
        }

    argv = resolve_command(config.command)
    binding_by_occurrence = _binding_index(bindings)
    prepared: dict[
        str, tuple[dict[str, Any], ResolvedAssetBinding, str, dict[str, Any]]
    ] = {}
    request_jobs: list[dict[str, Any]] = []
    for job in jobs:
        request_job, binding, canonical_text = _prepare_request_job(
            job=job,
            bindings=binding_by_occurrence,
            canonical_text_by_job_id=canonical_text_by_job_id,
        )
        job_id = str(job["job_id"])
        prepared[job_id] = (request_job, binding, canonical_text, job)
        request_jobs.append(request_job)

    request = {
        "protocol_version": FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "jobs": request_jobs,
        "response_contract": {
            "status": "aligned_batch",
            "timebase": "absolute_source_milliseconds",
            "span_offsets": "python_unicode_character_offsets",
            "job_result_status": "aligned",
        },
    }
    runner = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="lyric-aligner-forced-batch-") as temporary:
        temp = Path(temporary)
        request_path = temp / "request.json"
        response_path = temp / "response.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        try:
            completed = runner(
                [
                    *argv,
                    "--batch-request",
                    str(request_path),
                    "--batch-response",
                    str(response_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=config.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch process timed out"
            ) from exc
        except OSError as exc:
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch process could not start"
            ) from exc
        returncode = int(getattr(completed, "returncode", -1))
        if returncode != 0:
            raise ForcedAlignmentExecutionError(
                f"forced-aligner batch process exited nonzero: {returncode}"
            )
        if not response_path.is_file():
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch process produced no response"
            )
        try:
            response = json.loads(response_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch response is invalid JSON"
            ) from exc
        if not isinstance(response, dict):
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch response must be an object"
            )

    response_jobs = _validate_batch_response_identity(response, config=config)
    response_by_id: dict[str, dict[str, Any]] = {}
    for row in response_jobs:
        if not isinstance(row, dict):
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch job response must be an object"
            )
        job_id = str(row.get("job_id") or "").strip()
        if not job_id or job_id in response_by_id:
            raise ForcedAlignmentExecutionError(
                "forced-aligner batch response job IDs must be unique/non-empty"
            )
        response_by_id[job_id] = row
    expected_ids = set(prepared)
    if set(response_by_id) != expected_ids:
        raise ForcedAlignmentExecutionError(
            "forced-aligner batch response job IDs do not exactly match request"
        )

    normalized: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job["job_id"])
        _, binding, canonical_text, original_job = prepared[job_id]
        row = response_by_id[job_id]
        single_response = {
            "protocol_version": FORCED_ALIGNMENT_PROTOCOL_VERSION,
            "job_id": job_id,
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "status": row.get("status"),
            "source_window_ms": row.get("source_window_ms"),
            "line_source_start_ms": row.get("line_source_start_ms"),
            "line_source_end_ms": row.get("line_source_end_ms"),
            "line_confidence": row.get("line_confidence"),
            "spans": row.get("spans"),
        }
        normalized.append(
            _normalize_response(
                single_response,
                job=original_job,
                canonical_text=canonical_text,
                binding=binding,
                config=config,
            )
        )

    return {
        "schema_version": FORCED_ALIGNMENT_SCHEMA_VERSION,
        "backend": "external_forced_aligner",
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "execution_mode": "batch_subprocess",
        "batch_protocol_version": FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION,
        "command_invoked": True,
        "command_invocation_count": 1,
        "job_count": len(normalized),
        "jobs": normalized,
        "canonical_text_authority": "canonical_lyrics_only",
        "timing_authority": "auxiliary_source_forced_alignment_evidence",
        "privacy": "raw canonical text/source paths exist only in one ephemeral local batch request; formal evidence stores hashes/offsets/timing only",
    }
