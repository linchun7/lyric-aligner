"""Optional local Qwen ASR observations with alignment of observed text only.

ASR and its forced timestamp alignment form one evidence family. No canonical
text is sent to either model and unavailable model probabilities remain null.
"""
from __future__ import annotations

import math
import hashlib
import importlib.util
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

from lyric_aligner.alignment.asr_executor import (
    ASR_SAMPLE_RATE, WORD_MATCH_POLICY_ID, AsrExecutionError, _canonical_word_span, _finite_ms,
    _job_language_hint, _sha, _text_support,
)
from lyric_aligner.text.language_spans import ASR_LANGUAGE_HINT_POLICY_ID

BACKEND = "qwen3_asr"
STRATEGY = "local_observed_transcript_alignment_v2_windows_tokenizer"
_TOKENIZER_COMPAT = None


@dataclass(frozen=True)
class QwenAsrExecutionConfig:
    model_id: str
    alignment_model_id: str
    device: str = "cpu"
    dtype: str = "bfloat16"
    max_new_tokens: int = 256
    cpu_threads: int = 4
    include_private_text: bool = False

    def validate(self):
        for name in ("model_id", "alignment_model_id"):
            if not str(getattr(self, name)).strip() or not Path(getattr(self, name)).is_dir():
                raise AsrExecutionError(f"Qwen {name} must be an existing local model directory")
        if self.dtype not in ("float32", "bfloat16") or not self.device:
            raise AsrExecutionError("invalid Qwen device/dtype")
        if isinstance(self.max_new_tokens, bool) or not isinstance(self.max_new_tokens, int) or self.max_new_tokens < 1:
            raise AsrExecutionError("Qwen max_new_tokens must be a positive integer")
        if isinstance(self.cpu_threads, bool) or not isinstance(self.cpu_threads, int) or self.cpu_threads < 1:
            raise AsrExecutionError("Qwen cpu_threads must be a positive integer")

    def to_dict(self):
        return asdict(self)


