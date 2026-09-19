"""Execute source-side forced alignment through an explicit external JSON protocol.

The adapter is backend-neutral: WhisperX, SOFA, MFA-like tools, or another
aligner may implement the command protocol. The wrapper never treats ASR text as
canonical truth and never fabricates a result when the configured executable,
model identity, source audio, or response contract is unavailable.
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
from typing import Any, Callable

from lyric_aligner.assets.bindings import ResolvedAssetBinding
from lyric_aligner.command_line import CommandLineParseError, split_external_command
from lyric_aligner.contracts.artifacts import sha256_file


FORCED_ALIGNMENT_SCHEMA_VERSION = "1.0"
FORCED_ALIGNMENT_PROTOCOL_VERSION = "1.0"


class ForcedAlignmentExecutionError(ValueError):
    """Raised when source forced alignment cannot be executed safely."""


@dataclass(frozen=True)
class ExternalForcedAlignmentConfig:
    command: str
    backend_id: str
    backend_version: str
    model_id: str
    model_revision: str
    timeout_seconds: float = 120.0

    def validate_identity(self) -> None:
        for label, value in (
            ("command", self.command),
            ("backend_id", self.backend_id),
            ("backend_version", self.backend_version),
            ("model_id", self.model_id),
            ("model_revision", self.model_revision),
        ):
            if not str(value or "").strip():
                raise ForcedAlignmentExecutionError(f"{label} must be non-empty")
        if not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ForcedAlignmentExecutionError("timeout_seconds must be finite and > 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def command_argv(command: str) -> list[str]:
    """Split a configured command without invoking a shell."""

    try:
        return split_external_command(command)
    except CommandLineParseError as exc:
        raise ForcedAlignmentExecutionError("external command cannot be parsed") from exc


def resolve_command(command: str) -> list[str]:
    argv = command_argv(command)
    if not argv:
        raise ForcedAlignmentExecutionError("external forced-aligner command is empty")
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise ForcedAlignmentExecutionError(
            f"external forced-aligner executable not found: {argv[0]}"
        )
    return [resolved, *argv[1:]]


def _sha_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _finite_ms(value: Any, *, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForcedAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise ForcedAlignmentExecutionError(f"{label} must be finite")
    return int(round(number))


def _probability(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForcedAlignmentExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ForcedAlignmentExecutionError(f"{label} must be within [0,1]")
    return number


def _source_jobs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.get("mode") != "plan_only":
        raise ForcedAlignmentExecutionError("alignment plan must be plan_only")
    if plan.get("backend_execution_performed") is not False:
        raise ForcedAlignmentExecutionError("alignment plan already reports execution")
    raw = plan.get("jobs")
    if not isinstance(raw, list):
        raise ForcedAlignmentExecutionError("alignment plan jobs must be a list")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise ForcedAlignmentExecutionError("alignment plan job must be an object")
        if "source_forced_alignment" not in (row.get("requested_capabilities") or []):
            continue
        job_id = str(row.get("job_id") or "").strip()
        if not job_id or job_id in seen:
            raise ForcedAlignmentExecutionError(
                "source forced-alignment job IDs must be unique/non-empty"
            )
        seen.add(job_id)
        window = row.get("source_window_ms")
        if not isinstance(window, list) or len(window) != 2:
            raise ForcedAlignmentExecutionError(
                f"forced-alignment job {job_id} has no valid source_window_ms"
            )
        start = _finite_ms(window[0], label="source window start")
        end = _finite_ms(window[1], label="source window end")
        if start < 0 or end <= start:
            raise ForcedAlignmentExecutionError(
                f"forced-alignment job {job_id} source window is invalid"
            )
        if row.get("canonical_line_index") is None:
            raise ForcedAlignmentExecutionError(
                f"forced-alignment job {job_id} has no canonical line identity"
            )
        if not str(row.get("canonical_text_sha256") or "").strip():
            raise ForcedAlignmentExecutionError(
                f"forced-alignment job {job_id} has no canonical text SHA"
            )
        jobs.append(row)
    return jobs


def _binding_index(
    bindings: list[ResolvedAssetBinding],
) -> dict[str, ResolvedAssetBinding]:
    output: dict[str, ResolvedAssetBinding] = {}
    for binding in bindings:
        if binding.occurrence_id in output:
            raise ForcedAlignmentExecutionError(
                f"duplicate asset binding occurrence {binding.occurrence_id}"
            )
        output[binding.occurrence_id] = binding
    return output


def _validate_response_identity(
    payload: dict[str, Any],
    *,
    job_id: str,
    config: ExternalForcedAlignmentConfig,
    source_window_ms: list[int],
) -> None:
    expected = {
        "protocol_version": FORCED_ALIGNMENT_PROTOCOL_VERSION,
        "job_id": job_id,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
    }
    for key, value in expected.items():
        if str(payload.get(key) or "") != str(value):
            raise ForcedAlignmentExecutionError(
                f"forced-aligner response {key} mismatch for job {job_id}"
            )
    if payload.get("status") != "aligned":
        raise ForcedAlignmentExecutionError(
            f"forced-aligner did not return aligned status for job {job_id}"
        )
    response_window = payload.get("source_window_ms")
    if response_window != source_window_ms:
        raise ForcedAlignmentExecutionError(
            f"forced-aligner response source window mismatch for job {job_id}"
        )


def _validate_spans(
    raw_spans: Any,
    *,
    canonical_text: str,
    source_window_ms: list[int],
) -> list[dict[str, Any]]:
    if raw_spans is None:
        return []
    if not isinstance(raw_spans, list):
        raise ForcedAlignmentExecutionError("forced-aligner spans must be a list")
    output: list[dict[str, Any]] = []
    previous_char_end = 0
    previous_time_end = source_window_ms[0]
    for index, span in enumerate(raw_spans):
        if not isinstance(span, dict):
            raise ForcedAlignmentExecutionError("forced-aligner span must be an object")
        try:
            char_start = int(span["char_start"])
            char_end = int(span["char_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ForcedAlignmentExecutionError(
                f"forced-aligner span {index} has invalid character offsets"
            ) from exc
        if not 0 <= char_start < char_end <= len(canonical_text):
            raise ForcedAlignmentExecutionError(
                f"forced-aligner span {index} character range is outside canonical text"
            )
        if char_start < previous_char_end:
            raise ForcedAlignmentExecutionError(
                "forced-aligner character spans must be monotonic/non-overlapping"
            )
        start_ms = _finite_ms(span.get("source_start_ms"), label="span source_start_ms")
        end_ms = _finite_ms(span.get("source_end_ms"), label="span source_end_ms")
        if not (
            source_window_ms[0] <= start_ms < end_ms <= source_window_ms[1]
        ):
            raise ForcedAlignmentExecutionError(
                f"forced-aligner span {index} is outside source window"
            )
        if start_ms < previous_time_end:
            raise ForcedAlignmentExecutionError(
                "forced-aligner time spans must be monotonic/non-overlapping"
            )
        fragment = canonical_text[char_start:char_end]
        output.append(
            {
                "span_index": index,
                "char_start": char_start,
                "char_end": char_end,
                "canonical_fragment_sha256": _sha_text(fragment),
                "source_start_ms": start_ms,
                "source_end_ms": end_ms,
                "confidence": _probability(
                    span.get("confidence"), label="span confidence"
                ),
            }
        )
        previous_char_end = char_end
        previous_time_end = end_ms
    return output


def _normalize_response(
    payload: dict[str, Any],
    *,
    job: dict[str, Any],
    canonical_text: str,
    binding: ResolvedAssetBinding,
    config: ExternalForcedAlignmentConfig,
) -> dict[str, Any]:
    job_id = str(job["job_id"])
    source_window_ms = [int(value) for value in job["source_window_ms"]]
    _validate_response_identity(
        payload,
        job_id=job_id,
        config=config,
        source_window_ms=source_window_ms,
    )
    line_start_ms = _finite_ms(
        payload.get("line_source_start_ms"), label="line_source_start_ms"
    )
    line_end_ms = _finite_ms(
        payload.get("line_source_end_ms"), label="line_source_end_ms"
    )
    if not (
        source_window_ms[0] <= line_start_ms < line_end_ms <= source_window_ms[1]
    ):
        raise ForcedAlignmentExecutionError(
            f"forced-aligner line boundary is outside source window for job {job_id}"
        )
    spans = _validate_spans(
        payload.get("spans"),
        canonical_text=canonical_text,
        source_window_ms=source_window_ms,
    )
    return {
        "job_id": job_id,
        "occurrence_id": str(job.get("occurrence_id") or ""),
        "track_id": str(job.get("track_id") or ""),
        "ordinal": int(job.get("ordinal", -1)),
        "canonical_line_index": int(job["canonical_line_index"]),
        "canonical_text_sha256": _sha_text(canonical_text),
        "source_window_ms": source_window_ms,
        "source_audio_sha256": binding.source_audio_sha256,
        "line_source_start_ms": line_start_ms,
        "line_source_end_ms": line_end_ms,
        "line_confidence": _probability(
            payload.get("line_confidence"), label="line confidence"
        ),
        "span_count": len(spans),
        "spans": spans,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
    }


def execute_external_forced_alignment_jobs(
    *,
    plan: dict[str, Any],
    bindings: list[ResolvedAssetBinding],
    canonical_text_by_job_id: dict[str, str],
    config: ExternalForcedAlignmentConfig,
    selected_job_ids: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run exact source-side jobs through an external forced-aligner command."""

    config.validate_identity()
    jobs = _source_jobs(plan)
    by_id = {str(job["job_id"]): job for job in jobs}
    if selected_job_ids is not None:
        requested = [str(value or "").strip() for value in selected_job_ids]
        if any(not value for value in requested) or len(set(requested)) != len(requested):
            raise ForcedAlignmentExecutionError(
                "selected forced-alignment job IDs must be unique/non-empty"
            )
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise ForcedAlignmentExecutionError(
                "selected forced-alignment jobs are not in the source alignment plan"
            )
        jobs = [job for job in jobs if str(job["job_id"]) in set(requested)]

    # Explicit empty selection/plan: no executable or model runtime is required.
    if not jobs:
        return {
            "schema_version": FORCED_ALIGNMENT_SCHEMA_VERSION,
            "backend": "external_forced_aligner",
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "command_invoked": False,
            "job_count": 0,
            "jobs": [],
            "canonical_text_authority": "canonical_lyrics_only",
            "timing_authority": "auxiliary_source_forced_alignment_evidence",
        }

    argv = resolve_command(config.command)
    binding_by_occurrence = _binding_index(bindings)
    runner = runner or subprocess.run
    results: list[dict[str, Any]] = []

    for job in jobs:
        job_id = str(job["job_id"])
        occurrence_id = str(job.get("occurrence_id") or "")
        binding = binding_by_occurrence.get(occurrence_id)
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

        request = {
            "protocol_version": FORCED_ALIGNMENT_PROTOCOL_VERSION,
            "job_id": job_id,
            "backend_id": config.backend_id,
            "backend_version": config.backend_version,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "language_profile": str(job.get("language_profile") or "auto"),
            "source_audio_path": str(source_path),
            "source_audio_sha256": binding.source_audio_sha256,
            "source_window_ms": [int(value) for value in job["source_window_ms"]],
            "canonical_text": canonical_text,
            "canonical_text_sha256": expected_sha,
            "response_contract": {
                "timebase": "absolute_source_milliseconds",
                "span_offsets": "python_unicode_character_offsets",
                "status": "aligned",
            },
        }
        with tempfile.TemporaryDirectory(prefix="lyric-aligner-forced-") as temporary:
            temp = Path(temporary)
            request_path = temp / "request.json"
            response_path = temp / "response.json"
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
                raise ForcedAlignmentExecutionError(
                    f"forced-aligner timed out for job {job_id}"
                ) from exc
            except OSError as exc:
                raise ForcedAlignmentExecutionError(
                    f"forced-aligner could not start for job {job_id}"
                ) from exc
            returncode = int(getattr(completed, "returncode", -1))
            if returncode != 0:
                raise ForcedAlignmentExecutionError(
                    f"forced-aligner exited nonzero for job {job_id}: {returncode}"
                )
            if not response_path.is_file():
                raise ForcedAlignmentExecutionError(
                    f"forced-aligner produced no response for job {job_id}"
                )
            try:
                response = json.loads(response_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ForcedAlignmentExecutionError(
                    f"forced-aligner response is invalid JSON for job {job_id}"
                ) from exc
            if not isinstance(response, dict):
                raise ForcedAlignmentExecutionError(
                    f"forced-aligner response must be an object for job {job_id}"
                )
            results.append(
                _normalize_response(
                    response,
                    job=job,
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
        "command_invoked": True,
        "job_count": len(results),
        "jobs": results,
        "canonical_text_authority": "canonical_lyrics_only",
        "timing_authority": "auxiliary_source_forced_alignment_evidence",
        "privacy": "raw canonical text exists only in ephemeral local request files; evidence stores hashes/offsets/timing only",
    }
