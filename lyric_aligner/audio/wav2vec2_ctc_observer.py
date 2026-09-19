"""Local Wav2Vec2 CTC observer adapter for trusted lyric text.

This adapter never transcribes or rewrites canonical lyrics. It only produces
framewise CTC evidence for a known target sequence; production mutation authority
remains above this layer and requires Human-Gold calibration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import math
from pathlib import Path
import unicodedata
from typing import Any

import numpy as np

from lyric_aligner.audio.ctc_forced_alignment import (
    CTCForcedAlignmentResult,
    ctc_outer_boundary_hypothesis_set,
    ctc_viterbi_force_align,
)
from lyric_aligner.alignment.boundary_hypotheses import BoundaryHypothesisSet


WAV2VEC2_CTC_OBSERVER_VERSION = "1.0"
WAV2VEC2_CTC_OBSERVER_AUTHORITY = "observer_evidence_only_never_direct_mutation"
XINGYU_XLSR53_MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
XINGYU_XLSR53_CORRELATION_GROUP = "ssl_speech_ctc_xlsr53_zh"


class Wav2Vec2CTCObserverError(ValueError):
    pass


@dataclass(frozen=True)
class Wav2Vec2CTCEmission:
    version: str
    authority: str
    model_id: str
    model_revision: str
    sampling_rate: int
    frame_ms: float
    blank_id: int
    canonical_text: str
    alignment_text: str
    target_class_ids: tuple[int, ...]
    frame_scores: np.ndarray
    audio_duration_ms: float
    automatic_mutation_allowed: bool = False

    def validate(self) -> None:
        if self.version != WAV2VEC2_CTC_OBSERVER_VERSION:
            raise Wav2Vec2CTCObserverError("unsupported Wav2Vec2 observer version")
        if self.authority != WAV2VEC2_CTC_OBSERVER_AUTHORITY:
            raise Wav2Vec2CTCObserverError("invalid Wav2Vec2 observer authority")
        if not self.model_id.strip() or not self.model_revision.strip():
            raise Wav2Vec2CTCObserverError("model identity must be complete")
        if self.sampling_rate <= 0 or not math.isfinite(self.frame_ms) or self.frame_ms <= 0.0:
            raise Wav2Vec2CTCObserverError("sampling/frame rate is invalid")
        if self.blank_id < 0 or not self.alignment_text or not self.target_class_ids:
            raise Wav2Vec2CTCObserverError("CTC lexical identity is incomplete")
        scores = np.asarray(self.frame_scores)
        if scores.ndim != 2 or scores.shape[0] < 1 or scores.shape[1] < 2:
            raise Wav2Vec2CTCObserverError("frame_scores must be [frames, classes]")
        if not np.all(np.isfinite(scores)):
            raise Wav2Vec2CTCObserverError("frame_scores contain non-finite values")
        if any(value < 0 or value >= scores.shape[1] for value in self.target_class_ids):
            raise Wav2Vec2CTCObserverError("target class is outside model vocabulary")
        if any(value == self.blank_id for value in self.target_class_ids):
            raise Wav2Vec2CTCObserverError("target sequence contains CTC blank")
        if not math.isfinite(self.audio_duration_ms) or self.audio_duration_ms <= 0.0:
            raise Wav2Vec2CTCObserverError("audio duration is invalid")
        if self.automatic_mutation_allowed:
            raise Wav2Vec2CTCObserverError("observer cannot directly authorize subtitle mutation")


@dataclass(frozen=True)
class Wav2Vec2CTCOuterEvidence:
    emission: Wav2Vec2CTCEmission
    viterbi: CTCForcedAlignmentResult
    start: BoundaryHypothesisSet
    end: BoundaryHypothesisSet

    def to_dict(self, *, include_frame_scores: bool = False) -> dict[str, Any]:
        emission = asdict(self.emission)
        scores = np.asarray(self.emission.frame_scores)
        if include_frame_scores:
            emission["frame_scores"] = scores.tolist()
        else:
            emission.pop("frame_scores", None)
            emission["frame_count"] = int(scores.shape[0])
            emission["class_count"] = int(scores.shape[1])
        emission["target_class_ids"] = list(self.emission.target_class_ids)
        return {
            "emission": emission,
            "viterbi": self.viterbi.to_dict(),
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
        }


def normalize_trusted_ctc_text(text: str) -> str:
    """Normalize only model-facing characters; canonical display text is untouched."""

    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    chars: list[str] = []
    for char in normalized:
        if char.isspace():
            continue
        category = unicodedata.category(char)
        if category.startswith(("P", "S")):
            continue
        chars.append(char)
    result = "".join(chars)
    if not result:
        raise Wav2Vec2CTCObserverError("canonical text has no alignable characters")
    return result


def map_text_to_ctc_classes(
    alignment_text: str,
    vocabulary: dict[str, int],
    *,
    blank_id: int,
    reserved_ids: set[int] | None = None,
) -> tuple[int, ...]:
    """Map trusted characters to exact CTC classes and fail closed on unknowns."""

    if not alignment_text:
        raise Wav2Vec2CTCObserverError("alignment_text must not be empty")
    reserved = set(reserved_ids or set())
    reserved.add(int(blank_id))
    class_ids: list[int] = []
    unknown: list[str] = []
    for char in alignment_text:
        class_id = vocabulary.get(char)
        if class_id is None or int(class_id) in reserved:
            unknown.append(char)
            continue
        class_ids.append(int(class_id))
    if unknown:
        preview = "".join(dict.fromkeys(unknown))[:12]
        raise Wav2Vec2CTCObserverError(
            f"trusted text contains characters unavailable to the CTC vocabulary: {preview!r}"
        )
    if len(class_ids) != len(alignment_text):
        raise Wav2Vec2CTCObserverError("CTC target mapping lost trusted characters")
    return tuple(class_ids)


def _load_mono_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - optional runtime only
        raise RuntimeError("soundfile is required by the Wav2Vec2 observer runtime") from exc

    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    waveform = np.asarray(samples, dtype=np.float32)
    if waveform.ndim == 2:
        waveform = np.mean(waveform, axis=1, dtype=np.float32)
    if waveform.ndim != 1 or waveform.size < 1:
        raise Wav2Vec2CTCObserverError("audio must decode to a non-empty waveform")
    if not np.all(np.isfinite(waveform)):
        raise Wav2Vec2CTCObserverError("decoded audio contains non-finite samples")
    return waveform, int(sample_rate)


def _resample_audio(waveform: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(waveform, dtype=np.float32)
    try:
        from scipy.signal import resample_poly
    except ImportError as exc:  # pragma: no cover - optional runtime only
        raise RuntimeError("scipy is required when observer audio needs resampling") from exc
    ratio = Fraction(int(target_rate), int(source_rate))
    result = np.asarray(
        resample_poly(waveform, ratio.numerator, ratio.denominator),
        dtype=np.float32,
    )
    if result.size < 1 or not np.all(np.isfinite(result)):
        raise Wav2Vec2CTCObserverError("audio resampling produced invalid samples")
    return result


class LocalWav2Vec2CTCBackend:
    """Load one pinned local CTC model once and reuse it across many short clips."""

    def __init__(
        self,
        model_dir: Path,
        *,
        model_id: str,
        model_revision: str,
        expected_sampling_rate: int = 16000,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.model_id = str(model_id)
        self.model_revision = str(model_revision)
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"observer model snapshot does not exist: {self.model_dir}")
        if not self.model_revision.strip():
            raise Wav2Vec2CTCObserverError("model_revision must be pinned")
        try:
            import torch
            from transformers import AutoModelForCTC, AutoProcessor
        except ImportError as exc:  # pragma: no cover - optional runtime only
            raise RuntimeError("torch and transformers are required by the observer runtime") from exc

        self._torch = torch
        self.processor = AutoProcessor.from_pretrained(str(self.model_dir), local_files_only=True)
        self.model = AutoModelForCTC.from_pretrained(str(self.model_dir), local_files_only=True)
        self.model.eval()
        self.sampling_rate = int(
            getattr(self.processor.feature_extractor, "sampling_rate", expected_sampling_rate)
        )
        if self.sampling_rate != int(expected_sampling_rate):
            raise Wav2Vec2CTCObserverError(
                f"unexpected model sampling rate: {self.sampling_rate} != {expected_sampling_rate}"
            )
        blank_value = getattr(self.model.config, "pad_token_id", None)
        if blank_value is None:
            blank_value = getattr(self.processor.tokenizer, "pad_token_id", None)
        if blank_value is None:
            raise Wav2Vec2CTCObserverError("CTC blank/pad class is unavailable")
        self.blank_id = int(blank_value)
        self.vocabulary = {
            str(key).lower(): int(value)
            for key, value in self.processor.tokenizer.get_vocab().items()
        }
        self.reserved_ids = {
            int(value)
            for value in getattr(self.processor.tokenizer, "all_special_ids", [])
            if value is not None
        }

    def infer(self, audio_path: Path, *, canonical_text: str) -> Wav2Vec2CTCEmission:
        if not audio_path.is_file():
            raise FileNotFoundError(f"observer audio does not exist: {audio_path}")
        alignment_text = normalize_trusted_ctc_text(canonical_text)
        target_classes = map_text_to_ctc_classes(
            alignment_text,
            self.vocabulary,
            blank_id=self.blank_id,
            reserved_ids=self.reserved_ids,
        )
        waveform, source_rate = _load_mono_audio(audio_path)
        waveform = _resample_audio(waveform, source_rate, self.sampling_rate)
        audio_duration_ms = float(waveform.size) * 1000.0 / float(self.sampling_rate)
        tensor = self._torch.from_numpy(np.asarray(waveform, dtype=np.float32)).unsqueeze(0)
        with self._torch.inference_mode():
            logits = self.model(tensor).logits[0].detach().cpu().numpy()
        scores = np.asarray(logits, dtype=np.float64)
        if scores.ndim != 2 or scores.shape[0] < 1 or not np.all(np.isfinite(scores)):
            raise Wav2Vec2CTCObserverError("model produced invalid CTC logits")
        frame_ms = audio_duration_ms / float(scores.shape[0])
        emission = Wav2Vec2CTCEmission(
            version=WAV2VEC2_CTC_OBSERVER_VERSION,
            authority=WAV2VEC2_CTC_OBSERVER_AUTHORITY,
            model_id=self.model_id,
            model_revision=self.model_revision,
            sampling_rate=self.sampling_rate,
            frame_ms=frame_ms,
            blank_id=self.blank_id,
            canonical_text=str(canonical_text),
            alignment_text=alignment_text,
            target_class_ids=target_classes,
            frame_scores=scores,
            audio_duration_ms=audio_duration_ms,
            automatic_mutation_allowed=False,
        )
        emission.validate()
        return emission


def infer_local_wav2vec2_ctc(
    audio_path: Path,
    *,
    canonical_text: str,
    model_dir: Path,
    model_id: str,
    model_revision: str,
    expected_sampling_rate: int = 16000,
) -> Wav2Vec2CTCEmission:
    """One-off compatibility wrapper; batch callers should reuse LocalWav2Vec2CTCBackend."""

    backend = LocalWav2Vec2CTCBackend(
        model_dir,
        model_id=model_id,
        model_revision=model_revision,
        expected_sampling_rate=expected_sampling_rate,
    )
    return backend.infer(audio_path, canonical_text=canonical_text)


def build_outer_evidence(
    emission: Wav2Vec2CTCEmission,
    *,
    window_start_ms: int,
    case_id: str,
    observer_id: str,
    observer_revision: str,
    correlation_group: str = XINGYU_XLSR53_CORRELATION_GROUP,
    audio_basis: str = "locked_final_mix_clip",
    language: str = "zh",
    max_hypotheses: int = 5,
    min_separation_frames: int = 1,
) -> Wav2Vec2CTCOuterEvidence:
    """Build Viterbi plus posterior start/end evidence from one emission matrix."""

    emission.validate()
    viterbi = ctc_viterbi_force_align(
        emission.frame_scores,
        emission.target_class_ids,
        blank_id=emission.blank_id,
        frame_ms=emission.frame_ms,
    )
    common = dict(
        blank_id=emission.blank_id,
        frame_ms=emission.frame_ms,
        window_start_ms=int(window_start_ms),
        observer_id=observer_id,
        observer_revision=observer_revision,
        correlation_group=correlation_group,
        audio_basis=audio_basis,
        language=language,
        max_hypotheses=max_hypotheses,
        min_separation_frames=min_separation_frames,
    )
    start = ctc_outer_boundary_hypothesis_set(
        emission.frame_scores,
        emission.target_class_ids,
        boundary_kind="start",
        boundary_id=f"{case_id}:start",
        **common,
    )
    end = ctc_outer_boundary_hypothesis_set(
        emission.frame_scores,
        emission.target_class_ids,
        boundary_kind="end",
        boundary_id=f"{case_id}:end",
        **common,
    )
    return Wav2Vec2CTCOuterEvidence(
        emission=emission,
        viterbi=viterbi,
        start=start,
        end=end,
    )


__all__ = [
    "WAV2VEC2_CTC_OBSERVER_VERSION",
    "WAV2VEC2_CTC_OBSERVER_AUTHORITY",
    "XINGYU_XLSR53_MODEL_ID",
    "XINGYU_XLSR53_CORRELATION_GROUP",
    "Wav2Vec2CTCObserverError",
    "Wav2Vec2CTCEmission",
    "Wav2Vec2CTCOuterEvidence",
    "LocalWav2Vec2CTCBackend",
    "normalize_trusted_ctc_text",
    "map_text_to_ctc_classes",
    "infer_local_wav2vec2_ctc",
    "build_outer_evidence",
]
