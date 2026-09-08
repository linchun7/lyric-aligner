"""Local-only batch adapter for experimental source-context HuBERTFA.

It deliberately returns only standard, window-local word timings.  Target
interval extraction and source-to-mix projection remain in the project core.
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

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[1]
HFA_ROOT = REPO_ROOT / "private" / "_models" / "hubertfa_sidecar"
CODE_ROOT = REPO_ROOT / "private" / "_models" / "vocal2midi_probe"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lyric_aligner.alignment.source_context_hubertfa import (
    SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT,
    SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
    SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_HUBERTFA_POLICY_ID,
    SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK,
    SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
    SOURCE_CONTEXT_POLICY_THREE_LINE,
    SOURCE_CONTEXT_BLOCK_MAX_SEGMENTS,
    SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
    SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
    SOURCE_CONTEXT_HUBERTFA_PROTOCOL,
    SOURCE_CONTEXT_N_BEST,
    SOURCE_CONTEXT_RADIUS,
    SOURCE_CONTEXT_WINDOW_PAD_MS,
    acoustic_policy_contract,
    context_policy_contract,
    batch_id_matches_request,
    json_sha,
)
from lyric_aligner.alignment.hubertfa_time_bands import (
    HUBERTFA_TIME_BAND_DECODER_ID,
    HUBERTFA_TIME_BAND_POSTCHECK_ID,
    HuBERTFATimeBandError,
    make_time_banded_decoder,
    record_word_bands,
    validate_word_bands,
)
from lyric_aligner.text.english_lexicon import EnglishLexiconBuildError, verify_derived_english_lexicon


MODEL = HFA_ROOT / "model" / "model.onnx"
MODEL_CONFIG = HFA_ROOT / "model" / "config.json"
MODEL_VERSION = HFA_ROOT / "model" / "VERSION"
MODEL_VOCAB = HFA_ROOT / "model" / "vocab.json"
EN_DICTIONARY = HFA_ROOT / "model" / "ds_cmudict-07b.txt"
VENDOR_ONNX_INFER = CODE_ROOT / "inference" / "HubertFA" / "onnx_infer.py"
VENDOR_DEVICE_UTILS = CODE_ROOT / "inference" / "device_utils.py"
TIME_BAND_DECODER = REPO_ROOT / "lyric_aligner" / "alignment" / "hubertfa_time_bands.py"
MAX_ITEMS_PER_DATASET = 96
CPU_EXECUTION = {"device": "cpu", "intra_op_num_threads": 4,
                 "inter_op_num_threads": 1, "execution_mode": "sequential"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_version() -> str:
    return "Python " + ".".join(str(value) for value in sys.version_info[:3])


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def _derived_dictionary_path(request: dict, *, context_policy: str) -> Path:
    """Verify a derived lexicon by replaying its bound generic build recipe."""
    if context_policy != SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        raise ValueError("source-context HuBERTFA derived dictionary is only valid for anchored-path-v1")
    manifest_binding = request.get("dictionary_manifest")
    if not isinstance(manifest_binding, dict) or set(manifest_binding) != {"path", "sha256"}:
        raise ValueError("source-context HuBERTFA derived dictionary manifest binding invalid")
    manifest_path = Path(str(manifest_binding["path"])).resolve()
    if not manifest_path.is_file() or _binding(manifest_path) != manifest_binding:
        raise ValueError("source-context HuBERTFA derived dictionary manifest identity mismatch")
    dictionary_binding = request.get("dictionary")
    if not isinstance(dictionary_binding, dict) or set(dictionary_binding) != {"path", "sha256"}:
        raise ValueError("source-context HuBERTFA derived dictionary binding invalid")
    dictionary_path = Path(str(dictionary_binding["path"])).resolve()
    if not dictionary_path.is_file() or _binding(dictionary_path) != dictionary_binding:
        raise ValueError("source-context HuBERTFA derived dictionary identity mismatch")
    if dictionary_path == EN_DICTIONARY.resolve():
        raise ValueError("source-context HuBERTFA derived dictionary must not reuse the base dictionary path")
    try:
        verify_derived_english_lexicon(dictionary_path=dictionary_path, manifest_path=manifest_path,
                                       base_dictionary_path=EN_DICTIONARY, vocab_path=MODEL_VOCAB)
    except EnglishLexiconBuildError as exc:
        raise ValueError("source-context HuBERTFA derived dictionary verification failed: " + str(exc)) from exc
    return dictionary_path


def _validated_dictionary_path(request: dict, *, context_policy: str) -> Path:
    """Preserve the old exact base gate unless the new path manifest is bound."""
    if "dictionary_manifest" not in request:
        if request.get("dictionary") != _binding(EN_DICTIONARY):
            raise ValueError("source-context HuBERTFA dictionary identity mismatch")
        return EN_DICTIONARY
    return _derived_dictionary_path(request, context_policy=context_policy)


def _vendor_files() -> dict[str, str]:
    hubertfa = CODE_ROOT / "inference" / "HubertFA"
    files = sorted(hubertfa.rglob("*.py")) + [VENDOR_DEVICE_UTILS]
    if not all(path.is_file() for path in files):
        raise RuntimeError("HuBERTFA vendor runtime files are unavailable")
    return {str(path.resolve()): sha256_file(path) for path in files}


def _new_cpu4_model():
    """Lazily import optional vendor dependencies only for an eligible batch."""
    import onnxruntime as ort
    from inference.HubertFA.onnx_infer import InferenceOnnx
    from inference.device_utils import resolve_onnx_providers

    class _Cpu4InferenceOnnx(InferenceOnnx):
        @staticmethod
        def create_session(onnx_path, device: str | None = None):
            _, providers = resolve_onnx_providers(device, label="HuBERTFA ONNX")
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = CPU_EXECUTION["intra_op_num_threads"]
            options.inter_op_num_threads = CPU_EXECUTION["inter_op_num_threads"]
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            return ort.InferenceSession(str(onnx_path), options, providers=providers)

    return _Cpu4InferenceOnnx(onnx_path=MODEL)


def _library_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


def _dictionary_keys(path: Path) -> set[str]:
    return {line.split("\t", 1)[0].strip().casefold()
            for line in path.read_text(encoding="utf-8-sig").splitlines() if "\t" in line}


def _words(prediction: object) -> list[dict[str, float | str]]:
    raw = prediction[2] if isinstance(prediction, (tuple, list)) and len(prediction) > 2 else prediction
    result=[]
    for word in raw:
        if isinstance(word, dict):
            text, start, end = str(word.get("text") or ""), float(word.get("start")), float(word.get("end"))
        else:
            text, start, end = str(getattr(word, "text", "")), float(getattr(word, "start")), float(getattr(word, "end"))
        if not text or not all(math.isfinite(value) for value in (start, end)) or start < 0 or end < start:
            raise RuntimeError("HuBERTFA emitted invalid source-context word")
        result.append({"text": text, "start": start, "end": end})
    return result


def _stem(prediction: object) -> str:
    if not isinstance(prediction, (tuple, list)) or not prediction:
        raise RuntimeError("HuBERTFA prediction has no source path identity")
    return Path(str(prediction[0])).stem


def _unaligned(record: dict, reason: str) -> dict:
    return {"record_id": record["record_id"], "status": "unaligned", "reason": reason, "words": []}


def _request_contract(request: dict) -> tuple[str, dict[str, object], dict[str, object]]:
    """Resolve only the explicit, independently-versioned request identities.

    The block policy has a separate identity so a signed three-line request
    cannot silently widen its source context.  It still permits records marked
    ``three-line-v1`` to preserve prior qualified observations in that batch.
    """
    policy_id = request.get("policy_id")
    if policy_id == SOURCE_CONTEXT_HUBERTFA_POLICY_ID:
        acoustic_policy = SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT
        context_policy = SOURCE_CONTEXT_POLICY_THREE_LINE
    elif policy_id == SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID:
        acoustic_policy = SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP
        context_policy = SOURCE_CONTEXT_POLICY_THREE_LINE
    elif policy_id == SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID:
        acoustic_policy = SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP
        context_policy = SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK
    elif policy_id == SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID:
        acoustic_policy = SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP
        context_policy = SOURCE_CONTEXT_POLICY_ANCHORED_PATH
    else:
        raise ValueError("source-context HuBERTFA policy mismatch")
    acoustic = acoustic_policy_contract(acoustic_policy)
    context = context_policy_contract(context_policy, acoustic_policy)
    if context["policy_id"] != policy_id:
        raise ValueError("source-context HuBERTFA policy contract mismatch")
    return context_policy, dict(acoustic["inference_kwargs"]), {
        **dict(acoustic["request_fields"]), **dict(context["request_fields"]),
    }


def _validate_request(request: dict) -> dict[str, object]:
    if request.get("protocol_version") != SOURCE_CONTEXT_HUBERTFA_PROTOCOL:
        raise ValueError("source-context HuBERTFA protocol mismatch")
    context_policy, inference_kwargs, required_policy_fields = _request_contract(request)
    for key, value in required_policy_fields.items():
        if request.get(key) != value:
            raise ValueError("source-context HuBERTFA policy field mismatch: " + key)
    policy_fields = ("acoustic_policy", "non_lexical_phonemes", "pad_times", "pad_length",
                     "context_policy", "block_max_interior_lines", "block_max_window_ms",
                     "block_max_lexical_units", "time_banded_decoder_id", "time_banded_postcheck",
                     "time_banded_source_pad_ms")
    if context_policy == SOURCE_CONTEXT_POLICY_THREE_LINE and any(key in request for key in policy_fields
                                                                    if key not in required_policy_fields):
        raise ValueError("source-context HuBERTFA historical three-line policy cannot carry overrides")
    for key, value in (("context_radius", SOURCE_CONTEXT_RADIUS), ("n_best", SOURCE_CONTEXT_N_BEST),
                       ("window_pad_ms", SOURCE_CONTEXT_WINDOW_PAD_MS)):
        if request.get(key) != value:
            raise ValueError("source-context HuBERTFA fixed policy field mismatch: " + key)
    if (request.get("model") != _binding(MODEL) or request.get("model_config") != _binding(MODEL_CONFIG)
            or request.get("model_version") != _binding(MODEL_VERSION)
            or request.get("model_vocab") != _binding(MODEL_VOCAB)):
        raise ValueError("source-context HuBERTFA model/config identity mismatch")
    runtime = request.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("path") != str(Path(sys.executable).resolve()) or runtime.get("version") != _runtime_version():
        raise ValueError("source-context HuBERTFA runtime path/version mismatch")
    if not isinstance(request.get("batch_id"), str) or len(request["batch_id"]) != 64 or not batch_id_matches_request(request):
        raise ValueError("source-context HuBERTFA batch request identity mismatch")
    if request.get("cpu_execution") != CPU_EXECUTION:
        raise ValueError("source-context HuBERTFA CPU execution contract mismatch")
    if request.get("vendor_files") != _vendor_files():
        raise ValueError("source-context HuBERTFA vendor manifest mismatch")
    if context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        if request.get("time_band_decoder") != _binding(TIME_BAND_DECODER):
            raise ValueError("source-context HuBERTFA time-band decoder identity mismatch")
    elif "time_band_decoder" in request:
        raise ValueError("source-context HuBERTFA historical policy cannot carry time-band decoder")
    if "dictionary_manifest" in request and context_policy != SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        raise ValueError("source-context HuBERTFA historical policy cannot carry derived dictionary manifest")
    records=request.get("records")
    if not isinstance(records, list) or request.get("record_count") != len(records):
        raise ValueError("source-context HuBERTFA record count mismatch")
    ids=[str(item.get("record_id") or "") for item in records if isinstance(item, dict)]
    if len(ids)!=len(records) or not all(ids) or len(set(ids))!=len(ids):
        raise ValueError("source-context HuBERTFA record ids invalid/duplicated")
    # The derived-lexicon verifier reconstructs the full dictionary in a
    # temporary directory.  Bind the complete request first so an altered
    # payload cannot make the adapter perform this relatively expensive work.
    dictionary_path = _validated_dictionary_path(request, context_policy=context_policy)
    return inference_kwargs


def _prepare(record: dict, work: Path, stem: str, *, request_policy_id: str = SOURCE_CONTEXT_HUBERTFA_POLICY_ID,
             dictionary_path: Path | None = None) -> tuple[str, Path] | tuple[None, str]:
    """Validate one signed record and write its exact FLOAT source window."""
    # Resolve at call time so hermetic callers that patch ``EN_DICTIONARY`` do
    # not retain the local private-model path captured during module import.
    dictionary_path = EN_DICTIONARY if dictionary_path is None else Path(dictionary_path)
    try:
        request_context_policy, _inference_kwargs, _policy_fields = _request_contract({"policy_id": request_policy_id})
    except ValueError:
        return None, "unsupported_request_policy"
    record_context_policy = record.get("context_policy")
    if request_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK:
        if record_context_policy not in (SOURCE_CONTEXT_POLICY_THREE_LINE, SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK):
            return None, "anchored_block_record_context_policy_invalid"
    elif request_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        if record_context_policy not in (SOURCE_CONTEXT_POLICY_THREE_LINE, SOURCE_CONTEXT_POLICY_ANCHORED_PATH):
            return None, "anchored_path_record_context_policy_invalid"
    elif record_context_policy is not None:
        return None, "historical_three_line_record_context_policy_invalid"
    language=str(record.get("language") or "en")
    if language != "en":
        return None, "provider_language_not_verified_en"
    units=record.get("segment_lexical_units")
    if not isinstance(units,list) or any(not isinstance(part,list) or not part for part in units):
        return None, "invalid_segment_lexical_units"
    if (request_context_policy in (SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, SOURCE_CONTEXT_POLICY_ANCHORED_PATH)
            and record_context_policy in (SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, SOURCE_CONTEXT_POLICY_ANCHORED_PATH)):
        if not 3 <= len(units) <= SOURCE_CONTEXT_BLOCK_MAX_SEGMENTS:
            return None, "anchored_block_segment_count_invalid"
        outputs = record.get("target_outputs")
        if (not isinstance(outputs, list) or not outputs
                or any(not isinstance(item, dict) or not isinstance(item.get("target_segment_index"), int)
                       or not 0 < item["target_segment_index"] < len(units) - 1 for item in outputs)
                or len({item.get("position") for item in outputs}) != len(outputs)):
            return None, "anchored_block_target_outputs_invalid"
    elif len(units) != 3:
        return None, "invalid_three_segment_lexical_units"
    if json_sha(units)!=record.get("lexical_units_sha256"):
        return None, "lexical_units_identity_mismatch"
    # Read the large fixed dictionary once per signed record.  The old
    # comprehension reparsed it for every lexical unit, which becomes
    # pathological for the bounded 256-unit block policy.
    dictionary_keys = _dictionary_keys(dictionary_path)
    unknown=sorted({str(unit).casefold().strip() for part in units for unit in part
                    if str(unit).casefold().strip() not in dictionary_keys})
    if unknown:
        return None, "dictionary_coverage_missing:"+",".join(unknown[:12])
    try: start_ms,end_ms=(int(value) for value in record["source_window_ms"])
    except (KeyError,TypeError,ValueError): return None,"invalid_source_window"
    if not start_ms < end_ms:
        return None, "invalid_source_window"
    if (request_context_policy in (SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, SOURCE_CONTEXT_POLICY_ANCHORED_PATH)
            and record_context_policy in (SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, SOURCE_CONTEXT_POLICY_ANCHORED_PATH)):
        if end_ms - start_ms > SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS:
            return None, "anchored_block_source_window_cap_exceeded"
        if sum(len(part) for part in units) > SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS:
            return None, "anchored_block_lexical_unit_cap_exceeded"
    if request_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH and record_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        context = record.get("context")
        if (not isinstance(context, dict) or context.get("time_banded_decoder_id") != HUBERTFA_TIME_BAND_DECODER_ID
                or context.get("time_banded_postcheck") != HUBERTFA_TIME_BAND_POSTCHECK_ID
                or context.get("time_banded_source_pad_ms") != SOURCE_CONTEXT_WINDOW_PAD_MS):
            return None, "anchored_path_time_band_contract_invalid"
        try:
            record_word_bands(record, [str(unit) for part in units for unit in part],
                              source_pad_ms=SOURCE_CONTEXT_WINDOW_PAD_MS)
        except HuBERTFATimeBandError as exc:
            return None, str(exc)
    audio=Path(record.get("source_audio_path") or "")
    if not audio.is_file() or sha256_file(audio)!=record.get("source_audio_sha256"):
        return None, "source_audio_identity_mismatch"
    try:
        info=sf.info(str(audio)); start_frame=round(start_ms*info.samplerate/1000); end_frame=round(end_ms*info.samplerate/1000)
    except Exception:
        return None, "source_audio_decode_failed"
    try:
        expected_domain_end = int(record["source_search_domain_end_ms"])
    except (KeyError, TypeError, ValueError):
        return None, "source_clock_missing_search_domain_end"
    actual_domain_end = round(info.frames * 1000.0 / info.samplerate)
    if actual_domain_end != expected_domain_end:
        return None, "source_clock_mismatch"
    if not 0<=start_frame<end_frame<=info.frames:
        return None,"source_window_outside_audio"
    try:
        with sf.SoundFile(audio) as handle:
            handle.seek(start_frame); data=handle.read(end_frame-start_frame,dtype="float32",always_2d=True)
    except Exception:
        return None, "source_audio_decode_failed"
    if len(data)!=end_frame-start_frame:
        return None,"source_window_decode_short"
    mono=data.mean(axis=1); wav=work/(stem+".wav")
    sf.write(str(wav),mono,int(info.samplerate),subtype="FLOAT")
    readback,rate=sf.read(str(wav),dtype="float32",always_2d=True)
    if rate!=info.samplerate or readback.shape!=(len(mono),1) or not np.array_equal(readback[:,0],mono):
        raise RuntimeError("FLOAT source window readback mismatch")
    (work/(stem+".lab")).write_text(" ".join(str(unit) for part in units for unit in part)+"\n",encoding="utf-8")
    return language,wav


def _reset_chunk_state(model) -> None:
    """Keep one loaded session while making each <=96 input/output batch total."""
    model.dataset = []
    model.predictions = []


def _time_banded_decoder_for_record(model, record: dict, dataset_item: object):
    """Build one constrained decoder without changing the loaded ONNX model.

    Only new anchored-path block records enter here.  Legacy three-line records
    in the same request keep the vendor's ordinary decoder and their prior
    interpretation.
    """
    if not isinstance(dataset_item, (tuple, list)) or len(dataset_item) != 4:
        raise RuntimeError("time_band_dataset_item_invalid")
    _wav_path, _phones, word_seq, _mapping = dataset_item
    if not isinstance(word_seq, list):
        raise RuntimeError("time_band_word_sequence_invalid")
    bands = record_word_bands(record, word_seq, source_pad_ms=SOURCE_CONTEXT_WINDOW_PAD_MS)
    base = model.fa_decoder
    decoder_cls = make_time_banded_decoder(type(base))
    decoder = decoder_cls(vocab=model.vocab, sample_rate=model.mel_cfg["sample_rate"],
                          hop_size=model.mel_cfg["hop_size"])
    decoder.set_word_bands(word_seq, bands)
    return decoder, list(word_seq), bands


def run(request: dict) -> dict:
    inference_kwargs = _validate_request(request)
    request_policy_id = str(request["policy_id"])
    try:
        request_context_policy = _request_contract({"policy_id": request_policy_id})[0]
    except ValueError:
        # Unit tests can inject the already-validated inference contract with a
        # synthetic policy ID.  It is never a path-masked record.
        request_context_policy = None
    # ``_validate_request`` just bound the exact dictionary path, including a
    # replay of any derived manifest.  Do not retain that state across runs.
    # Injected unit-test validators may omit a dictionary path, in which case
    # retain the historical fixture behavior with the base path.  An accepted
    # production-derived path is never silently replaced if it disappears.
    dictionary_binding = request.get("dictionary")
    dictionary_path = (Path(str(dictionary_binding["path"])).resolve()
                       if isinstance(dictionary_binding, dict) and isinstance(dictionary_binding.get("path"), str)
                       else EN_DICTIONARY)
    records=request["records"]; response_by_id={}
    session_providers = []
    inference_failures = []
    with tempfile.TemporaryDirectory(prefix="hubertfa-source-context-") as raw:
        work=Path(raw); groups={"en":[]}
        for index,record in enumerate(records,1):
            stem="record_{:05d}".format(index)
            prepared=_prepare(record,work,stem,request_policy_id=request_policy_id,
                              dictionary_path=dictionary_path)
            if prepared[0] is None:
                response_by_id[record["record_id"]]=_unaligned(record,prepared[1]);continue
            language,_=prepared;groups[language].append((stem,record))
        model=None
        for language,items in groups.items():
            for start in range(0,len(items),MAX_ITEMS_PER_DATASET):
                chunk=items[start:start+MAX_ITEMS_PER_DATASET]
                if not chunk: continue
                if model is None:
                    model=_new_cpu4_model();model.load_config();model.init_decoder();model.load_model(device="cpu")
                    session_providers = list(model.model.get_providers())
                # Dataset construction reads all WAVs in this temporary folder;
                # make a per-chunk folder of hard-linked files to retain exact IDs.
                with tempfile.TemporaryDirectory(prefix="hfa-source-context-chunk-") as raw_chunk:
                    chunk_dir=Path(raw_chunk); expected={}
                    for stem,record in chunk:
                        (chunk_dir/(stem+".wav")).write_bytes((work/(stem+".wav")).read_bytes())
                        (chunk_dir/(stem+".lab")).write_text((work/(stem+".lab")).read_text(encoding="utf-8"),encoding="utf-8")
                        expected[stem]=record
                    # InferenceBase accumulates both lists.  Keep one loaded
                    # session and make the input scan bounded, but invoke its
                    # per-item inference loop on one item at a time.  A vendor
                    # phoneme-consensus failure for one lyric must not mark the
                    # other 95 IDs unavailable.
                    _reset_chunk_state(model)
                    model.get_dataset(wav_folder=chunk_dir,language=language,g2p="dictionary",
                                      dictionary_path=dictionary_path)
                    if len(model.dataset)!=len(expected): raise RuntimeError("HuBERTFA source-context dataset count mismatch")
                    dataset_by_stem = {Path(str(item[0])).stem: item for item in model.dataset}
                    if set(dataset_by_stem) != set(expected):
                        raise RuntimeError("HuBERTFA source-context dataset identity mismatch")
                    for stem, record in expected.items():
                        _reset_chunk_state(model)
                        model.dataset = [dataset_by_stem[stem]]
                        original_decoder = None
                        expected_words = None
                        word_bands = None
                        try:
                            if request_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH \
                                    and record.get("context_policy") == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
                                original_decoder = model.fa_decoder
                                model.fa_decoder, expected_words, word_bands = _time_banded_decoder_for_record(
                                    model, record, dataset_by_stem[stem])
                            model.infer(**inference_kwargs)
                            if len(model.predictions) != 1:
                                raise RuntimeError("HuBERTFA source-context single prediction count mismatch")
                            prediction = model.predictions[0]
                            if _stem(prediction) != stem:
                                raise RuntimeError("HuBERTFA source-context single prediction identity mismatch")
                            words = _words(prediction)
                            if expected_words is not None and word_bands is not None:
                                validate_word_bands(words, expected_words, word_bands,
                                                    frame_length=model.fa_decoder.frame_length)
                            response_by_id[record["record_id"]] = {"record_id":record["record_id"],"status":"aligned",
                                "reason":"","words":words,
                                **({"time_band_postcheck": "passed", "time_band_word_band_count": len(word_bands)}
                                   if word_bands is not None else {})}
                        except Exception as exc:
                            reason = "hubertfa_inference_failed:" + type(exc).__name__ + ":" + str(exc)
                            response_by_id[record["record_id"]] = _unaligned(record, reason)
                            inference_failures.append({"record_id": record["record_id"], "stage": "per_record_infer",
                                "exception_type": type(exc).__name__, "message": str(exc)})
                        finally:
                            if original_decoder is not None:
                                model.fa_decoder = original_decoder
    if set(response_by_id)!={record["record_id"] for record in records}:
        raise RuntimeError("HuBERTFA source-context records not fully accounted")
    return {"protocol_version":SOURCE_CONTEXT_HUBERTFA_PROTOCOL,"policy_id":request["policy_id"],
            "batch_id":request["batch_id"],"model":request["model"],"model_config":request["model_config"],
            "model_version":request["model_version"],"model_vocab":request["model_vocab"],
            "dictionary":request["dictionary"],"runtime":request["runtime"],"cpu_execution":CPU_EXECUTION,
            "vendor_files":request["vendor_files"],
            **({"time_band_decoder": request["time_band_decoder"]}
               if "time_band_decoder" in request else {}),
            **({"dictionary_manifest": request["dictionary_manifest"]}
               if "dictionary_manifest" in request else {}),
            "vendor_onnx_infer":{"path":str(VENDOR_ONNX_INFER.resolve()),"sha256":sha256_file(VENDOR_ONNX_INFER)},
            "provider_runtime":{"onnxruntime_version":_library_version("onnxruntime"), "actual_providers":session_providers,
                "libraries":{"onnxruntime":_library_version("onnxruntime"), "librosa":_library_version("librosa"),
                             "soundfile":_library_version("soundfile")},
                "vendor_device_utils":{"path":str(VENDOR_DEVICE_UTILS.resolve()),"sha256":sha256_file(VENDOR_DEVICE_UTILS)}},
            "diagnostics":{"inference_failures": inference_failures},
            **{key: request[key] for key in ("acoustic_policy", "non_lexical_phonemes", "pad_times", "pad_length",
                                                "context_policy", "block_max_interior_lines", "block_max_window_ms",
                                                "block_max_lexical_units", "time_banded_decoder_id",
                                                "time_banded_postcheck", "time_banded_source_pad_ms") if key in request},
            "record_count":len(records),"records":[response_by_id[record["record_id"]] for record in records]}


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--request",type=Path,required=True);parser.add_argument("--response",type=Path,required=True);args=parser.parse_args()
    request=json.loads(args.request.read_text(encoding="utf-8-sig"));response=run(request)
    args.response.parent.mkdir(parents=True,exist_ok=True);args.response.write_text(json.dumps(response,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return 0


if __name__=="__main__": raise SystemExit(main())
