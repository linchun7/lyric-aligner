"""Mandarin singing pronunciation-CTC observer for trusted lyric text.

The adapter reproduces the alignment path of the frozen ASRU 2023 LyricAlignment
source. Canonical lyrics remain immutable; model-facing characters are mapped through
a frozen bert-base-chinese vocabulary and the upstream pronunciation lookup table.
Observer evidence never grants subtitle mutation authority by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import unicodedata
from typing import Any, Mapping, Sequence

import numpy as np

from lyric_aligner.alignment.boundary_hypotheses import BoundaryHypothesisSet
from lyric_aligner.audio.ctc_forced_alignment import (
    CTCForcedAlignmentResult,
    ctc_outer_boundary_hypothesis_set,
    ctc_viterbi_force_align,
)


OBSERVER_VERSION = "1.0"
OBSERVER_AUTHORITY = "observer_evidence_only_never_direct_mutation"
SOURCE_REVISION = "1c39cd0369ce7dc55fa45277b59215b333d3a817"
MODEL_ID = "navi0105/mandarin-lyricalignment-asru2023-alignment"
CORRELATION_GROUP = "whisper_medium_mandarin_singing_pronunciation_ctc"
SAMPLING_RATE = 16000
FRAME_MS = 20.0
RAW_OUTPUT_DIM = 21129
CTC_CLASS_COUNT = 21128
BLANK_ID = 0
MODEL_CHECKPOINT_SHA256 = "c04e813a35a3520ab8521d748b17f4506239ec379ded9c2625e5789ffbdbcb95"
ARCHITECTURE_FINGERPRINT_SHA256 = "e0af360cfee74f9eec1a276166654ad2ab9547d1bb639e540b45b84495b9cfc7"
BERT_VOCAB_SHA256 = "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c"
PRONUNCIATION_TABLE_SHA256 = "c3631b572ceab162e3f5cd382e44322c8aac2cd8f85260b51515746313b2edcb"
ALIGN_MODEL_SOURCE_SHA256 = "378977d19ab549ef5d808bebbcf6e421ad1170d363657393c4214e4684240447"
WHISPER_MEDIUM_AUDIO_HEADS = 16
WHISPER_MEDIUM_AUDIO_LAYERS = 24
WHISPER_MEDIUM_AUDIO_STATE = 1024
WHISPER_MEDIUM_AUDIO_CONTEXT = 1500
WHISPER_MEDIUM_MELS = 80


class MandarinLyricAlignmentObserverError(ValueError):
    pass


@dataclass(frozen=True)
class MandarinLexicalMap:
    alignment_text: str
    bert_vocab_ids: tuple[int, ...]
    pronunciations: tuple[str, ...]
    target_class_ids: tuple[int, ...]

    def validate(self) -> None:
        length = len(self.alignment_text)
        if length < 1 or not (
            len(self.bert_vocab_ids)
            == len(self.pronunciations)
            == len(self.target_class_ids)
            == length
        ):
            raise MandarinLyricAlignmentObserverError("lexical mapping lost trusted characters")
        if any(value < 0 or value >= CTC_CLASS_COUNT for value in self.bert_vocab_ids):
            raise MandarinLyricAlignmentObserverError("BERT vocabulary id is outside frozen vocabulary")
        if any(not value or value == "bad" for value in self.pronunciations):
            raise MandarinLyricAlignmentObserverError("pronunciation mapping contains unsupported character")
        if any(value <= BLANK_ID or value >= CTC_CLASS_COUNT for value in self.target_class_ids):
            raise MandarinLyricAlignmentObserverError("pronunciation class is outside official CTC range")


@dataclass(frozen=True)
class MandarinLyricAlignmentEmission:
    version: str
    authority: str
    model_id: str
    model_revision: str
    source_revision: str
    sampling_rate: int
    frame_ms: float
    blank_id: int
    canonical_text: str
    alignment_text: str
    bert_vocab_ids: tuple[int, ...]
    pronunciations: tuple[str, ...]
    target_class_ids: tuple[int, ...]
    frame_scores: np.ndarray
    audio_duration_ms: float
    automatic_mutation_allowed: bool = False

    def validate(self) -> None:
        if self.version != OBSERVER_VERSION or self.authority != OBSERVER_AUTHORITY:
            raise MandarinLyricAlignmentObserverError("observer version/authority is invalid")
        if not self.model_id.strip() or not self.model_revision.strip() or self.source_revision != SOURCE_REVISION:
            raise MandarinLyricAlignmentObserverError("observer provenance is incomplete")
        if self.sampling_rate != SAMPLING_RATE or self.frame_ms != FRAME_MS or self.blank_id != BLANK_ID:
            raise MandarinLyricAlignmentObserverError("observer clock/audio/blank identity is invalid")
        MandarinLexicalMap(
            self.alignment_text,
            self.bert_vocab_ids,
            self.pronunciations,
            self.target_class_ids,
        ).validate()
        scores = np.asarray(self.frame_scores)
        if scores.ndim != 2 or scores.shape[0] < 1 or scores.shape[1] != CTC_CLASS_COUNT:
            raise MandarinLyricAlignmentObserverError("frame_scores must be [frames, 21128]")
        if not np.all(np.isfinite(scores)):
            raise MandarinLyricAlignmentObserverError("frame_scores contain non-finite values")
        if not np.allclose(np.sum(np.exp(scores), axis=1), 1.0, atol=1e-6, rtol=1e-6):
            raise MandarinLyricAlignmentObserverError("CTC log probabilities are not normalized")
        if not math.isfinite(self.audio_duration_ms) or self.audio_duration_ms <= 0.0:
            raise MandarinLyricAlignmentObserverError("audio duration is invalid")
        if self.automatic_mutation_allowed:
            raise MandarinLyricAlignmentObserverError("observer cannot directly authorize subtitle mutation")


@dataclass(frozen=True)
class MandarinLyricAlignmentOuterEvidence:
    emission: MandarinLyricAlignmentEmission
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
        emission["bert_vocab_ids"] = list(self.emission.bert_vocab_ids)
        emission["pronunciations"] = list(self.emission.pronunciations)
        emission["target_class_ids"] = list(self.emission.target_class_ids)
        return {"emission": emission, "viterbi": self.viterbi.to_dict(), "start": self.start.to_dict(), "end": self.end.to_dict()}


def normalize_trusted_mandarin_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text))
    chars: list[str] = []
    for char in normalized:
        if char.isspace():
            continue
        if unicodedata.category(char).startswith(("P", "S")):
            continue
        chars.append(char)
    result = "".join(chars)
    if not result:
        raise MandarinLyricAlignmentObserverError("canonical text has no alignable characters")
    return result


def load_frozen_bert_vocabulary(path: Path) -> tuple[str, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise MandarinLyricAlignmentObserverError("frozen BERT vocabulary cannot be read") from exc
    # Do not use str.splitlines(): bert-base-chinese has a real U+2028 LINE
    # SEPARATOR vocabulary entry, which splitlines() would incorrectly consume as a
    # delimiter and shift every subsequent vocabulary id.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    vocabulary = tuple(line[:-1] if line.endswith("\r") else line for line in lines)
    if len(vocabulary) != CTC_CLASS_COUNT or any(not line for line in vocabulary) or len(set(vocabulary)) != len(vocabulary):
        raise MandarinLyricAlignmentObserverError("frozen BERT vocabulary is not the expected 21128 unique entries")
    return vocabulary


def load_frozen_pronunciation_table(path: Path) -> tuple[tuple[str, ...], Mapping[str, int]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MandarinLyricAlignmentObserverError("frozen pronunciation table cannot be read") from exc
    if not isinstance(payload, list) or len(payload) != 3:
        raise MandarinLyricAlignmentObserverError("pronunciation table root must contain three elements")
    pronunciation_by_vocab_id, _reverse, pronunciation_class_lookup = payload
    if not isinstance(pronunciation_by_vocab_id, list) or len(pronunciation_by_vocab_id) != CTC_CLASS_COUNT:
        raise MandarinLyricAlignmentObserverError("pronunciation table vocabulary size is not 21128")
    if not isinstance(pronunciation_class_lookup, dict):
        raise MandarinLyricAlignmentObserverError("pronunciation class lookup must be an object")
    lookup: dict[str, int] = {}
    for key, value in pronunciation_class_lookup.items():
        try:
            class_id = int(value)
        except (TypeError, ValueError) as exc:
            raise MandarinLyricAlignmentObserverError("pronunciation class id is invalid") from exc
        if class_id <= BLANK_ID or class_id >= CTC_CLASS_COUNT:
            raise MandarinLyricAlignmentObserverError("pronunciation class id is outside official CTC range")
        lookup[str(key)] = class_id
    return tuple(str(value) for value in pronunciation_by_vocab_id), lookup


def map_text_to_pronunciation_classes(
    alignment_text: str,
    vocabulary: Sequence[str],
    pronunciation_by_vocab_id: Sequence[str],
    pronunciation_class_lookup: Mapping[str, int],
) -> MandarinLexicalMap:
    if not alignment_text or len(vocabulary) != CTC_CLASS_COUNT or len(pronunciation_by_vocab_id) != CTC_CLASS_COUNT:
        raise MandarinLyricAlignmentObserverError("frozen lexical resources are incomplete")
    vocab_index = {value: index for index, value in enumerate(vocabulary)}
    if len(vocab_index) != CTC_CLASS_COUNT:
        raise MandarinLyricAlignmentObserverError("frozen BERT vocabulary is not unique")
    vocab_ids: list[int] = []
    pronunciations: list[str] = []
    class_ids: list[int] = []
    unsupported: list[str] = []
    for char in alignment_text:
        vocab_id = vocab_index.get(char)
        if vocab_id is None:
            unsupported.append(char)
            continue
        pronunciation = str(pronunciation_by_vocab_id[vocab_id])
        class_id = pronunciation_class_lookup.get(pronunciation)
        if pronunciation == "bad" or class_id is None:
            unsupported.append(char)
            continue
        vocab_ids.append(int(vocab_id))
        pronunciations.append(pronunciation)
        class_ids.append(int(class_id))
    if unsupported:
        preview = "".join(dict.fromkeys(unsupported))[:12]
        raise MandarinLyricAlignmentObserverError(
            f"trusted text contains characters unavailable to frozen pronunciation mapping: {preview!r}"
        )
    result = MandarinLexicalMap(alignment_text, tuple(vocab_ids), tuple(pronunciations), tuple(class_ids))
    result.validate()
    return result


def _logsigmoid(values: np.ndarray) -> np.ndarray:
    return -np.logaddexp(0.0, -np.asarray(values, dtype=np.float64))


def _log_softmax(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    maximum = np.max(array, axis=1, keepdims=True)
    shifted = array - maximum
    return shifted - np.log(np.sum(np.exp(shifted), axis=1, keepdims=True))


def official_ctc_log_probabilities(raw_logits: np.ndarray) -> np.ndarray:
    """Reproduce upstream CTC silence/voiced probability semantics exactly."""
    logits = np.asarray(raw_logits, dtype=np.float64)
    if logits.ndim != 2 or logits.shape[0] < 1 or logits.shape[1] != RAW_OUTPUT_DIM:
        raise MandarinLyricAlignmentObserverError("raw alignment logits must be [frames, 21129]")
    if not np.all(np.isfinite(logits)):
        raise MandarinLyricAlignmentObserverError("raw alignment logits contain non-finite values")
    silence_logit = logits[:, -1]
    result = np.empty((logits.shape[0], CTC_CLASS_COUNT), dtype=np.float64)
    result[:, BLANK_ID] = _logsigmoid(silence_logit)
    result[:, 1:] = _log_softmax(logits[:, 1:-1]) + _logsigmoid(-silence_logit)[:, None]
    if not np.allclose(np.sum(np.exp(result), axis=1), 1.0, atol=1e-9, rtol=1e-9):
        raise MandarinLyricAlignmentObserverError("official CTC transform is not normalized")
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_file_sha(path: Path, expected_sha256: str, label: str) -> None:
    if not Path(path).is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    actual = _sha256_file(Path(path))
    if actual != expected_sha256:
        raise MandarinLyricAlignmentObserverError(
            f"{label} SHA256 mismatch: {actual} != {expected_sha256}"
        )


def _build_alignment_only_model(torch_module, whisper_module):
    nn = torch_module.nn
    audio_encoder_type = whisper_module.model.AudioEncoder

    class _EncoderContainer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.encoder = audio_encoder_type(
                WHISPER_MEDIUM_MELS,
                WHISPER_MEDIUM_AUDIO_CONTEXT,
                WHISPER_MEDIUM_AUDIO_STATE,
                WHISPER_MEDIUM_AUDIO_HEADS,
                WHISPER_MEDIUM_AUDIO_LAYERS,
            )

        def embed_audio(self, mel):
            return self.encoder(mel)

    class _AlignmentRNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.rnn = nn.GRU(
                input_size=WHISPER_MEDIUM_AUDIO_STATE,
                hidden_size=384,
                num_layers=2,
                dropout=0.1,
                batch_first=True,
                bidirectional=True,
            )
            self.activate = nn.Mish()
            self.fc = nn.Linear(768, RAW_OUTPUT_DIM)

        def forward(self, values):
            values, _ = self.rnn(values)
            return self.fc(self.activate(values))

    class _AlignmentOnlyModel(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.whisper_model = _EncoderContainer()
            self.align_rnn = _AlignmentRNN()

    with torch_module.device("meta"):
        return _AlignmentOnlyModel()


class LocalMandarinLyricAlignmentBackend:
    """Pinned local ASRU 2023 singing-alignment backend, observer evidence only."""

    def __init__(
        self,
        checkpoint_path: Path,
        *,
        bert_vocab_path: Path,
        pronunciation_table_path: Path,
        model_revision: str = MODEL_CHECKPOINT_SHA256,
        device: str = "cpu",
    ) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        self.bert_vocab_path = Path(bert_vocab_path)
        self.pronunciation_table_path = Path(pronunciation_table_path)
        self.model_revision = str(model_revision)
        self.device = str(device)
        if self.model_revision != MODEL_CHECKPOINT_SHA256:
            raise MandarinLyricAlignmentObserverError(
                "3B model revision must equal the pinned checkpoint SHA256"
            )
        if self.device != "cpu":
            raise MandarinLyricAlignmentObserverError(
                "validated 3B runtime currently permits CPU inference only"
            )

        _require_file_sha(
            self.checkpoint_path,
            MODEL_CHECKPOINT_SHA256,
            "3B alignment checkpoint",
        )
        _require_file_sha(
            self.bert_vocab_path,
            BERT_VOCAB_SHA256,
            "frozen BERT vocabulary",
        )
        _require_file_sha(
            self.pronunciation_table_path,
            PRONUNCIATION_TABLE_SHA256,
            "frozen pronunciation table",
        )
        self.vocabulary = load_frozen_bert_vocabulary(self.bert_vocab_path)
        (
            self.pronunciation_by_vocab_id,
            self.pronunciation_class_lookup,
        ) = load_frozen_pronunciation_table(self.pronunciation_table_path)

        try:
            import torch
            import whisper
        except ImportError as exc:  # pragma: no cover - isolated runtime only
            raise RuntimeError(
                "torch and openai-whisper are required by the 3B runtime"
            ) from exc

        self._torch = torch
        self._whisper = whisper
        model = _build_alignment_only_model(torch, whisper)
        state = torch.load(
            self.checkpoint_path,
            map_location="cpu",
            weights_only=True,
            mmap=True,
        )
        if not isinstance(state, dict) or not state:
            raise MandarinLyricAlignmentObserverError(
                "3B checkpoint is not a non-empty state dict"
            )
        selected = {
            str(key): value
            for key, value in state.items()
            if str(key).startswith("whisper_model.encoder.")
            or str(key).startswith("align_rnn.")
        }
        if not selected:
            raise MandarinLyricAlignmentObserverError(
                "3B checkpoint contains no alignment inference tensors"
            )
        incompatibility = model.load_state_dict(selected, strict=True, assign=True)
        if incompatibility.missing_keys or incompatibility.unexpected_keys:
            raise MandarinLyricAlignmentObserverError(
                "3B checkpoint alignment state is structurally incompatible"
            )
        del selected
        del state
        model.eval()
        self.model = model

    def _load_audio(self, audio_path: Path) -> np.ndarray:
        if not Path(audio_path).is_file():
            raise FileNotFoundError(f"3B observer audio does not exist: {audio_path}")
        try:
            import librosa
        except ImportError as exc:  # pragma: no cover - isolated runtime only
            raise RuntimeError("librosa is required by the 3B runtime") from exc
        waveform, sample_rate = librosa.load(
            str(audio_path),
            sr=SAMPLING_RATE,
            mono=True,
        )
        values = np.asarray(waveform, dtype=np.float32)
        if int(sample_rate) != SAMPLING_RATE or values.ndim != 1 or values.size < 1:
            raise MandarinLyricAlignmentObserverError(
                "3B audio decode did not produce valid 16kHz mono samples"
            )
        if not np.all(np.isfinite(values)):
            raise MandarinLyricAlignmentObserverError(
                "3B decoded audio contains non-finite samples"
            )
        return values

    def _infer_raw_logits(self, waveform: np.ndarray) -> np.ndarray:
        torch = self._torch
        whisper = self._whisper
        audio = np.asarray([waveform], dtype=np.float32)
        with torch.inference_mode():
            mel = whisper.audio.log_mel_spectrogram(audio).to(self.device)
            if mel.shape[-1] <= whisper.audio.N_FRAMES:
                original_embedding_length = int(round(mel.shape[-1] / 2.0))
                padded_mel = whisper.audio.pad_or_trim(
                    mel,
                    whisper.audio.N_FRAMES,
                )
                padded_embedding = self.model.whisper_model.embed_audio(padded_mel)
                embedding = padded_embedding[:, :original_embedding_length, :]
            else:
                pieces = []
                for start in range(0, int(mel.shape[-1]), whisper.audio.N_FRAMES):
                    end = min(
                        start + whisper.audio.N_FRAMES,
                        int(mel.shape[-1]),
                    )
                    original_embedding_length = int(round((end - start) / 2.0))
                    current_mel = whisper.audio.pad_or_trim(
                        mel[:, :, start:end],
                        whisper.audio.N_FRAMES,
                    )
                    current_embedding = self.model.whisper_model.embed_audio(current_mel)
                    pieces.append(
                        current_embedding[:, :original_embedding_length, :]
                    )
                embedding = torch.cat(pieces, dim=1)
            raw = self.model.align_rnn(embedding)[0].detach().cpu().numpy()
        logits = np.asarray(raw, dtype=np.float64)
        if (
            logits.ndim != 2
            or logits.shape[0] < 1
            or logits.shape[1] != RAW_OUTPUT_DIM
        ):
            raise MandarinLyricAlignmentObserverError(
                "3B model produced invalid alignment logits"
            )
        if not np.all(np.isfinite(logits)):
            raise MandarinLyricAlignmentObserverError(
                "3B model produced non-finite alignment logits"
            )
        return logits

    def infer(
        self,
        audio_path: Path,
        *,
        canonical_text: str,
    ) -> MandarinLyricAlignmentEmission:
        alignment_text = normalize_trusted_mandarin_text(canonical_text)
        lexical = map_text_to_pronunciation_classes(
            alignment_text,
            self.vocabulary,
            self.pronunciation_by_vocab_id,
            self.pronunciation_class_lookup,
        )
        waveform = self._load_audio(Path(audio_path))
        audio_duration_ms = (
            float(waveform.size) * 1000.0 / float(SAMPLING_RATE)
        )
        raw_logits = self._infer_raw_logits(waveform)
        frame_scores = official_ctc_log_probabilities(raw_logits)
        emission = MandarinLyricAlignmentEmission(
            version=OBSERVER_VERSION,
            authority=OBSERVER_AUTHORITY,
            model_id=MODEL_ID,
            model_revision=self.model_revision,
            source_revision=SOURCE_REVISION,
            sampling_rate=SAMPLING_RATE,
            frame_ms=FRAME_MS,
            blank_id=BLANK_ID,
            canonical_text=str(canonical_text),
            alignment_text=lexical.alignment_text,
            bert_vocab_ids=lexical.bert_vocab_ids,
            pronunciations=lexical.pronunciations,
            target_class_ids=lexical.target_class_ids,
            frame_scores=frame_scores,
            audio_duration_ms=audio_duration_ms,
            automatic_mutation_allowed=False,
        )
        emission.validate()
        return emission


def build_outer_evidence(
    emission: MandarinLyricAlignmentEmission,
    *,
    window_start_ms: int,
    case_id: str,
    observer_revision: str,
    observer_id: str = "mandarin_lyricalignment_asru2023_ctc",
    correlation_group: str = CORRELATION_GROUP,
    audio_basis: str = "locked_final_mix_clip",
    language: str = "zh",
    max_hypotheses: int = 5,
    min_separation_frames: int = 1,
) -> MandarinLyricAlignmentOuterEvidence:
    emission.validate()
    viterbi = ctc_viterbi_force_align(
        emission.frame_scores,
        emission.target_class_ids,
        blank_id=emission.blank_id,
        frame_ms=emission.frame_ms,
        scores_are_log_probs=True,
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
        scores_are_log_probs=True,
    )
    start = ctc_outer_boundary_hypothesis_set(
        emission.frame_scores, emission.target_class_ids, boundary_kind="start", boundary_id=f"{case_id}:start", **common
    )
    end = ctc_outer_boundary_hypothesis_set(
        emission.frame_scores, emission.target_class_ids, boundary_kind="end", boundary_id=f"{case_id}:end", **common
    )
    return MandarinLyricAlignmentOuterEvidence(emission=emission, viterbi=viterbi, start=start, end=end)


__all__ = [
    "OBSERVER_VERSION", "OBSERVER_AUTHORITY", "SOURCE_REVISION", "MODEL_ID", "CORRELATION_GROUP",
    "SAMPLING_RATE", "FRAME_MS", "RAW_OUTPUT_DIM", "CTC_CLASS_COUNT", "BLANK_ID",
    "MODEL_CHECKPOINT_SHA256", "ARCHITECTURE_FINGERPRINT_SHA256", "BERT_VOCAB_SHA256",
    "PRONUNCIATION_TABLE_SHA256", "ALIGN_MODEL_SOURCE_SHA256",
    "MandarinLyricAlignmentObserverError", "MandarinLexicalMap", "MandarinLyricAlignmentEmission",
    "MandarinLyricAlignmentOuterEvidence", "LocalMandarinLyricAlignmentBackend",
    "normalize_trusted_mandarin_text", "load_frozen_bert_vocabulary",
    "load_frozen_pronunciation_table", "map_text_to_pronunciation_classes", "official_ctc_log_probabilities",
    "build_outer_evidence",
]
