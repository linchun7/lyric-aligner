"""Bounded faster-whisper execution for planner-selected local mix windows.

This executor is optional and lazy-imports faster-whisper.  It never downloads
or loads a model during planning/availability checks.  Runtime output omits raw
ASR text by default but keeps hashes, confidence, word timing and canonical
local-match scores for line-specific jobs.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

from lyric_aligner.text.language_spans import asr_language_hint_for_text


ASR_EVIDENCE_SCHEMA_VERSION = "1.0"


class AsrExecutionError(RuntimeError):
    """Raised when a planned ASR job cannot be executed truthfully."""


@dataclass(frozen=True)
class FasterWhisperExecutionConfig:
    model_id: str
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    temperature: float = 0.0
    include_private_text: bool = False

    def validate(self) -> None:
        if not str(self.model_id or "").strip():
            raise AsrExecutionError("faster-whisper model_id is required")
        if not str(self.device or "").strip():
            raise AsrExecutionError("faster-whisper device is required")
        if not str(self.compute_type or "").strip():
            raise AsrExecutionError("faster-whisper compute_type is required")
        if self.beam_size < 1:
            raise AsrExecutionError("beam_size must be >= 1")
        if not math.isfinite(float(self.temperature)) or self.temperature < 0:
            raise AsrExecutionError("temperature must be finite and >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in value if character.isalnum())


def _text_support(canonical: str, observed: str) -> float | None:
    left = _normalize(canonical)
    right = _normalize(observed)
    if not left or not right:
        return None
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _language_hint(profile: str) -> str | None:
    value = str(profile or "").strip().lower()
    return value if value in {"en", "zh", "ko", "ja"} else None


def _job_language_hint(job: dict[str, Any], canonical_text: str | None) -> str | None:
    """Prefer explicit/local canonical evidence over whole-track language.

    ``asr_language_hint`` is a planner-level override and may intentionally be
    ``auto``/empty.  When canonical text is available we derive the hint from
    that local line.  A mixed-language result intentionally returns ``None``
    and must not fall back to the track profile, otherwise a Chinese track could
    force an English rap/code-switch job through ``language='zh'``.
    """

    if "asr_language_hint" in job:
        return _language_hint(str(job.get("asr_language_hint") or ""))
    if canonical_text is not None and str(canonical_text).strip():
        return asr_language_hint_for_text(
            canonical_text,
            track_language=str(job.get("language_profile") or "auto"),
        )
    return _language_hint(str(job.get("language_profile") or ""))


def _model_factory_default(model_id: str, *, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AsrExecutionError(
            "faster_whisper package is not installed; install requirements-asr.txt"
        ) from exc
    try:
        return WhisperModel(model_id, device=device, compute_type=compute_type)
    except Exception as exc:  # backend-specific model/runtime failures must be explicit
        raise AsrExecutionError(f"cannot initialize faster-whisper model: {exc}") from exc


def _finite_ms(value: Any, *, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AsrExecutionError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise AsrExecutionError(f"{label} must be finite")
    return int(round(number))


def _word_row(word: Any, *, include_private_text: bool) -> dict[str, Any]:
    text = str(getattr(word, "word", "") or "")
    start = float(getattr(word, "start", 0.0) or 0.0)
    end = float(getattr(word, "end", start) or start)
    probability = getattr(word, "probability", None)
    row: dict[str, Any] = {
        "start_ms": int(round(start * 1000.0)),
        "end_ms": int(round(end * 1000.0)),
        "text_sha256": _sha(text),
        "probability": None if probability is None else round(float(probability), 6),
    }
    if include_private_text:
        row["text"] = text
    return row


def _segment_row(segment: Any, *, include_private_text: bool) -> tuple[dict[str, Any], str]:
    text = str(getattr(segment, "text", "") or "")
    start = float(getattr(segment, "start", 0.0) or 0.0)
    end = float(getattr(segment, "end", start) or start)
    words = getattr(segment, "words", None) or []
    row: dict[str, Any] = {
        "start_ms": int(round(start * 1000.0)),
        "end_ms": int(round(end * 1000.0)),
        "text_sha256": _sha(text),
        "avg_logprob": round(float(getattr(segment, "avg_logprob", 0.0) or 0.0), 6),
        "no_speech_prob": round(
            float(getattr(segment, "no_speech_prob", 0.0) or 0.0), 6
        ),
        "compression_ratio": round(
            float(getattr(segment, "compression_ratio", 0.0) or 0.0), 6
        ),
        "words": [
            _word_row(word, include_private_text=include_private_text)
            for word in words
        ],
    }
    if include_private_text:
        row["text"] = text
    return row, text


def execute_faster_whisper_jobs(
    *,
    audio_path: Path,
    plan: dict[str, Any],
    canonical_text_by_job_id: dict[str, str] | None,
    config: FasterWhisperExecutionConfig,
    model_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute only plan jobs requesting mix_asr, one bounded clip per job."""

    config.validate()
    if not audio_path.is_file():
        raise AsrExecutionError(f"mix audio does not exist: {audio_path}")
    if plan.get("mode") != "plan_only" or plan.get("backend_execution_performed") is not False:
        raise AsrExecutionError("input is not an unexecuted alignment plan")
    jobs = plan.get("jobs")
    if not isinstance(jobs, list):
        raise AsrExecutionError("alignment plan jobs must be a list")
    selected = [
        job
        for job in jobs
        if isinstance(job, dict)
        and "mix_asr" in (job.get("requested_capabilities") or [])
    ]
    if not selected:
        return {
            "schema_version": ASR_EVIDENCE_SCHEMA_VERSION,
            "backend": "faster_whisper",
            "config": config.to_dict(),
            "model_loaded": False,
            "job_count": 0,
            "jobs": [],
            "privacy": "raw ASR text omitted unless include_private_text=true",
        }

    factory = model_factory or _model_factory_default
    try:
        model = factory(
            config.model_id,
            device=config.device,
            compute_type=config.compute_type,
        )
    except AsrExecutionError:
        raise
    except Exception as exc:
        raise AsrExecutionError(f"faster-whisper model factory failed: {exc}") from exc

    canonical_text_by_job_id = canonical_text_by_job_id or {}
    results: list[dict[str, Any]] = []
    for job in selected:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise AsrExecutionError("alignment job is missing job_id")
        window = job.get("mix_window_ms")
        if not isinstance(window, list) or len(window) != 2:
            raise AsrExecutionError(f"ASR job {job_id} has no finite mix window")
        start_ms = _finite_ms(window[0], label="ASR clip start")
        end_ms = _finite_ms(window[1], label="ASR clip end")
        if start_ms < 0 or end_ms <= start_ms:
            raise AsrExecutionError(f"ASR job {job_id} has invalid mix window")
        canonical = canonical_text_by_job_id.get(job_id)
        language = _job_language_hint(job, canonical)
        kwargs = {
            "language": language,
            "beam_size": config.beam_size,
            "temperature": config.temperature,
            "condition_on_previous_text": False,
            "word_timestamps": True,
            "vad_filter": False,
            "clip_timestamps": [start_ms / 1000.0, end_ms / 1000.0],
        }
        try:
            segments_iter, info = model.transcribe(str(audio_path), **kwargs)
            segments = list(segments_iter)
        except Exception as exc:
            raise AsrExecutionError(f"ASR job {job_id} failed: {exc}") from exc

        segment_rows: list[dict[str, Any]] = []
        observed_parts: list[str] = []
        for segment in segments:
            row, text = _segment_row(
                segment, include_private_text=config.include_private_text
            )
            segment_rows.append(row)
            observed_parts.append(text)
        observed = " ".join(observed_parts)
        support = None if canonical is None else _text_support(canonical, observed)
        result: dict[str, Any] = {
            "job_id": job_id,
            "occurrence_id": str(job.get("occurrence_id") or ""),
            "canonical_line_index": job.get("canonical_line_index"),
            "mix_window_ms": [start_ms, end_ms],
            "language_hint": language,
            "detected_language": str(getattr(info, "language", "") or ""),
            "language_probability": round(
                float(getattr(info, "language_probability", 0.0) or 0.0), 6
            ),
            "observed_text_sha256": _sha(observed),
            "canonical_text_support_score": None
            if support is None
            else round(float(support), 6),
            "segment_count": len(segment_rows),
            "segments": segment_rows,
        }
        if config.include_private_text:
            result["observed_text"] = observed
        results.append(result)

    return {
        "schema_version": ASR_EVIDENCE_SCHEMA_VERSION,
        "backend": "faster_whisper",
        "config": config.to_dict(),
        "model_loaded": True,
        "job_count": len(results),
        "jobs": results,
        "privacy": (
            "private ASR text included by explicit request"
            if config.include_private_text
            else "raw ASR text omitted; hashes/confidence/timing/support only"
        ),
    }