def _prepare_windows_tokenizer():
    """Nagisa's native model loader cannot open Unicode Windows paths.

    Mirror the installed package unchanged into a private ASCII temp directory;
    keep it alive for this process. Never modify model weights or site-packages.
    """
    global _TOKENIZER_COMPAT
    if _TOKENIZER_COMPAT is not None:
        return _TOKENIZER_COMPAT[1]
    if sys.platform != "win32":
        return None
    spec = importlib.util.find_spec("nagisa")
    if spec is None or not spec.origin or str(spec.origin).isascii():
        return None
    source = Path(spec.origin).resolve().parent
    files = sorted(p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    if not files:
        raise AsrExecutionError("installed nagisa package is empty")
    temporary = tempfile.TemporaryDirectory(prefix="lyric-asr-nagisa-")
    destination = Path(temporary.name) / "nagisa"
    if not str(destination).isascii():
        temporary.cleanup()
        raise AsrExecutionError("Windows Qwen tokenizer requires an ASCII TEMP directory")
    hashes = []
    try:
        for path in files:
            relative = path.relative_to(source)
            copied = destination / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copied)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
                raise AsrExecutionError("nagisa tokenizer copy hash mismatch")
            hashes.append(relative.as_posix() + ":" + digest)
    except Exception:
        temporary.cleanup()
        raise
    metadata = {"policy": "windows_nagisa_ascii_mirror_v1", "file_count": len(files),
                "installed_package_sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest()}
    sys.path.insert(0, temporary.name)
    _TOKENIZER_COMPAT = (temporary, metadata)
    return metadata


def _model(config):
    try:
        _prepare_windows_tokenizer()
        import torch
        from qwen_asr import Qwen3ASRModel
        if config.device == "cpu":
            torch.set_num_threads(config.cpu_threads)
        return Qwen3ASRModel.from_pretrained(
            config.model_id, dtype=getattr(torch, config.dtype), device_map=config.device,
            local_files_only=True,
            max_inference_batch_size=1, max_new_tokens=config.max_new_tokens,
            forced_aligner=config.alignment_model_id,
            forced_aligner_kwargs={"dtype": torch.float32, "device_map": config.device, "local_files_only": True},
        )
    except Exception as exc:
        raise AsrExecutionError(f"cannot initialize local Qwen ASR: {exc}") from exc


def _audio(path):
    try:
        _prepare_windows_tokenizer()
        from qwen_asr.inference.utils import normalize_audios
        return normalize_audios(str(path))[0]
    except Exception as exc:
        raise AsrExecutionError(f"cannot decode Qwen ASR audio: {exc}") from exc


def execute_qwen_asr_jobs(*, audio_path, plan, canonical_text_by_job_id, config,
                          model_factory=None, audio_loader=None):
    config.validate()
    if not Path(audio_path).is_file():
        raise AsrExecutionError("mix audio does not exist")
    if plan.get("mode") != "plan_only" or plan.get("backend_execution_performed") is not False:
        raise AsrExecutionError("input is not an unexecuted alignment plan")
    if not isinstance(plan.get("jobs"), list):
        raise AsrExecutionError("alignment plan jobs must be a list")
    selected = [j for j in plan["jobs"] if isinstance(j, dict) and "mix_asr" in (j.get("requested_capabilities") or [])]
    result = dict(schema_version="1.0", backend=BACKEND, execution_strategy=STRATEGY,
        word_match_policy_id=WORD_MATCH_POLICY_ID,
        language_hint_policy_id=ASR_LANGUAGE_HINT_POLICY_ID,
        config=config.to_dict(), model_loaded=False, job_count=0, jobs=[],
        canonical_prompt_used=False, timestamp_text_source="independently_observed_transcript",
        evidence_family_count=1,
        privacy="private ASR text included" if config.include_private_text else "raw ASR text omitted")
    if not selected:
        return result
    audio = (audio_loader or _audio)(Path(audio_path))
    if getattr(audio, "ndim", 1) != 1:
        raise AsrExecutionError("decoded Qwen audio must be mono")
    duration_ms = len(audio) * 1000 / ASR_SAMPLE_RATE
    prepared = []
    seen = set()
    for job in selected:
        identity = str(job.get("job_id") or "").strip()
        window = job.get("mix_window_ms")
        if not identity or identity in seen:
            raise AsrExecutionError("missing or duplicate Qwen job identity")
        seen.add(identity)
        if not isinstance(window, list) or len(window) != 2:
            raise AsrExecutionError("Qwen job has no finite mix window")
        start, end = [_finite_ms(t, label="Qwen clip time") for t in window]
        if start < 0 or end <= start or end > duration_ms:
            raise AsrExecutionError("Qwen job extends beyond audio or has invalid window")
        prepared.append((job, identity, start, end))
    model = (model_factory or _model)(config)
    canonical_text_by_job_id = canonical_text_by_job_id or {}
    languages = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese"}
    for job, identity, start, end in prepared:
        canonical = canonical_text_by_job_id.get(identity)
        hint = _job_language_hint(job, canonical)
        clip = audio[start * ASR_SAMPLE_RATE // 1000:end * ASR_SAMPLE_RATE // 1000]
        try:
            observations = model.transcribe(audio=(clip, ASR_SAMPLE_RATE), context="",
                language=languages.get(hint), return_time_stamps=True)
            if len(observations) != 1:
                raise ValueError("expected one local ASR observation")
            observation = observations[0]
            words = [SimpleNamespace(word=w.text, start=float(w.start_time),
                end=float(w.end_time), probability=None) for w in (observation.time_stamps or [])]
        except Exception as exc:
            raise AsrExecutionError(f"Qwen job {identity} failed: {exc}") from exc
        span = None if canonical is None else _canonical_word_span(canonical,
            [SimpleNamespace(words=words)], offset_ms=start, window_ms=(start, end))
        onset_covered = bool(span and span["canonical_start_covered"])
        end_covered = bool(span and span["canonical_end_covered"])
        observed = str(observation.text or "")
        row = dict(job_id=identity, occurrence_id=str(job.get("occurrence_id") or ""),
            canonical_line_index=job.get("canonical_line_index"), mix_window_ms=[start, end],
            backend=BACKEND, language_hint=hint, detected_language=str(observation.language or ""),
            language_probability=None, observed_text_sha256=_sha(observed),
            canonical_text_support_score=None if canonical is None else _text_support(canonical, observed),
            canonical_match_support_score=None if span is None else span["support_score"],
            canonical_match_ambiguous=bool(span and span["ambiguous"]),
            canonical_match_candidates=[] if span is None else span["candidates"],
            canonical_match_start_ms=span["start_ms"] if onset_covered else None,
            canonical_match_end_ms=span["end_ms"] if end_covered else None,
            canonical_start_covered=onset_covered,
            canonical_end_covered=end_covered,
            observed_match_span=span,
            canonical_match_word_count=None if span is None else span["word_count"],
            canonical_match_mean_word_probability=None,
            canonical_match_normalized_sha256=None if span is None else span["normalized_match_sha256"],
            segment_count=0, segments=[], aligned_words=[])
        # No segment boundary exists in this backend. Do not manufacture one
        # from the requested clip interval for a downstream timing fallback.
        for word in words:
            entry = dict(text_sha256=_sha(word.word), probability=None,
                start_ms=start+round(word.start*1000) if math.isfinite(word.start) else None,
                end_ms=start+round(word.end*1000) if math.isfinite(word.end) else None)
            if config.include_private_text:
                entry["text"] = word.word
            row["aligned_words"].append(entry)
        if config.include_private_text:
            row["observed_text"] = observed
        result["jobs"].append(row)
    result.update(model_loaded=True, job_count=len(result["jobs"]),
        runtime_compatibility=None if _TOKENIZER_COMPAT is None else _TOKENIZER_COMPAT[1])
    return result
