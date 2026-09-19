"""Experimental, source-side three-line HuBERTFA observations.

Nothing in this module is a production evidence family.  It builds an
auditable three-line source window from existing source-ASR packet evidence,
asks an isolated local sidecar for words, and extracts the target interval
with the shared contextual API.  The caller decides whether to write a
report-only ledger or a separate experimental overlay.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from lyric_aligner.audio.forced_alignment import (
    ForcedAlignmentEvidenceError,
    alignment_contextual_segment_interval_ms,
)
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.text.alignment_lexical import alignment_units
from lyric_aligner.alignment.hubertfa_time_bands import (
    HUBERTFA_TIME_BAND_DECODER_ID,
    HUBERTFA_TIME_BAND_POSTCHECK_ID,
)


SOURCE_CONTEXT_HUBERTFA_PROTOCOL = "source-context-hubertfa-batch-1.0"
SOURCE_CONTEXT_HUBERTFA_POLICY_ID = "source-context-hubertfa-three-line-v1"
SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID = "source-context-hubertfa-three-line-lexical-only-no-ap-v1"
SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID = "source-context-hubertfa-anchored-block-lexical-only-no-ap-v1"
SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID = "source-context-hubertfa-anchored-path-lexical-only-no-ap-v1"
SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT = "ap_default"
SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP = "lexical_only_no_ap"
SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICIES = frozenset({
    SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT,
    SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
})
SOURCE_CONTEXT_POLICY_THREE_LINE = "three-line-v1"
SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK = "anchored-block-v1"
SOURCE_CONTEXT_POLICY_ANCHORED_PATH = "anchored-path-v1"
SOURCE_CONTEXT_POLICIES = frozenset({
    SOURCE_CONTEXT_POLICY_THREE_LINE,
    SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK,
    SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
})
SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES = 12
SOURCE_CONTEXT_BLOCK_MAX_SEGMENTS = SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES + 2
SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS = 45_000
SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS = 256
SOURCE_CONTEXT_RADIUS = 2
SOURCE_CONTEXT_N_BEST = 1024
SOURCE_CONTEXT_WINDOW_PAD_MS = 1500
SOURCE_CONTEXT_CPU_EXECUTION = {"device": "cpu", "intra_op_num_threads": 4,
                                "inter_op_num_threads": 1, "execution_mode": "sequential"}
SOURCE_CONTEXT_AUTHORITY = "experimental_only_not_production_authority"
SOURCE_CONTEXT_MODES = frozenset({"report-only", "hfa-only-overlay"})


class SourceContextHuBERTFAError(ValueError):
    pass


def acoustic_policy_contract(selector: str) -> dict[str, Any]:
    """Return one of the two frozen HuBERTFA acoustic contracts.

    The original AP contract has no additional request fields, preserving its
    historical request and provenance identity.  The no-AP experiment gets a
    distinct policy identity and binds every vendor inference argument.
    """
    if selector == SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT:
        return {
            "policy_id": SOURCE_CONTEXT_HUBERTFA_POLICY_ID,
            "request_fields": {},
            "inference_kwargs": {"non_lexical_phonemes": "AP", "pad_times": 3, "pad_length": 3},
        }
    if selector == SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP:
        return {
            "policy_id": SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "request_fields": {
                "acoustic_policy": SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
                "non_lexical_phonemes": "",
                "pad_times": 3,
                "pad_length": 3,
            },
            "inference_kwargs": {"non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3},
        }
    raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA acoustic policy")


def context_policy_contract(selector: str, acoustic_policy: str) -> dict[str, Any]:
    """Return the fixed context contract without widening historical policies."""
    if selector == SOURCE_CONTEXT_POLICY_THREE_LINE:
        return {
            "policy_id": acoustic_policy_contract(acoustic_policy)["policy_id"],
            "request_fields": {},
            "min_segments": 3,
            "max_segments": 3,
            "max_window_ms": None,
            "max_lexical_units": None,
        }
    if selector == SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK:
        if acoustic_policy != SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP:
            raise SourceContextHuBERTFAError("anchored-block HuBERTFA requires lexical_only_no_ap")
        return {
            "policy_id": SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "request_fields": {
                "context_policy": SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK,
                "block_max_interior_lines": SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES,
                "block_max_window_ms": SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
                "block_max_lexical_units": SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
            },
            "min_segments": 3,
            "max_segments": SOURCE_CONTEXT_BLOCK_MAX_SEGMENTS,
            "max_window_ms": SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
            "max_lexical_units": SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
        }
    if selector == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        if acoustic_policy != SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP:
            raise SourceContextHuBERTFAError("anchored-path HuBERTFA requires lexical_only_no_ap")
        return {
            "policy_id": SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "request_fields": {
                "context_policy": SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
                "block_max_interior_lines": SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES,
                "block_max_window_ms": SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
                "block_max_lexical_units": SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
                "time_banded_decoder_id": HUBERTFA_TIME_BAND_DECODER_ID,
                "time_banded_postcheck": HUBERTFA_TIME_BAND_POSTCHECK_ID,
                "time_banded_source_pad_ms": SOURCE_CONTEXT_WINDOW_PAD_MS,
            },
            "min_segments": 3,
            "max_segments": SOURCE_CONTEXT_BLOCK_MAX_SEGMENTS,
            "max_window_ms": SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
            "max_lexical_units": SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
        }
    raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA context policy")


def json_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def batch_id_matches_request(request: Mapping[str, Any]) -> bool:
    """Bind every request field, including windows and lexical units, to its ID."""
    if not isinstance(request, Mapping) or not isinstance(request.get("batch_id"), str):
        return False
    try:
        expected = json_sha({key: value for key, value in request.items() if key != "batch_id"})
    except (TypeError, ValueError):
        return False
    return request["batch_id"] == expected


def _bound(value: Any, *, label: str, resolve_path: Callable[[str], Path], bind_file: Callable[[dict, str], Path]) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise SourceContextHuBERTFAError(label + " requires {path,sha256}")
    # ``bind_file`` is the job's existing identity/path safety mechanism.
    path = bind_file(value, label)
    if not path.is_file():
        raise SourceContextHuBERTFAError(label + " path is not a file")
    return path


@dataclass(frozen=True)
class SourceContextHuBERTFAConfig:
    mode: str
    acoustic_policy: str
    adapter_path: Path
    model_path: Path
    model_config_path: Path
    model_version_path: Path
    model_vocab_path: Path
    dictionary_path: Path
    runtime_path: Path
    context_policy: str = SOURCE_CONTEXT_POLICY_THREE_LINE
    time_band_decoder_path: Path | None = None
    dictionary_manifest_path: Path | None = None

    def vendor_files(self) -> dict[str, str]:
        """Hash the finite vendor surface actually imported by the adapter."""
        inference_root = Path(__file__).resolve().parents[2] / "private" / "_models" / "vocal2midi_probe" / "inference"
        hubertfa = inference_root / "HubertFA"
        files = sorted(hubertfa.rglob("*.py")) + [inference_root / "device_utils.py"]
        if not all(item.is_file() for item in files):
            raise SourceContextHuBERTFAError("HuBERTFA vendor runtime files are unavailable")
        return {str(item.resolve()): sha256_file(item) for item in files}

    @classmethod
    def from_job(cls, value: Any, *, resolve_path: Callable[[str], Path],
                 bind_file: Callable[[dict, str], Path]) -> "SourceContextHuBERTFAConfig | None":
        """Parse the explicit, fixed-policy experimental block.

        The finite acoustic/context selectors name independently-versioned
        contracts; arbitrary vendor knobs remain forbidden.
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            raise SourceContextHuBERTFAError("experimental_source_context_hubertfa must be an object")
        expected = {"enabled", "mode", "adapter", "model", "model_config", "model_version",
                    "model_vocab", "dictionary", "runtime"}
        optional = {"acoustic_policy", "context_policy", "time_band_decoder", "dictionary_manifest"}
        if not expected <= set(value) <= expected | optional:
            raise SourceContextHuBERTFAError("experimental_source_context_hubertfa has unsupported or missing fields")
        if value["enabled"] is not True:
            raise SourceContextHuBERTFAError("experimental_source_context_hubertfa must be explicitly enabled")
        mode = str(value["mode"])
        if mode not in SOURCE_CONTEXT_MODES:
            raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA mode")
        acoustic_policy = str(value.get("acoustic_policy", SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT))
        if acoustic_policy not in SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICIES:
            raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA acoustic policy")
        context_policy = str(value.get("context_policy", SOURCE_CONTEXT_POLICY_THREE_LINE))
        if context_policy not in SOURCE_CONTEXT_POLICIES:
            raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA context policy")
        # Validate the cross-selector contract before opening any sidecar input.
        context_policy_contract(context_policy, acoustic_policy)
        adapter = _bound(value["adapter"], label="hfa:adapter", resolve_path=resolve_path, bind_file=bind_file)
        model = _bound(value["model"], label="hfa:model", resolve_path=resolve_path, bind_file=bind_file)
        model_config = _bound(value["model_config"], label="hfa:model_config", resolve_path=resolve_path, bind_file=bind_file)
        model_version = _bound(value["model_version"], label="hfa:model_version", resolve_path=resolve_path, bind_file=bind_file)
        model_vocab = _bound(value["model_vocab"], label="hfa:model_vocab", resolve_path=resolve_path, bind_file=bind_file)
        dictionary = _bound(value["dictionary"], label="hfa:dictionary", resolve_path=resolve_path, bind_file=bind_file)
        runtime = value["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != {"path"}:
            raise SourceContextHuBERTFAError("hfa:runtime must contain path only; executable hash is intentionally not claimed")
        runtime_path = resolve_path(str(runtime["path"]))
        if not runtime_path.is_file():
            raise SourceContextHuBERTFAError("hfa:runtime path is not a file")
        time_band_decoder = value.get("time_band_decoder")
        if context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
            time_band_decoder_path = _bound(time_band_decoder, label="hfa:time_band_decoder",
                                            resolve_path=resolve_path, bind_file=bind_file)
        elif time_band_decoder is not None:
            raise SourceContextHuBERTFAError("hfa:time_band_decoder is only valid for anchored-path-v1")
        else:
            time_band_decoder_path = None
        dictionary_manifest = value.get("dictionary_manifest")
        if dictionary_manifest is not None:
            if context_policy != SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
                raise SourceContextHuBERTFAError("hfa:dictionary_manifest is only valid for anchored-path-v1")
            dictionary_manifest_path = _bound(dictionary_manifest, label="hfa:dictionary_manifest",
                                               resolve_path=resolve_path, bind_file=bind_file)
        else:
            dictionary_manifest_path = None
        return cls(mode=mode, acoustic_policy=acoustic_policy, adapter_path=adapter, model_path=model, model_config_path=model_config,
                   model_version_path=model_version, model_vocab_path=model_vocab,
                   dictionary_path=dictionary, runtime_path=runtime_path, context_policy=context_policy,
                   time_band_decoder_path=time_band_decoder_path, dictionary_manifest_path=dictionary_manifest_path)

    @property
    def policy_id(self) -> str:
        return str(context_policy_contract(self.context_policy, self.acoustic_policy)["policy_id"])

    def acoustic_request_fields(self) -> dict[str, Any]:
        return dict(acoustic_policy_contract(self.acoustic_policy)["request_fields"])

    def context_request_fields(self) -> dict[str, Any]:
        return dict(context_policy_contract(self.context_policy, self.acoustic_policy)["request_fields"])

    def record_constraints(self) -> dict[str, Any]:
        contract = context_policy_contract(self.context_policy, self.acoustic_policy)
        return {key: contract[key] for key in ("min_segments", "max_segments", "max_window_ms", "max_lexical_units")}

    def identity(self) -> dict[str, Any]:
        result = {
            "protocol_version": SOURCE_CONTEXT_HUBERTFA_PROTOCOL,
            "policy_id": self.policy_id,
            "context_radius": SOURCE_CONTEXT_RADIUS,
            "n_best": SOURCE_CONTEXT_N_BEST,
            "window_pad_ms": SOURCE_CONTEXT_WINDOW_PAD_MS,
            "mode": self.mode,
            "adapter": {"path": str(self.adapter_path), "sha256": sha256_file(self.adapter_path)},
            "model": {"path": str(self.model_path), "sha256": sha256_file(self.model_path)},
            "model_config": {"path": str(self.model_config_path), "sha256": sha256_file(self.model_config_path)},
            # ``InferenceOnnx.load_config`` reads these two adjacent model files
            # in addition to config.json/model.onnx.  Bind every read input.
            "model_version": {"path": str(self.model_version_path), "sha256": sha256_file(self.model_version_path)},
            "model_vocab": {"path": str(self.model_vocab_path), "sha256": sha256_file(self.model_vocab_path)},
            "dictionary": {"path": str(self.dictionary_path), "sha256": sha256_file(self.dictionary_path)},
            # Python executable is recorded by path/version only; no false hash claim.
            "runtime": {"path": str(self.runtime_path), "version": _runtime_version(self.runtime_path)},
            # The sidecar subclass sets these SessionOptions explicitly.  The
            # values are an execution identity, not a tunable policy input.
            "cpu_execution": dict(SOURCE_CONTEXT_CPU_EXECUTION),
            "vendor_files": self.vendor_files(),
            **self.acoustic_request_fields(),
            **self.context_request_fields(),
        }
        if self.context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
            if self.time_band_decoder_path is None:
                raise SourceContextHuBERTFAError("anchored-path HuBERTFA decoder binding missing")
            result["time_band_decoder"] = {
                "path": str(self.time_band_decoder_path), "sha256": sha256_file(self.time_band_decoder_path),
            }
            if self.dictionary_manifest_path is not None:
                result["dictionary_manifest"] = {
                    "path": str(self.dictionary_manifest_path), "sha256": sha256_file(self.dictionary_manifest_path),
                }
        return result


def _runtime_version(runtime: Path) -> str:
    done = subprocess.run([str(runtime), "--version"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        raise SourceContextHuBERTFAError("hfa runtime --version failed")
    return (done.stdout or done.stderr).strip()


def _dictionary_keys(path: Path) -> set[str]:
    return {line.split("\t", 1)[0].strip().casefold()
            for line in path.read_text(encoding="utf-8-sig").splitlines() if "\t" in line}


def _english_text_complete(texts: Sequence[str]) -> bool:
    """Prove the English G2P front end will not silently discard a script.

    Punctuation and whitespace are display-only; every remaining character
    must be ASCII alphanumeric or an apostrophe accepted by ``english_units``.
    This is a front-end contract, not a language-derived reliability score.
    """
    import unicodedata

    allowed_punctuation = set(" \\t\\r\\n.,!?;:()[]{}\\\"/\\\\-–—_+&@#$%^*=~`|<>")
    for text in texts:
        normalized = unicodedata.normalize("NFKC", str(text)).replace("’", "'")
        for char in normalized:
            if char.isascii() and (char.isalnum() or char == "'"):
                continue
            if char in allowed_punctuation:
                continue
            return False
    return True


def _full_single_line_range(ranges: Sequence[Mapping[str, Any]], lines: Mapping[int, str]) -> int | None:
    if len(ranges) != 1:
        return None
    item = ranges[0]
    try:
        index, start, end = (int(item[key]) for key in ("canonical_line_index", "start_char", "end_char"))
    except (KeyError, TypeError, ValueError):
        return None
    if index not in lines or start != 0 or end != len(lines[index]):
        return None
    return index


def _positive_interval(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        start, end = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    return (start, end) if 0 <= start < end else None


def _candidate_by_id(packet: Mapping[str, Any], candidate_id: str | None) -> Mapping[str, Any] | None:
    if not candidate_id:
        return None
    return next((item for item in packet.get("candidates", []) if item.get("candidate_id") == candidate_id), None)


def _packet_complete_candidate(packet: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str | None]:
    """Return the frozen-protocol neighbor candidate: exactly one raw packet."""

    candidates = packet.get("candidates", [])
    if len(candidates) != 1:
        return None, ("neighbor_source_candidate_ambiguous" if len(candidates) > 1
                      else "neighbor_source_candidate_missing")
    candidate = candidates[0]
    if _positive_interval(candidate.get("source_interval_ms")) is None:
        return None, "neighbor_source_interval_incomplete"
    return candidate, None


def _qualified_anchor_candidate(packet: Mapping[str, Any], promotions: Mapping[str, str]) -> tuple[Mapping[str, Any] | None, str | None, str | None]:
    """Return only a full-context anchor or the original all-optimal promotion.

    This intentionally excludes raw partial singleton candidates.  The same
    source sequence built for the original all-canonical packet set is the only
    permitted duplicate resolver.
    """

    full = [candidate for candidate in packet.get("candidates", [])
            if candidate.get("full_context_disambiguated")]
    if len(full) == 1:
        chosen, qualification = full[0], "selected_full_context"
    elif packet.get("selection_reason") == "ambiguous_canonical_packet_identity":
        chosen = _candidate_by_id(packet, promotions.get(str(packet.get("cache_key_sha256"))))
        qualification = "all_optimal_duplicate_promotion" if chosen is not None else None
    else:
        chosen, qualification = None, None
    if chosen is None or _positive_interval(chosen.get("source_interval_ms")) is None:
        return None, None, "source_packet_no_unique_full_context_candidate"
    return chosen, qualification, None


def _target_candidate(packet: Mapping[str, Any], promotions: Mapping[str, str]) -> tuple[Mapping[str, Any] | None, str | None]:
    """Mirror the frozen English replay target rule for a three-line target."""
    chosen, _qualification, reason = _qualified_anchor_candidate(packet, promotions)
    return chosen, reason


def _three_line_window(
    *,
    target_index: int,
    line_order: Sequence[int],
    packets: Mapping[int, Mapping[str, Any]],
    target: Mapping[str, Any],
    source_domain: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Build the literal independent left/target/right FW span protocol."""

    try:
        ordinal = list(line_order).index(target_index)
    except ValueError:
        return None, "target_canonical_line_missing"
    if ordinal == 0 or ordinal == len(line_order) - 1:
        return None, "missing_canonical_left_or_right_line"
    left_index, right_index = line_order[ordinal - 1], line_order[ordinal + 1]
    left, reason = _packet_complete_candidate(packets[left_index])
    if left is None:
        return None, reason
    right, reason = _packet_complete_candidate(packets[right_index])
    if right is None:
        return None, reason
    intervals = {
        left_index: _positive_interval(left.get("source_interval_ms")),
        target_index: _positive_interval(target.get("source_interval_ms")),
        right_index: _positive_interval(right.get("source_interval_ms")),
    }
    if any(value is None for value in intervals.values()):
        return None, "three_line_source_interval_incomplete"
    left_interval = intervals[left_index]
    target_interval = intervals[target_index]
    right_interval = intervals[right_index]
    assert left_interval is not None and target_interval is not None and right_interval is not None
    if not (left_interval[0] < left_interval[1] <= target_interval[0] < target_interval[1]
            <= right_interval[0] < right_interval[1]):
        return None, "three_line_source_order_or_overlap_invalid"
    domain = _positive_interval([source_domain.get("start_ms"), source_domain.get("end_ms")])
    assert domain is not None
    values = [value for value in intervals.values() if value is not None]
    low, high = min(value[0] for value in values), max(value[1] for value in values)
    if low < domain[0] or high > domain[1]:
        return None, "three_line_source_interval_outside_search_domain"
    window = [max(domain[0], low - SOURCE_CONTEXT_WINDOW_PAD_MS),
              min(domain[1], high + SOURCE_CONTEXT_WINDOW_PAD_MS)]
    if not window[0] <= low < high <= window[1]:
        return None, "source_window_does_not_cover_full_context"
    return {
        "left_index": left_index,
        "target_index": target_index,
        "right_index": right_index,
        "line_intervals_ms": {str(index): list(interval) for index, interval in intervals.items()},
        "source_window_ms": window,
        "context_source_interval_ms": [low, high],
        "coverage": {
            "left_start_covered": window[0] <= intervals[left_index][0],
            "right_end_covered": intervals[right_index][1] <= window[1],
            "padding_before_ms": low - window[0],
            "padding_after_ms": window[1] - high,
            "window_policy": "minimum_complete_left_target_right_fw_span_pm1500_clamped_to_source",
        },
        "independent_packet_candidates": {
            "left": {"candidate_id": left.get("candidate_id"), "source_interval_ms": list(intervals[left_index])},
            "target": {"candidate_id": target.get("candidate_id"), "source_interval_ms": list(intervals[target_index])},
            "right": {"candidate_id": right.get("candidate_id"), "source_interval_ms": list(intervals[right_index])},
        },
    }, None


def prepare_occurrence(
    *, occurrence_id: str, source_audio_path: Path, source_audio_sha256: str,
    language: str, canonical_lines: Sequence[Mapping[str, Any]], cue_specs: Sequence[Mapping[str, Any]],
    observed_words: Sequence[Mapping[str, Any]], source_observation_sha256: str,
    source_search_domain: Mapping[str, Any], lyric_version: str, model_id: str,
    dictionary_path: Path, policy_id: str = SOURCE_CONTEXT_HUBERTFA_POLICY_ID,
    context_policy: str = SOURCE_CONTEXT_POLICY_THREE_LINE,
    packet_builder: Callable[..., dict] | None = None,
    sequence_resolver: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Prepare every owned target under the frozen English three-packet protocol.

    Source packets are built for *all* canonical lines before cue filtering.
    A qualified target uses its own locally-complete packet plus an exactly-one
    candidate packet for each immediate canonical neighbor.  This intentionally
    matches the frozen public English replication rather than deriving neighbor
    time from a target packet's embedded context.
    """
    if policy_id not in (SOURCE_CONTEXT_HUBERTFA_POLICY_ID,
                         SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID,
                         SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID,
                         SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID):
        raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA prepared policy")
    if context_policy not in SOURCE_CONTEXT_POLICIES:
        raise SourceContextHuBERTFAError("unsupported source-context HuBERTFA prepared context policy")
    if context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK:
        if policy_id != SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID:
            raise SourceContextHuBERTFAError("anchored-block preparation policy identity mismatch")
    if context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        if policy_id != SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID:
            raise SourceContextHuBERTFAError("anchored-path preparation policy identity mismatch")
    from lyric_aligner.alignment.source_packets import BOUNDED_TARGET_POLICY, build_source_packet_candidates
    from lyric_aligner.alignment.source_sequence import resolve_source_sequence

    if sha256_file(source_audio_path) != source_audio_sha256:
        raise SourceContextHuBERTFAError("source audio SHA mismatch")
    if not isinstance(source_observation_sha256, str) or len(source_observation_sha256) != 64:
        raise SourceContextHuBERTFAError("source observation SHA is required")
    if str(language).casefold() != "en":
        raise SourceContextHuBERTFAError("source-context HuBERTFA frontend language must be fixed to en")
    canonical = [{"canonical_line_index": int(item["canonical_line_index"]), "text": str(item["text"])}
                 for item in canonical_lines]
    line_order = [item["canonical_line_index"] for item in canonical]
    if not canonical or len(set(line_order)) != len(line_order) or any(a >= b for a, b in zip(line_order, line_order[1:])):
        raise SourceContextHuBERTFAError("canonical lines must retain unique increasing original indices")
    lines = {item["canonical_line_index"]: item["text"] for item in canonical}
    domain = _positive_interval([source_search_domain.get("start_ms"), source_search_domain.get("end_ms")])
    if domain is None:
        raise SourceContextHuBERTFAError("source search domain is invalid")
    builder = packet_builder or build_source_packet_candidates
    packets: dict[int, dict[str, Any]] = {}
    for index in line_order:
        text = lines[index]
        packet = builder(canonical_lines=canonical, observed_words=observed_words,
            target_cue={"cue_id": "hfa:" + str(index), "canonical_character_ranges": [{
                "canonical_line_index": index, "start_char": 0, "end_char": len(text)}]},
            source_audio_sha256=source_audio_sha256, lyric_version=lyric_version,
            source_search_domain=source_search_domain, model_id=model_id,
            context_radius=SOURCE_CONTEXT_RADIUS, n_best=SOURCE_CONTEXT_N_BEST,
            target_alignment_policy=BOUNDED_TARGET_POLICY)
        # The resolver refuses a mixed/unbound lattice.  The observer artifact
        # binding is added to every packet without altering its packet identity.
        packet["source_observation_sha256"] = source_observation_sha256
        packets[index] = packet
    resolver = sequence_resolver or resolve_source_sequence
    sequence = resolver(list(packets.values()), source_observation_sha256=source_observation_sha256)
    promotions = {str(item["packet_cache_key_sha256"]): str(item["candidate_id"])
                  for item in sequence.get("promotions", [])}
    dictionary = _dictionary_keys(dictionary_path)
    ledger: dict[int, dict[str, Any]] = {}
    prepared = []
    for spec in cue_specs:
        position = int(spec["position"])
        entry = {"position": position, "occurrence_id": occurrence_id,
                 "baseline_interval_ms": list(spec["baseline_interval_ms"]), "status": "unavailable", "reasons": []}
        ledger[position] = entry
        ranges = spec.get("canonical_character_ranges")
        if not isinstance(ranges, list):
            entry["reasons"].append("canonical_cue_ownership_unresolved")
            continue
        target_index = _full_single_line_range(ranges, lines)
        if target_index is None:
            entry["reasons"].append("target_not_exactly_one_complete_canonical_line")
            continue
        packet = packets[target_index]
        entry["source_packet"] = packet
        entry["target_index"] = target_index
        chosen, reason = _target_candidate(packet, promotions)
        if chosen is None:
            entry["reasons"].append(str(reason))
            continue
        complete, reason = _three_line_window(target_index=target_index, line_order=line_order,
            packets=packets, target=chosen, source_domain=source_search_domain)
        if complete is None:
            entry["reasons"].append(str(reason))
            continue
        text = [lines[complete[key]] for key in ("left_index", "target_index", "right_index")]
        if not _english_text_complete(text):
            entry["reasons"].append("provider_text_not_verified_en")
            continue
        try:
            units = [alignment_units("en", value) for value in text]
        except Exception as exc:
            entry["reasons"].append("canonical_lexical_preparation_failed:" + type(exc).__name__)
            continue
        unknown = sorted({unit for segment in units for unit in segment if unit not in dictionary})
        if unknown:
            entry["reasons"].append("dictionary_coverage_missing:" + ",".join(unknown[:12]))
            entry["dictionary_unknown_units"] = unknown
            continue
        record_id = json_sha({"occurrence_id": occurrence_id, "position": entry["position"],
                              "packet": packet["cache_key_sha256"], "candidate": chosen["candidate_id"],
                              "window": complete["source_window_ms"], "policy": policy_id})
        record = {"record_id": record_id, "occurrence_id": occurrence_id, "position": entry["position"],
                  "language": "en", "source_audio_path": str(source_audio_path), "source_audio_sha256": source_audio_sha256,
                  "source_window_ms": complete["source_window_ms"], "segment_text": text,
                  "source_search_domain_end_ms": domain[1],
                  "segment_lexical_units": units, "lexical_units_sha256": json_sha(units),
                  "source_packet_cache_key_sha256": packet["cache_key_sha256"],
                  "source_packet_candidate_id": chosen["candidate_id"],
                  "candidate_selection": "unique_full_context_or_duplicate_only_sequence_promotion",
                  "context": complete, "baseline_interval_ms": entry["baseline_interval_ms"]}
        if context_policy in (SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, SOURCE_CONTEXT_POLICY_ANCHORED_PATH):
            record["context_policy"] = SOURCE_CONTEXT_POLICY_THREE_LINE
        entry.update({"status": "ready_for_hubertfa", "reasons": [], "prepared_record_id": record_id,
                      "source_context": complete, "segment_lexical_units": units,
                      "source_packet_candidate_id": chosen["candidate_id"],
                      "candidate_selection": record["candidate_selection"]})
        prepared.append(record)
    result = {"schema_version": "source-context-hubertfa-prepared-occurrence-1.0",
            "policy_id": policy_id, "authority": SOURCE_CONTEXT_AUTHORITY,
            "context_policy": context_policy,
            "automatic_production_mutation_allowed": False, "occurrence_id": occurrence_id,
            "source_observation_sha256": source_observation_sha256, "source_sequence": sequence,
            "canonical_lines": canonical,
            "packet_count": len(packets), "packets": [{"canonical_line_index": index, "packet": packets[index]}
                                                      for index in line_order],
            "records": prepared, "ledger": [ledger[key] for key in sorted(ledger)]}
    if context_policy in (SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, SOURCE_CONTEXT_POLICY_ANCHORED_PATH):
        result.update({"source_audio_path": str(source_audio_path), "source_audio_sha256": source_audio_sha256,
                       "source_search_domain": dict(source_search_domain)})
    return result


def _block_anchor_detail(index: int, packet: Mapping[str, Any], candidate: Mapping[str, Any], qualification: str) -> dict[str, Any]:
    interval = _positive_interval(candidate.get("source_interval_ms"))
    assert interval is not None
    return {
        "canonical_line_index": index,
        "packet_cache_key_sha256": packet.get("cache_key_sha256"),
        "candidate_id": candidate.get("candidate_id"),
        "source_interval_ms": list(interval),
        "qualification": qualification,
    }


def _append_block_rejection(entries: Sequence[dict[str, Any]], reason: str) -> None:
    for entry in entries:
        entry["anchored_block_rejection"] = reason
        entry.setdefault("reasons", []).append("anchored_block:" + reason)


def prepare_anchored_blocks(prepared: Mapping[str, Any], *, dictionary_path: Path) -> dict[str, Any]:
    """Add bounded no-AP anchor-block records for targets lacking three-line FA.

    This consumes the exact all-canonical packet lattice and sequence already
    made by :func:`prepare_occurrence`; it never rebuilds, re-ranks or widens a
    failed nearest-anchor pair.  Existing three-line records remain untouched.
    """
    block_context_policy = prepared.get("context_policy")
    policy_id = prepared.get("policy_id")
    accepted = {
        SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK: SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID,
        SOURCE_CONTEXT_POLICY_ANCHORED_PATH: SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
    }
    if block_context_policy not in accepted:
        raise SourceContextHuBERTFAError("anchored blocks require anchored prepared context")
    if policy_id != accepted[block_context_policy]:
        raise SourceContextHuBERTFAError("anchored blocks require matching lexical-only no-AP policy")
    canonical = [dict(item) for item in prepared.get("canonical_lines", []) if isinstance(item, Mapping)]
    line_order = [int(item["canonical_line_index"]) for item in canonical]
    lines = {int(item["canonical_line_index"]): str(item["text"]) for item in canonical}
    packet_rows = prepared.get("packets", [])
    packets = {int(row["canonical_line_index"]): row["packet"] for row in packet_rows
               if isinstance(row, Mapping) and isinstance(row.get("packet"), Mapping)}
    if not canonical or set(packets) != set(line_order):
        raise SourceContextHuBERTFAError("anchored blocks require complete canonical packet lattice")
    sequence = prepared.get("source_sequence", {})
    promotions = {str(item["packet_cache_key_sha256"]): str(item["candidate_id"])
                  for item in sequence.get("promotions", []) if isinstance(item, Mapping)
                  and item.get("packet_cache_key_sha256") and item.get("candidate_id")}
    domain = _positive_interval([prepared.get("source_search_domain", {}).get("start_ms"),
                                 prepared.get("source_search_domain", {}).get("end_ms")])
    # ``prepare_occurrence`` historically did not retain this top-level field;
    # the caller supplies it below when constructing a new block-capable result.
    if domain is None:
        domain = _positive_interval(prepared.get("source_search_domain_ms"))
    if domain is None:
        raise SourceContextHuBERTFAError("anchored blocks require source search domain")

    anchors: dict[int, dict[str, Any]] = {}
    for index in line_order:
        candidate, qualification, _reason = _qualified_anchor_candidate(packets[index], promotions)
        if candidate is not None and qualification is not None:
            anchors[index] = _block_anchor_detail(index, packets[index], candidate, qualification)
    anchor_positions = [offset for offset, index in enumerate(line_order) if index in anchors]
    ledger = [entry for entry in prepared.get("ledger", []) if isinstance(entry, dict)]
    missing = [entry for entry in ledger if entry.get("status") == "unavailable"
               and isinstance(entry.get("target_index"), int)]
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for entry in missing:
        ordinal = line_order.index(int(entry["target_index"]))
        left_candidates = [position for position in anchor_positions if position < ordinal]
        right_candidates = [position for position in anchor_positions if position > ordinal]
        if not left_candidates or not right_candidates:
            _append_block_rejection([entry], "nearest_qualified_outer_anchor_missing")
            continue
        pair = (line_order[left_candidates[-1]], line_order[right_candidates[0]])
        grouped.setdefault(pair, []).append(entry)

    dictionary = _dictionary_keys(dictionary_path)
    records: list[dict[str, Any]] = []
    for (left_index, right_index), entries in sorted(grouped.items()):
        left_ordinal, right_ordinal = line_order.index(left_index), line_order.index(right_index)
        interior = line_order[left_ordinal + 1:right_ordinal]
        if not interior or len(interior) > SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES:
            _append_block_rejection(entries, "interior_line_cap_exceeded")
            continue
        outer = [anchors[left_index], anchors[right_index]]
        left_interval, right_interval = outer[0]["source_interval_ms"], outer[1]["source_interval_ms"]
        if not (left_interval[0] < left_interval[1] <= right_interval[0] < right_interval[1]):
            _append_block_rejection(entries, "outer_anchor_order_or_overlap_invalid")
            continue
        # The path policy binds every qualified source line in this block,
        # including a target that lacked a valid three-line context.  The old
        # block record remains byte-for-byte shaped as before.
        ordered_qualified = [anchors[index] for index in line_order[left_ordinal:right_ordinal + 1] if index in anchors]
        previous_end = None
        contradictory = False
        for item in ordered_qualified:
            interval = item["source_interval_ms"]
            if previous_end is not None and previous_end > interval[0]:
                contradictory = True
                break
            previous_end = interval[1]
        if contradictory:
            _append_block_rejection(entries, "qualified_interior_anchor_order_or_overlap_invalid")
            continue
        low = outer[0]["source_interval_ms"][0]
        high = outer[1]["source_interval_ms"][1]
        window = [max(domain[0], low - SOURCE_CONTEXT_WINDOW_PAD_MS),
                  min(domain[1], high + SOURCE_CONTEXT_WINDOW_PAD_MS)]
        if not window[0] <= low < high <= window[1] or window[1] - window[0] > SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS:
            _append_block_rejection(entries, "source_window_cap_exceeded")
            continue
        segment_indices = line_order[left_ordinal:right_ordinal + 1]
        texts = [lines[index] for index in segment_indices]
        if not _english_text_complete(texts):
            _append_block_rejection(entries, "provider_text_not_verified_en")
            continue
        try:
            units = [alignment_units("en", text) for text in texts]
        except Exception as exc:
            _append_block_rejection(entries, "canonical_lexical_preparation_failed:" + type(exc).__name__)
            continue
        unknown = sorted({unit for segment in units for unit in segment if unit not in dictionary})
        if unknown:
            _append_block_rejection(entries, "dictionary_coverage_missing:" + ",".join(unknown[:12]))
            continue
        total_units = sum(len(segment) for segment in units)
        if total_units > SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS:
            _append_block_rejection(entries, "lexical_unit_cap_exceeded")
            continue
        outputs = []
        for entry in entries:
            target_index = int(entry["target_index"])
            if target_index not in interior:
                _append_block_rejection([entry], "target_not_strictly_between_outer_anchors")
                continue
            outputs.append({"position": int(entry["position"]), "canonical_line_index": target_index,
                            "target_segment_index": segment_indices.index(target_index)})
        if not outputs:
            continue
        output_identity = [{key: item[key] for key in ("position", "canonical_line_index", "target_segment_index")}
                           for item in outputs]
        record_identity = {
            "occurrence_id": prepared.get("occurrence_id"),
            "context_policy": block_context_policy,
            "policy_id": prepared.get("policy_id"),
            "caps": {"max_interior_lines": SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES,
                     "max_window_ms": SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS,
                     "max_lexical_units": SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS},
            "outer_anchor_ids": outer,
            "canonical_interior_indices": interior,
            "source_observation_sha256": prepared.get("source_observation_sha256"),
            "outputs": output_identity,
        }
        if block_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
            record_identity["qualified_source_anchors"] = ordered_qualified
            record_identity["time_banded_decoder_id"] = HUBERTFA_TIME_BAND_DECODER_ID
            record_identity["time_banded_postcheck"] = HUBERTFA_TIME_BAND_POSTCHECK_ID
            record_identity["time_banded_source_pad_ms"] = SOURCE_CONTEXT_WINDOW_PAD_MS
        record_id = json_sha(record_identity)
        record = {
            "record_id": record_id,
            "occurrence_id": prepared.get("occurrence_id"),
            "context_policy": block_context_policy,
            "language": "en",
            "source_audio_path": prepared.get("source_audio_path"),
            "source_audio_sha256": prepared.get("source_audio_sha256"),
            "source_search_domain_end_ms": domain[1],
            "source_window_ms": window,
            "segment_text": texts,
            "segment_lexical_units": units,
            "lexical_units_sha256": json_sha(units),
            "target_outputs": outputs,
            "context": {
                "context_policy": block_context_policy,
                "outer_anchor_candidates": outer,
                "canonical_segment_indices": segment_indices,
                "canonical_interior_indices": interior,
                "source_window_ms": window,
                "source_observation_sha256": prepared.get("source_observation_sha256"),
                "total_lexical_units": total_units,
            },
        }
        if block_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
            record["context"].update({
                "qualified_source_anchors": ordered_qualified,
                "time_banded_decoder_id": HUBERTFA_TIME_BAND_DECODER_ID,
                "time_banded_postcheck": HUBERTFA_TIME_BAND_POSTCHECK_ID,
                "time_banded_source_pad_ms": SOURCE_CONTEXT_WINDOW_PAD_MS,
            })
        records.append(record)
        for output in outputs:
            entry = next(entry for entry in entries if int(entry["position"]) == output["position"])
            entry.update({"status": "ready_for_hubertfa_anchored_block", "block_record_id": record_id,
                          "block_target_segment_index": output["target_segment_index"],
                          "block_outer_anchor_candidates": outer,
                          "block_canonical_interior_indices": interior})
            if block_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
                qualified_indices = {item["canonical_line_index"] for item in ordered_qualified}
                entry["time_band_own_anchor"] = output["canonical_line_index"] in qualified_indices
    path_entries = (ledger if block_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH else [])
    result = {"schema_version": "source-context-hubertfa-anchored-block-1.0",
              "context_policy": block_context_policy,
              "records": records, "anchor_count": len(anchors),
              "missing_three_line_target_count": len(missing)}
    if block_context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH:
        result.update({
            "anchored_path_target_with_own_band_count": sum(
                bool(entry.get("time_band_own_anchor")) for entry in path_entries
                if entry.get("status") == "ready_for_hubertfa_anchored_block"),
            "anchored_path_target_outer_only_band_count": sum(
                not bool(entry.get("time_band_own_anchor")) for entry in path_entries
                if entry.get("status") == "ready_for_hubertfa_anchored_block"),
        })
    return result


def _compact_packet_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the actual source-time evidence a later reader can inspect."""
    return {
        key: candidate.get(key)
        for key in ("candidate_id", "source_interval_ms", "full_context_disambiguated",
                    "source_interval_start_reason", "source_interval_end_reason")
    }


def compact_prepared_occurrence(prepared: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize a compact, non-replayable audit of an in-memory packet lattice.

    Full ``n_best=1024`` packets are retained only while qualifying/inferencing.
    They are deliberately not copied into both the per-occurrence result and
    the global ledger.  Packet computation digests are audit fingerprints, not
    artifact bindings: the full content is not materialized by this function.
    """
    raw_packets = prepared.get("packets")
    packet_by_key: dict[str, Mapping[str, Any]] = {}
    packet_index_by_key: dict[str, int] = {}
    if isinstance(raw_packets, list):
        for row in raw_packets:
            if not isinstance(row, Mapping) or not isinstance(row.get("packet"), Mapping):
                continue
            packet = row["packet"]
            key = str(packet.get("cache_key_sha256") or "")
            if key:
                packet_by_key[key] = packet
                packet_index_by_key[key] = int(row.get("canonical_line_index"))

    used_by_packet: dict[str, set[str]] = {}
    for record in prepared.get("records", []):
        if not isinstance(record, Mapping):
            continue
        context = record.get("context")
        if not isinstance(context, Mapping):
            continue
        selected = context.get("independent_packet_candidates", {})
        if not isinstance(selected, Mapping):
            continue
        for role, line_key in (("left", "left_index"), ("target", "target_index"), ("right", "right_index")):
            item = selected.get(role)
            if not isinstance(item, Mapping):
                continue
            candidate_id = item.get("candidate_id")
            if not isinstance(candidate_id, str):
                continue
            # Candidate IDs are packet-local; identify its packet from the
            # matching canonical index in the same three-line context.
            index = context.get(line_key)
            for key, packet_index in packet_index_by_key.items():
                if packet_index == index:
                    used_by_packet.setdefault(key, set()).add(candidate_id)

    # Anchored block/path records retain their actual source candidates once.
    # The path policy uses every qualified anchor in its bounded block; keeping
    # those small selected observations is necessary to audit the destination
    # mask without copying an n-best packet lattice.
    for source_entry in prepared.get("ledger", []):
        if not isinstance(source_entry, Mapping):
            continue
        for anchor in source_entry.get("block_outer_anchor_candidates", []):
            if not isinstance(anchor, Mapping):
                continue
            key, candidate_id = anchor.get("packet_cache_key_sha256"), anchor.get("candidate_id")
            if isinstance(key, str) and isinstance(candidate_id, str):
                used_by_packet.setdefault(key, set()).add(candidate_id)
    for record in prepared.get("anchored_block_records", []):
        if not isinstance(record, Mapping):
            continue
        context = record.get("context")
        if not isinstance(context, Mapping):
            continue
        for anchor in context.get("qualified_source_anchors", []):
            if not isinstance(anchor, Mapping):
                continue
            key, candidate_id = anchor.get("packet_cache_key_sha256"), anchor.get("candidate_id")
            if isinstance(key, str) and isinstance(candidate_id, str):
                used_by_packet.setdefault(key, set()).add(candidate_id)

    compact_packets = []
    for key, packet in sorted(packet_by_key.items(), key=lambda item: packet_index_by_key[item[0]]):
        candidates = [candidate for candidate in packet.get("candidates", []) if isinstance(candidate, Mapping)]
        candidate_by_id = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
        used_ids = sorted(used_by_packet.get(key, set()))
        compact_packets.append({
            "canonical_line_index": packet_index_by_key[key],
            "packet_cache_key_sha256": key,
            "packet_computation_digest_sha256": json_sha(packet),
            "packet_computation_digest_scope": "complete in-memory packet before compact materialization",
            "packet_computation_retention": "digest_only_not_a_content_addressed_artifact",
            "policy_id": packet.get("policy_id"),
            "selection_reason": packet.get("selection_reason"),
            "selected_candidate_id": packet.get("selected_candidate_id"),
            "candidates_truncated": packet.get("candidates_truncated"),
            "candidate_count_before_truncation": packet.get("coverage", {}).get("candidate_count_before_truncation"),
            "candidate_count_used_by_hubertfa": len(used_ids),
            "used_candidates": [_compact_packet_candidate(candidate_by_id[candidate_id])
                                for candidate_id in used_ids if candidate_id in candidate_by_id],
        })

    packet_summary_by_key = {item["packet_cache_key_sha256"]: item for item in compact_packets}
    compact_ledger = []
    for source_entry in prepared.get("ledger", []):
        if not isinstance(source_entry, Mapping):
            continue
        entry = {key: value for key, value in source_entry.items() if key != "source_packet"}
        packet = source_entry.get("source_packet")
        if isinstance(packet, Mapping):
            packet_key = str(packet.get("cache_key_sha256") or "")
            entry["source_packet_summary"] = packet_summary_by_key.get(packet_key, {
                "packet_cache_key_sha256": packet_key,
                "packet_computation_digest_sha256": json_sha(packet),
                "packet_computation_retention": "digest_only_not_a_content_addressed_artifact",
            })
        compact_ledger.append(entry)

    return {
        "schema_version": "source-context-hubertfa-prepared-occurrence-1.1-compact",
        "policy_id": prepared.get("policy_id"),
        "authority": prepared.get("authority"),
        "automatic_production_mutation_allowed": False,
        "occurrence_id": prepared.get("occurrence_id"),
        "source_observation_sha256": prepared.get("source_observation_sha256"),
        "canonical_lines": prepared.get("canonical_lines", []),
        "source_sequence": prepared.get("source_sequence"),
        "packet_count": prepared.get("packet_count", len(compact_packets)),
        "packet_materialization": {
            "full_candidates_retained_during_computation": True,
            "full_packets_materialized_in_this_artifact": False,
            "compact_packets": compact_packets,
        },
        "records": [dict(record) for record in prepared.get("records", []) if isinstance(record, Mapping)],
        # Bounded block requests are small (at most 14 segments) and are kept
        # once so the batch identity can be reviewed; no packet candidates are
        # duplicated here.
        "anchored_block_records": [dict(record) for record in prepared.get("anchored_block_records", [])
                                   if isinstance(record, Mapping)],
        "ledger": compact_ledger,
    }

def build_batch_request(config: SourceContextHuBERTFAConfig, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    identity = config.identity()
    payload = {**identity, "record_count": len(records), "records": [dict(row) for row in records]}
    payload["batch_id"] = json_sha({key: value for key, value in payload.items() if key != "batch_id"})
    return payload


def execute_batch(config: SourceContextHuBERTFAConfig, records: Sequence[Mapping[str, Any]], *, work_dir: Path,
                  executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Invoke the local adapter once, then validate a total ID-preserving reply."""
    request = build_batch_request(config, records)
    expected_ids = [str(row["record_id"]) for row in records]
    if len(set(expected_ids)) != len(expected_ids):
        raise SourceContextHuBERTFAError("duplicate HuBERTFA source-context record id")
    work_dir.mkdir(parents=True, exist_ok=True)
    request_path, response_path = work_dir / "request.json", work_dir / "response.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stdout_path, stderr_path = work_dir / "adapter.stdout.txt", work_dir / "adapter.stderr.txt"
    if executor is None:
        done = subprocess.run([str(config.runtime_path), "-X", "utf8", str(config.adapter_path), "--request", str(request_path),
                               "--response", str(response_path)], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False)
        stdout_path.write_text(done.stdout, encoding="utf-8"); stderr_path.write_text(done.stderr, encoding="utf-8")
        if done.returncode != 0:
            raise SourceContextHuBERTFAError("HuBERTFA source-context adapter failed; see " + str(stderr_path))
        if not response_path.is_file():
            raise SourceContextHuBERTFAError("HuBERTFA source-context adapter wrote no response")
        response = json.loads(response_path.read_text(encoding="utf-8-sig"))
    else:
        response = executor(request)
        response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stdout_path.write_text("injected executor\n", encoding="utf-8"); stderr_path.write_text("", encoding="utf-8")
    for key in ("protocol_version", "policy_id", "batch_id"):
        if response.get(key) != request.get(key):
            raise SourceContextHuBERTFAError("HuBERTFA source-context response " + key + " mismatch")
    if (response.get("model") != request["model"] or response.get("model_config") != request["model_config"]
            or response.get("model_version") != request["model_version"]
            or response.get("model_vocab") != request["model_vocab"]
            or response.get("dictionary") != request["dictionary"] or response.get("runtime") != request["runtime"]
            or response.get("cpu_execution") != request["cpu_execution"]):
        raise SourceContextHuBERTFAError("HuBERTFA source-context response model/dictionary mismatch")
    if request.get("time_band_decoder") is not None and response.get("time_band_decoder") != request["time_band_decoder"]:
        raise SourceContextHuBERTFAError("HuBERTFA source-context time-band decoder identity mismatch")
    if request.get("dictionary_manifest") is not None and response.get("dictionary_manifest") != request["dictionary_manifest"]:
        raise SourceContextHuBERTFAError("HuBERTFA source-context derived dictionary manifest identity mismatch")
    for key, value in {**config.acoustic_request_fields(), **config.context_request_fields()}.items():
        if response.get(key) != value:
            raise SourceContextHuBERTFAError("HuBERTFA source-context response policy mismatch")
    if response.get("vendor_files") != request["vendor_files"]:
        raise SourceContextHuBERTFAError("HuBERTFA source-context vendor manifest mismatch")
    vendor = response.get("vendor_onnx_infer")
    if not isinstance(vendor, dict) or set(vendor) != {"path", "sha256"} or not Path(vendor["path"]).is_file() or sha256_file(Path(vendor["path"])) != vendor["sha256"]:
        raise SourceContextHuBERTFAError("HuBERTFA source-context vendor identity mismatch")
    returned = response.get("records")
    unaligned_only = isinstance(returned, list) and all(
        isinstance(row, Mapping) and row.get("status") != "aligned" for row in returned)
    provider_runtime = response.get("provider_runtime")
    if (not isinstance(provider_runtime, dict)
            or not isinstance(provider_runtime.get("onnxruntime_version"), str)
            or not provider_runtime["onnxruntime_version"].strip()
            or (provider_runtime.get("actual_providers") != ["CPUExecutionProvider"]
                and not (unaligned_only and provider_runtime.get("actual_providers") == []))):
        raise SourceContextHuBERTFAError("HuBERTFA source-context actual CPU provider mismatch")
    libraries = provider_runtime.get("libraries")
    if (not isinstance(libraries, dict)
            or any(not isinstance(libraries.get(name), str) or not libraries[name].strip()
                   for name in ("onnxruntime", "librosa", "soundfile"))):
        raise SourceContextHuBERTFAError("HuBERTFA source-context runtime library identity missing")
    device_utils = provider_runtime.get("vendor_device_utils")
    if (not isinstance(device_utils, dict) or set(device_utils) != {"path", "sha256"}
            or not Path(device_utils["path"]).is_file()
            or sha256_file(Path(device_utils["path"])) != device_utils["sha256"]):
        raise SourceContextHuBERTFAError("HuBERTFA source-context device utils identity mismatch")
    if not isinstance(returned, list) or [str(row.get("record_id")) for row in returned] != expected_ids:
        raise SourceContextHuBERTFAError("HuBERTFA source-context response record identity/order mismatch")
    return {"request": request, "response": response,
            "artifacts": {"request": {"path": str(request_path), "sha256": sha256_file(request_path)},
                          "response": {"path": str(response_path), "sha256": sha256_file(response_path)},
                          "stdout": {"path": str(stdout_path), "sha256": sha256_file(stdout_path)},
                          "stderr": {"path": str(stderr_path), "sha256": sha256_file(stderr_path)}}}


def finalize_records(records: Sequence[Mapping[str, Any]], response_records: Sequence[Mapping[str, Any]], *,
                     effective_mapping: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract complete contextual target intervals and project them to mix."""
    from lyric_aligner.alignment.forced_projection import _project_interval
    if len(records) != len(response_records):
        raise SourceContextHuBERTFAError("HuBERTFA source-context response count mismatch")
    results: dict[str, dict[str, Any]] = {}
    for record, returned in zip(records, response_records):
        record_id = str(record["record_id"])
        if str(returned.get("record_id")) != record_id:
            raise SourceContextHuBERTFAError("HuBERTFA source-context response record mismatch")
        base = {"record_id": record_id, "status": "unavailable", "source_interval_ms": None,
                "projected_mix_interval_ms": None, "reason": ""}
        if returned.get("status") != "aligned":
            results[record_id] = {**base, "reason": str(returned.get("reason") or "adapter_unaligned")}; continue
        words = returned.get("words")
        if not isinstance(words, list):
            results[record_id] = {**base, "reason": "adapter_words_missing"}; continue
        try:
            finite_words = all(math.isfinite(float(word.get("start"))) and math.isfinite(float(word.get("end")))
                               for word in words if isinstance(word, Mapping)) and all(isinstance(word, Mapping) for word in words)
        except (TypeError, ValueError):
            finite_words = False
        if not finite_words:
            results[record_id] = {**base, "reason": "adapter_nonfinite_word_time"}; continue
        try:
            target_segment_index = int(record.get("target_segment_index", 1))
            interval = alignment_contextual_segment_interval_ms(words, window_start_ms=int(record["source_window_ms"][0]),
                segment_tokens=record["segment_lexical_units"], target_segment_index=target_segment_index)
        except (ForcedAlignmentEvidenceError, TypeError, ValueError) as exc:
            results[record_id] = {**base, "reason": "contextual_interval_rejected:" + type(exc).__name__}; continue
        start, end = interval; window = record["source_window_ms"]
        if not int(window[0]) <= start < end <= int(window[1]):
            results[record_id] = {**base, "reason": "contextual_interval_outside_source_window"}; continue
        projected = _project_interval(effective_mapping, start, end)
        if projected.get("projection_status") != "projected":
            results[record_id] = {**base, "reason": "source_to_mix_projection_failed:" + str(projected.get("projection_reason"))}; continue
        mix = [int(projected["mix_start_ms"]), int(projected["mix_end_ms"])]
        if not 0 <= mix[0] < mix[1]:
            results[record_id] = {**base, "reason": "projected_mix_interval_nonpositive"}; continue
        results[record_id] = {"record_id": record_id, "status": "observed_complete_interval",
                              "source_interval_ms": [start, end], "projected_mix_interval_ms": mix,
                              "reason": "", "words_sha256": json_sha(words),
                              "projection": projected}
    return results


__all__ = ["SOURCE_CONTEXT_HUBERTFA_PROTOCOL", "SOURCE_CONTEXT_HUBERTFA_POLICY_ID",
           "SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID",
           "SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID",
           "SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID",
           "SOURCE_CONTEXT_POLICY_THREE_LINE", "SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK", "SOURCE_CONTEXT_POLICY_ANCHORED_PATH",
           "SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES", "SOURCE_CONTEXT_BLOCK_MAX_SEGMENTS",
           "SOURCE_CONTEXT_BLOCK_MAX_WINDOW_MS", "SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS",
           "SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_DEFAULT",
           "SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP",
           "SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICIES", "acoustic_policy_contract", "context_policy_contract", "SOURCE_CONTEXT_RADIUS",
           "SOURCE_CONTEXT_N_BEST", "SOURCE_CONTEXT_WINDOW_PAD_MS", "SOURCE_CONTEXT_MODES", "SourceContextHuBERTFAConfig",
           "SourceContextHuBERTFAError", "prepare_occurrence", "prepare_anchored_blocks", "compact_prepared_occurrence", "build_batch_request", "execute_batch", "finalize_records", "json_sha",
           "batch_id_matches_request"]
