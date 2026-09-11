#!/usr/bin/env python3
"""Isolated HuBERTFA English direct-final-mix shadow adapter.

This is intentionally a new identity.  It does not modify or reuse the calibrated
`hubertfa_mandarin_v1` production profile identity, even though both use the same
HuBERTFA model family.  Outputs are uncalibrated shadow evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import sys
import tempfile
from pathlib import Path

import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[1]
HFA_ROOT = REPO_ROOT / "private" / "_models" / "hubertfa_sidecar"
CODE_ROOT = REPO_ROOT / "private" / "_models" / "vocal2midi_probe"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from inference.HubertFA.onnx_infer import InferenceOnnx
from lyric_aligner.alignment.english_final_mix_hubertfa_shadow import (
    ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
    ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
    ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
    ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL,
    ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
    sha256_file,
    sha256_json,
)
from lyric_aligner.audio.forced_alignment import (
    CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
    ForcedAlignmentEvidenceError,
    alignment_contextual_segment_interval_ms,
)
from lyric_aligner.alignment.window_policy import FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID

MODEL = HFA_ROOT / "model" / "model.onnx"
MODEL_CONFIG = HFA_ROOT / "model" / "config.json"
MODEL_VERSION = HFA_ROOT / "model" / "VERSION"
MODEL_VOCAB = HFA_ROOT / "model" / "vocab.json"
EN_DICTIONARY = HFA_ROOT / "model" / "ds_cmudict-07b.txt"
EXISTING_MANDARIN_ADAPTER = HFA_ROOT / "production_batch_adapter.py"
VENDOR_DEVICE_UTILS = CODE_ROOT / "inference" / "device_utils.py"
CHUNK_SIZE = 48


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _binding(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"required HuBERTFA file is unavailable: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def _vendor_files() -> dict[str, str]:
    hubertfa = CODE_ROOT / "inference" / "HubertFA"
    files = sorted(hubertfa.rglob("*.py")) + [VENDOR_DEVICE_UTILS]
    if not files or not all(path.is_file() for path in files):
        raise RuntimeError("HuBERTFA English shadow vendor runtime files are unavailable")
    return {str(path.resolve()): sha256_file(path) for path in files}


def _library_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


def _provider_runtime(model: InferenceOnnx) -> dict[str, object]:
    providers = []
    if getattr(model, "model", None) is not None and hasattr(model.model, "get_providers"):
        providers = list(model.model.get_providers())
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "actual_providers": providers,
        "libraries": {
            "onnxruntime": _library_version("onnxruntime"),
            "librosa": _library_version("librosa"),
            "soundfile": _library_version("soundfile"),
        },
        "vendor_device_utils": _binding(VENDOR_DEVICE_UTILS),
    }


def _prediction_words(prediction: object) -> list[dict[str, float | str]]:
    raw = prediction[2] if isinstance(prediction, (tuple, list)) and len(prediction) > 2 else prediction
    result: list[dict[str, float | str]] = []
    for word in raw:
        if isinstance(word, dict):
            text = str(word.get("text") or "")
            start = float(word.get("start"))
            end = float(word.get("end"))
        else:
            text = str(getattr(word, "text", ""))
            start = float(getattr(word, "start"))
            end = float(getattr(word, "end"))
        if not text or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise RuntimeError("HuBERTFA emitted invalid aligned word")
        result.append({"text": text, "start": start, "end": end})
    return result


def _prediction_stem(prediction: object) -> str:
    if not isinstance(prediction, (tuple, list)) or not prediction:
        raise RuntimeError("HuBERTFA prediction has no source path identity")
    return Path(str(prediction[0])).stem


def _dictionary_keys() -> set[str]:
    return {
        line.split("\t", 1)[0].strip().casefold()
        for line in EN_DICTIONARY.read_text(encoding="utf-8-sig").splitlines()
        if "\t" in line
    }


def _validate_request(request: dict) -> list[dict]:
    expected = {
        "protocol_version": ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL,
        "policy_id": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
        "observer_id": ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
        "correlation_group": ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
        "authority": "shadow_only_uncalibrated",
        "automatic_timing_change_allowed": False,
        "window_policy_id": FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
        "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
        "context_continuity_max_gap_ms": ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
    }
    for key, value in expected.items():
        if request.get(key) != value:
            raise ValueError(f"English HuBERTFA shadow request {key} mismatch")
    supplied_request_sha = str(request.get("request_sha256") or "")
    unsigned = dict(request)
    unsigned.pop("request_sha256", None)
    if supplied_request_sha != sha256_json(unsigned):
        raise ValueError("English HuBERTFA shadow request SHA mismatch")
    final_audio = Path(str(request.get("final_audio_path") or "")).resolve()
    expected_audio_sha = str(request.get("final_audio_sha256") or "")
    if not final_audio.is_file() or sha256_file(final_audio) != expected_audio_sha:
        raise ValueError("English HuBERTFA shadow final mix identity mismatch")
    records = request.get("records")
    if not isinstance(records, list) or int(request.get("record_count") or -1) != len(records):
        raise ValueError("English HuBERTFA shadow record_count mismatch")
    if len(records) > 256:
        raise ValueError("English HuBERTFA shadow batch exceeds 256 records")
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("English HuBERTFA shadow record must be an object")
        record_id = str(row.get("record_id") or "")
        if not record_id or record_id in seen:
            raise ValueError("English HuBERTFA shadow record IDs are invalid/duplicated")
        seen.add(record_id)
        if row.get("language") != "en":
            raise ValueError("English HuBERTFA shadow only accepts language=en")
        units = row.get("segment_lexical_units")
        if (
            not isinstance(units, list)
            or len(units) != 3
            or any(not isinstance(part, list) or not part for part in units)
        ):
            raise ValueError("English HuBERTFA shadow requires exactly three nonempty lexical segments")
        if sha256_json(units) != row.get("lexical_units_sha256"):
            raise ValueError("English HuBERTFA shadow lexical SHA mismatch")
        if row.get("alignment_method_id") != CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID:
            raise ValueError("English HuBERTFA shadow alignment method mismatch")
        if row.get("target_segment_index") != 1:
            raise ValueError("English HuBERTFA shadow target must be the middle contextual segment")
        if row.get("context_continuity_max_gap_ms") != ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS:
            raise ValueError("English HuBERTFA shadow context continuity policy mismatch")
        gaps = row.get("adjacent_context_gaps_ms")
        if (
            not isinstance(gaps, list)
            or len(gaps) != 2
            or any(type(value) is not int or value < 0 for value in gaps)
            or any(value > ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS for value in gaps)
        ):
            raise ValueError("English HuBERTFA shadow context continuity evidence is invalid")
        indices = row.get("context_canonical_line_indices")
        if (
            not isinstance(indices, list)
            or len(indices) != 3
            or any(type(value) is not int or value < 0 for value in indices)
            or indices[1] != row.get("canonical_line_index")
            or indices != list(range(indices[0], indices[0] + 3))
        ):
            raise ValueError("English HuBERTFA shadow canonical context identity is invalid")
        try:
            start, end = (int(value) for value in row["mix_window_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("English HuBERTFA shadow mix window is invalid") from exc
        if start < 0 or end <= start:
            raise ValueError("English HuBERTFA shadow mix window is invalid")
        if row.get("routing_timing_is_truth") is not False:
            raise ValueError("English HuBERTFA shadow routing timing cannot be labeled truth")
    return records


def _unaligned(row: dict, reason: str) -> dict:
    return {
        "record_id": row["record_id"],
        "status": "unaligned",
        "predicted_onset_ms": None,
        "predicted_interval_ms": None,
        "reason": reason,
        "lexical_units_sha256": row["lexical_units_sha256"],
        "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
        "mix_window_ms": row["mix_window_ms"],
    }


def _infer_chunk(model: InferenceOnnx, prepared: dict[str, dict]) -> dict[str, object]:
    model.dataset = []
    model.predictions = []
    model.get_dataset(wav_folder=next(iter(prepared.values()))["work"], language="en", g2p="dictionary", dictionary_path=EN_DICTIONARY)
    if len(model.dataset) != len(prepared):
        raise RuntimeError("English HuBERTFA shadow dataset count differs from prepared records")
    model.infer(non_lexical_phonemes="AP", pad_times=3, pad_length=3)
    predictions: dict[str, object] = {}
    for prediction in model.predictions:
        stem = _prediction_stem(prediction)
        if stem not in prepared or stem in predictions:
            raise RuntimeError("English HuBERTFA shadow prediction identity unknown/duplicated")
        predictions[stem] = prediction
    if set(predictions) != set(prepared):
        raise RuntimeError("English HuBERTFA shadow prediction coverage mismatch")
    return predictions


def run(request: dict) -> dict:
    records = _validate_request(request)
    final_audio = Path(request["final_audio_path"]).resolve()
    dictionary_keys = _dictionary_keys()
    info = sf.info(str(final_audio))
    mix_duration_ms = int(round(info.frames * 1000.0 / info.samplerate))
    response_by_id: dict[str, dict] = {}

    model = InferenceOnnx(onnx_path=MODEL)
    model.load_config()
    model.init_decoder()
    model.load_model(device="cpu")

    for chunk_start in range(0, len(records), CHUNK_SIZE):
        chunk = records[chunk_start : chunk_start + CHUNK_SIZE]
        with tempfile.TemporaryDirectory(prefix="hubertfa-en-final-mix-shadow-") as raw:
            work = Path(raw)
            prepared: dict[str, dict] = {}
            for offset, row in enumerate(chunk, start=1):
                flattened = [str(unit).strip().casefold() for part in row["segment_lexical_units"] for unit in part]
                unknown = sorted({unit for unit in flattened if unit not in dictionary_keys})
                if unknown:
                    response_by_id[row["record_id"]] = _unaligned(
                        row, "dictionary_coverage_missing:" + ",".join(unknown[:12])
                    )
                    continue
                window_start, window_end = [int(value) for value in row["mix_window_ms"]]
                if window_end > mix_duration_ms:
                    raise ValueError("English HuBERTFA shadow mix window exceeds final mix")
                start_frame = int(round(window_start * info.samplerate / 1000.0))
                end_frame = int(round(window_end * info.samplerate / 1000.0))
                if not 0 <= start_frame < end_frame <= info.frames:
                    raise ValueError("English HuBERTFA shadow frame window is invalid")
                data, sample_rate = sf.read(
                    str(final_audio), start=start_frame, stop=end_frame, dtype="float32", always_2d=True
                )
                if len(data) == 0:
                    raise RuntimeError("English HuBERTFA shadow decoded empty window")
                stem = f"case_{chunk_start + offset:05d}"
                wav = work / f"{stem}.wav"
                sf.write(str(wav), data.mean(axis=1), sample_rate, subtype="PCM_16")
                (work / f"{stem}.lab").write_text(" ".join(flattened) + "\n", encoding="utf-8")
                prepared[stem] = {"row": row, "work": work}

            if prepared:
                try:
                    predictions = _infer_chunk(model, prepared)
                    failed_batch_reason = None
                except Exception as exc:
                    predictions = {}
                    failed_batch_reason = f"backend_batch_failed:{type(exc).__name__}:{exc}"
                for stem, item in prepared.items():
                    row = item["row"]
                    if failed_batch_reason is not None:
                        response_by_id[row["record_id"]] = _unaligned(row, failed_batch_reason)
                        continue
                    words = _prediction_words(predictions[stem])
                    try:
                        interval = alignment_contextual_segment_interval_ms(
                            words,
                            window_start_ms=int(row["mix_window_ms"][0]),
                            segment_tokens=row["segment_lexical_units"],
                            target_segment_index=1,
                        )
                    except ForcedAlignmentEvidenceError as exc:
                        response_by_id[row["record_id"]] = _unaligned(
                            row, "contextual_alignment_rejected:" + str(exc)
                        )
                        continue
                    predicted_start, predicted_end = [int(value) for value in interval]
                    window_start, window_end = [int(value) for value in row["mix_window_ms"]]
                    if not window_start <= predicted_start < predicted_end <= window_end:
                        response_by_id[row["record_id"]] = _unaligned(
                            row, "predicted_interval_outside_exact_mix_window"
                        )
                        continue
                    response_by_id[row["record_id"]] = {
                        "record_id": row["record_id"],
                        "status": "aligned",
                        "predicted_onset_ms": predicted_start,
                        "predicted_interval_ms": [predicted_start, predicted_end],
                        "reason": "",
                        "aligned_words_sha256": sha256_json(words),
                        "lexical_units_sha256": row["lexical_units_sha256"],
                        "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
                        "mix_window_ms": row["mix_window_ms"],
                    }

    expected_ids = [str(row["record_id"]) for row in records]
    if set(response_by_id) != set(expected_ids):
        raise RuntimeError("English HuBERTFA shadow did not account for all records")
    adapter_path = Path(__file__).resolve()
    return {
        "protocol_version": ENGLISH_FINAL_MIX_HUBERTFA_REQUEST_PROTOCOL,
        "policy_id": ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
        "observer_id": ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
        "correlation_group": ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
        "authority": "shadow_only_uncalibrated",
        "automatic_timing_change_allowed": False,
        "plan_sha256": request["plan_sha256"],
        "request_sha256": request["request_sha256"],
        "task_fingerprint_sha256": request["task_fingerprint_sha256"],
        "final_audio_sha256": request["final_audio_sha256"],
        "window_policy_id": request["window_policy_id"],
        "alignment_method_id": request["alignment_method_id"],
        "context_continuity_max_gap_ms": request["context_continuity_max_gap_ms"],
        "record_count": len(records),
        "model": _binding(MODEL),
        "model_config": _binding(MODEL_CONFIG),
        "model_version": _binding(MODEL_VERSION),
        "model_vocab": _binding(MODEL_VOCAB),
        "dictionary": _binding(EN_DICTIONARY),
        "adapter": _binding(adapter_path),
        "existing_mandarin_adapter_dependency": _binding(EXISTING_MANDARIN_ADAPTER),
        "vendor_files": _vendor_files(),
        "provider_runtime": _provider_runtime(model),
        "records": [response_by_id[record_id] for record_id in expected_ids],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
    response = run(request)
    _atomic_json(args.response.resolve(), response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
