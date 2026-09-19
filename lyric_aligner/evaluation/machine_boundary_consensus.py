"""Machine-only boundary candidates for human listening and diagnostics.

This module deliberately has no production-authority path. It evaluates the
pre-model locked 60-clip benchmark population with two direct lexical aligners
plus a local final-mix signal detector, then emits a conservative candidate for
the human audit UI. Human gold is neither required nor consumed.

For outer start/end boundaries the signal detector is an independent corroborator:
its search center comes only from the editor boundary prior, never from either
direct aligner. For internal boundaries a non-semantic detector cannot identify
which syllable transition is the canonical split, so its editor-midpoint probe is
diagnostic only and cannot upgrade direct-aligner agreement. All of this remains
machine evidence and can never substitute for the human authority layer.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from lyric_aligner.alignment.production_backend_profiles import (
    ProductionBoundaryBackendProfile,
    get_production_boundary_backend_profile,
)
from lyric_aligner.alignment.window_policy import FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID
from lyric_aligner.audio.boundary_acoustic import (
    ACOUSTIC_BOUNDARY_BACKEND_ID,
    analyze_final_mix_boundary,
)
from lyric_aligner.command_line import CommandLineParseError, split_external_command
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.audio.forced_alignment import padded_window_edge_clamp_reason
from lyric_aligner.evaluation.human_gold_prediction import (
    HUMAN_GOLD_PREDICTION_PROTOCOL_VERSION,
)
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    alignment_units,
)


MACHINE_BOUNDARY_CONSENSUS_SCHEMA_VERSION = "human-boundary-machine-consensus-1.0"
MACHINE_BOUNDARY_CANDIDATE_POLICY_ID = "two-direct-aligners-plus-local-acoustic-v1"
DEFAULT_PROFILE_IDS = ("sofa_mandarin_v1", "hubertfa_mandarin_v1")
BOUNDARY_KINDS = ("start", "end", "internal")


class MachineBoundaryConsensusError(ValueError):
    pass


@dataclass(frozen=True)
class MachineConsensusPolicy:
    direct_high_agreement_ms: int = 120
    direct_usable_agreement_ms: int = 180
    acoustic_support_ms: int = 180
    hard_disagreement_ms: int = 300

    def validate(self) -> None:
        values = (
            self.direct_high_agreement_ms,
            self.direct_usable_agreement_ms,
            self.acoustic_support_ms,
            self.hard_disagreement_ms,
        )
        if any(value < 0 for value in values):
            raise MachineBoundaryConsensusError("machine consensus thresholds must be nonnegative")
        if self.direct_high_agreement_ms > self.direct_usable_agreement_ms:
            raise MachineBoundaryConsensusError("direct high-agreement threshold exceeds usable threshold")
        if self.direct_usable_agreement_ms > self.hard_disagreement_ms:
            raise MachineBoundaryConsensusError("direct usable threshold exceeds hard-disagreement threshold")


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_command(command: str) -> list[str]:
    try:
        argv = split_external_command(command)
    except CommandLineParseError as exc:
        raise MachineBoundaryConsensusError("external prediction command cannot be parsed") from exc
    if not argv:
        raise MachineBoundaryConsensusError("external prediction command is empty")
    executable = shutil.which(argv[0])
    if executable is None:
        raise MachineBoundaryConsensusError(f"external prediction executable not found: {argv[0]}")
    return [executable, *argv[1:]]


def _case_map(cases: Sequence[Mapping[str, Any]], *, boundary_kind: str) -> dict[str, Mapping[str, Any]]:
    if not cases:
        raise MachineBoundaryConsensusError("locked machine-candidate scope is empty")
    result: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise MachineBoundaryConsensusError("locked machine-candidate case must be an object")
        case_id = str(case.get("case_id") or "").strip()
        if len(case_id) != 64 or case_id in result:
            raise MachineBoundaryConsensusError("locked machine-candidate case IDs are invalid")
        segments = case.get("segments")
        if not isinstance(segments, list) or not segments:
            raise MachineBoundaryConsensusError("locked machine-candidate case has no canonical segments")
        if boundary_kind == "internal":
            try:
                index = int(case.get("internal_boundary_index"))
            except (TypeError, ValueError) as exc:
                raise MachineBoundaryConsensusError("internal machine-candidate case has invalid boundary index") from exc
            if len(segments) < 2 or not 1 <= index < len(segments):
                raise MachineBoundaryConsensusError("internal boundary index lies outside canonical segments")
        result[case_id] = case
    return result


def _units(case: Mapping[str, Any], *, language: str) -> list[list[str]]:
    output: list[list[str]] = []
    for segment in case["segments"]:
        if not isinstance(segment, Mapping):
            raise MachineBoundaryConsensusError("canonical segment must be an object")
        text = str(segment.get("text") or "").strip()
        if not text:
            raise MachineBoundaryConsensusError("canonical segment text is empty")
        output.append(alignment_units(language, text))
    return output


def _validate_response_record(payload: Mapping[str, Any], request: Mapping[str, Any]) -> int | None:
    for key in ("record_id", "boundary_kind", "lexical_units_sha256"):
        if str(payload.get(key) or "") != str(request[key]):
            raise MachineBoundaryConsensusError(f"backend response record {key} mismatch")
    if payload.get("mix_window_ms") != request["mix_window_ms"]:
        raise MachineBoundaryConsensusError("backend response record window mismatch")
    status = str(payload.get("status") or "")
    if status == "unaligned":
        if payload.get("predicted_ms") is not None:
            raise MachineBoundaryConsensusError("unaligned backend record must have null predicted_ms")
        return None
    if status != "aligned":
        raise MachineBoundaryConsensusError("backend response record status is invalid")
    predicted = payload.get("predicted_ms")
    if isinstance(predicted, bool) or not isinstance(predicted, int) or predicted < 0:
        raise MachineBoundaryConsensusError("aligned backend predicted_ms must be a nonnegative integer")
    start_ms, end_ms = [int(value) for value in request["mix_window_ms"]]
    if not start_ms <= predicted <= end_ms:
        raise MachineBoundaryConsensusError("backend prediction lies outside requested final-mix window")
    if str(request["boundary_kind"]) in {"start", "end"}:
        edge_reason = padded_window_edge_clamp_reason(
            predicted,
            mix_window_ms=request["mix_window_ms"],
            row_interval_ms=request["row_interval_ms"],
            boundary_kind=str(request["boundary_kind"]),
        )
        if edge_reason:
            return None
    # For internal boundaries, row_interval_ms is an editor reference only.
    # The locked final-mix window is the hard acoustic contract: a true sung
    # segment transition can legitimately fall outside the editor cue when the
    # editor timing itself is what the benchmark is intended to measure.
    return int(predicted)


def execute_locked_prediction_scope(
    *,
    locked_cases: Sequence[Mapping[str, Any]],
    final_audio_path: str | Path,
    final_audio_sha256: str,
    boundary_kind: str,
    language: str,
    resolved_profile: Mapping[str, Any],
    runner: Callable[..., Any] | None = None,
    timeout_seconds: float = 900.0,
) -> dict[str, int | None]:
    """Run one direct aligner on a locked case population without any human gold."""

    if boundary_kind not in BOUNDARY_KINDS:
        raise MachineBoundaryConsensusError("boundary_kind must be start, end, or internal")
    code = str(language or "").strip().lower()
    if not code:
        raise MachineBoundaryConsensusError("language must be non-empty")
    if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
        raise MachineBoundaryConsensusError("timeout_seconds must be finite and positive")
    audio_path = Path(final_audio_path)
    if not audio_path.is_file() or sha256_file(audio_path) != str(final_audio_sha256):
        raise MachineBoundaryConsensusError("final mix identity differs from locked benchmark")

    cases = _case_map(locked_cases, boundary_kind=boundary_kind)
    request_records: list[dict[str, Any]] = []
    for case in cases.values():
        lexical = _units(case, language=code)
        record_id = f"{case['case_id']}:{boundary_kind}"
        request = {
            "record_id": record_id,
            "case_id": str(case["case_id"]),
            "boundary_kind": boundary_kind,
            "lexical_units_sha256": _sha_json(lexical),
            "segment_lexical_units": lexical,
            "mix_window_ms": [int(case["clip_start_ms"]), int(case["clip_end_ms"])],
            "row_interval_ms": [int(case["start_ms"]), int(case["end_ms"])],
            "boundary_index": int(case["internal_boundary_index"]) if boundary_kind == "internal" else None,
        }
        if any("gold" in str(key).casefold() for key in request):
            raise MachineBoundaryConsensusError("machine request accidentally contains human-gold fields")
        request_records.append(request)

    command = str(resolved_profile.get("command") or "").strip()
    argv = _resolve_command(command)
    batch_request = {
        "protocol_version": HUMAN_GOLD_PREDICTION_PROTOCOL_VERSION,
        "backend_id": str(resolved_profile["backend_id"]),
        "backend_version": str(resolved_profile["backend_version"]),
        "model_id": str(resolved_profile["model_id"]),
        "model_revision": str(resolved_profile["model_revision"]),
        "family": str(resolved_profile["family"]),
        "correlation_group": str(resolved_profile["correlation_group"]),
        "audio_basis": "final_mix",
        "language": code,
        "backend_profile_id": str(resolved_profile["profile_id"]),
        "window_policy_id": str(resolved_profile.get("window_policy_id") or FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID),
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "final_audio_path": str(audio_path),
        "final_audio_sha256": str(final_audio_sha256),
        "record_count": len(request_records),
        "records": request_records,
    }
    runner = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="machine-boundary-candidate-") as temporary:
        root = Path(temporary)
        request_path = root / "request.json"
        response_path = root / "response.json"
        request_path.write_text(json.dumps(batch_request, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        completed = runner(
            [*argv, "--request", str(request_path), "--response", str(response_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=float(timeout_seconds),
        )
        if int(getattr(completed, "returncode", -1)) != 0:
            detail = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "")[-2000:]
            raise MachineBoundaryConsensusError("external machine-candidate backend failed: " + detail)
        if not response_path.is_file():
            raise MachineBoundaryConsensusError("external machine-candidate backend produced no response")
        response = json.loads(response_path.read_text(encoding="utf-8-sig"))
    if not isinstance(response, Mapping):
        raise MachineBoundaryConsensusError("external backend response must be an object")
    expected_identity = {
        "protocol_version": HUMAN_GOLD_PREDICTION_PROTOCOL_VERSION,
        "backend_id": resolved_profile["backend_id"],
        "backend_version": resolved_profile["backend_version"],
        "model_id": resolved_profile["model_id"],
        "model_revision": resolved_profile["model_revision"],
        "family": resolved_profile["family"],
        "correlation_group": resolved_profile["correlation_group"],
        "audio_basis": "final_mix",
        "language": code,
        "backend_profile_id": resolved_profile["profile_id"],
        "window_policy_id": resolved_profile.get("window_policy_id") or FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "final_audio_sha256": final_audio_sha256,
    }
    for key, expected in expected_identity.items():
        if str(response.get(key) or "") != str(expected):
            raise MachineBoundaryConsensusError(f"machine-candidate backend response {key} mismatch")
    response_records = response.get("records")
    if not isinstance(response_records, list) or len(response_records) != len(request_records):
        raise MachineBoundaryConsensusError("machine-candidate backend response count mismatch")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in response_records:
        if not isinstance(row, Mapping):
            raise MachineBoundaryConsensusError("machine-candidate backend response record must be an object")
        record_id = str(row.get("record_id") or "")
        if not record_id or record_id in by_id:
            raise MachineBoundaryConsensusError("machine-candidate backend response IDs are invalid")
        by_id[record_id] = row
    request_by_id = {str(row["record_id"]): row for row in request_records}
    if set(by_id) != set(request_by_id):
        raise MachineBoundaryConsensusError("machine-candidate backend response IDs differ from request")
    return {
        record_id: _validate_response_record(by_id[record_id], request_by_id[record_id])
        for record_id in request_by_id
    }


def _rounded_median(values: Sequence[int]) -> int:
    if not values:
        raise MachineBoundaryConsensusError("cannot take median of empty candidate set")
    return int(round(float(median(values))))


def _build_consensus_record(
    *,
    case: Mapping[str, Any],
    population: str,
    boundary_kind: str,
    direct_predictions: Mapping[str, int | None],
    final_audio_path: Path,
    policy: MachineConsensusPolicy,
) -> dict[str, Any]:
    record_id = f"{case['case_id']}:{boundary_kind}"
    available_direct = {
        profile_id: int(value)
        for profile_id, value in direct_predictions.items()
        if value is not None
    }
    direct_values = list(available_direct.values())
    direct_spread = max(direct_values) - min(direct_values) if len(direct_values) >= 2 else None

    # The signal-only observer must not inherit a SOFA/HuBERTFA prediction as its
    # search center.  Outer boundaries use the editor boundary prior; internal
    # uses only the owning cue midpoint because a non-semantic detector cannot
    # know which syllable transition is the canonical internal split.
    if boundary_kind == "start":
        acoustic_center = int(case["start_ms"])
        acoustic_role = "independent_outer_corroborator"
    elif boundary_kind == "end":
        acoustic_center = int(case["end_ms"])
        acoustic_role = "independent_outer_corroborator"
    else:
        acoustic_center = int(round((int(case["start_ms"]) + int(case["end_ms"])) / 2))
        acoustic_role = "nonsemantic_internal_context_diagnostic"

    acoustic_kind = "end" if boundary_kind == "end" else "start"
    acoustic = analyze_final_mix_boundary(
        final_audio_path,
        editor_ms=acoustic_center,
        boundary_kind=acoustic_kind,
    )
    acoustic_ms = int(acoustic["boundary_ms"]) if acoustic.get("available") else None
    direct_median = _rounded_median(direct_values) if direct_values else None
    # Direct lexical aligners initialize the listening candidate whenever any are
    # available.  The independent acoustic observer may corroborate confidence but
    # does not drag a semantic candidate toward an unrelated musical onset.
    candidate_ms = direct_median if direct_median is not None else acoustic_ms
    acoustic_delta = (
        abs(acoustic_ms - direct_median)
        if acoustic_ms is not None and direct_median is not None
        else None
    )
    all_values = [*direct_values]
    if acoustic_ms is not None:
        all_values.append(acoustic_ms)
    all_spread = max(all_values) - min(all_values) if len(all_values) >= 2 else None

    if len(direct_values) >= 2 and direct_spread is not None and direct_spread > policy.hard_disagreement_ms:
        status = "disagreement"
        reason = "direct_aligners_disagree"
    elif (
        boundary_kind != "internal"
        and len(direct_values) >= 2
        and direct_spread is not None
        and direct_spread <= policy.direct_high_agreement_ms
        and acoustic_ms is not None
        and acoustic_delta is not None
        and acoustic_delta <= policy.acoustic_support_ms
    ):
        status = "high_consensus"
        reason = "two_direct_aligners_and_independent_editor_centered_local_acoustic_agree"
    elif len(direct_values) >= 2 and direct_spread is not None and direct_spread <= policy.direct_usable_agreement_ms:
        status = "aligner_consensus"
        reason = (
            "two_direct_aligners_agree_internal_acoustic_is_nonsemantic_diagnostic"
            if boundary_kind == "internal"
            else "two_direct_aligners_agree_acoustic_not_required_for_candidate"
        )
    elif len(direct_values) >= 1:
        status = "weak_consensus"
        reason = "partial_direct_alignment_support"
    elif acoustic_ms is not None:
        status = "acoustic_only"
        reason = (
            "nonsemantic_internal_acoustic_only_no_semantic_split_identity"
            if boundary_kind == "internal"
            else "direct_aligners_unavailable_local_acoustic_only"
        )
    else:
        status = "unresolved"
        reason = "no_machine_boundary_available"

    return {
        "id": record_id,
        "case_id": str(case["case_id"]),
        "population": population,
        "boundary_kind": boundary_kind,
        "track": str(case["track"]),
        "cue_number": int(case["cue_number"]),
        "candidate_ms": candidate_ms,
        "consensus_status": status,
        "reason": reason,
        "direct_predictions_ms": dict(sorted(available_direct.items())),
        "direct_prediction_count": len(direct_values),
        "direct_spread_ms": direct_spread,
        "acoustic_backend_id": ACOUSTIC_BOUNDARY_BACKEND_ID,
        "acoustic_role": acoustic_role,
        "acoustic_search_center_ms": acoustic_center,
        "acoustic_prediction_ms": acoustic_ms,
        "acoustic_available": bool(acoustic.get("available")),
        "acoustic_confidence": acoustic.get("confidence"),
        "acoustic_uncertainty_ms": acoustic.get("uncertainty_ms"),
        "acoustic_delta_from_direct_median_ms": acoustic_delta,
        "all_available_spread_ms": all_spread,
    }


def build_machine_boundary_consensus(
    *,
    full_lock: Mapping[str, Any],
    repository_root: str | Path,
    profile_ids: Sequence[str] = DEFAULT_PROFILE_IDS,
    profile_getter: Callable[[str], ProductionBoundaryBackendProfile] = get_production_boundary_backend_profile,
    prediction_executor: Callable[..., dict[str, int | None]] = execute_locked_prediction_scope,
    policy: MachineConsensusPolicy | None = None,
) -> dict[str, Any]:
    """Run all 90 locked boundary points and return machine-only candidate evidence."""

    policy = policy or MachineConsensusPolicy()
    policy.validate()
    inputs = full_lock.get("inputs") if isinstance(full_lock, Mapping) else None
    populations = full_lock.get("populations") if isinstance(full_lock, Mapping) else None
    if not isinstance(inputs, Mapping) or not isinstance(populations, Mapping):
        raise MachineBoundaryConsensusError("full benchmark lock lacks inputs/populations")
    language = str(inputs.get("language_scope") or "").strip().lower()
    final_audio = Path(str(inputs.get("final_audio_path") or ""))
    final_audio_sha = str(inputs.get("final_audio_sha256") or "")
    if not language or not final_audio.is_file() or sha256_file(final_audio) != final_audio_sha:
        raise MachineBoundaryConsensusError("locked final-audio provenance is invalid")
    repository = Path(repository_root).resolve()

    profile_ids = tuple(str(value or "").strip() for value in profile_ids)
    if len(profile_ids) < 2 or len(set(profile_ids)) != len(profile_ids) or any(not value for value in profile_ids):
        raise MachineBoundaryConsensusError("machine consensus requires at least two unique direct-aligner profiles")
    resolved_profiles = [profile_getter(profile_id).resolve(repository, language=language) for profile_id in profile_ids]
    if len({str(row["correlation_group"]) for row in resolved_profiles}) < 2:
        raise MachineBoundaryConsensusError("direct-aligner profiles are not independent correlation groups")

    outer = populations.get("outer")
    internal = populations.get("internal")
    if not isinstance(outer, Mapping) or not isinstance(internal, Mapping):
        raise MachineBoundaryConsensusError("locked outer/internal populations are missing")
    outer_cases = outer.get("records")
    internal_cases = internal.get("records")
    if not isinstance(outer_cases, list) or len(outer_cases) != 30:
        raise MachineBoundaryConsensusError("full benchmark outer population must contain 30 cases")
    if not isinstance(internal_cases, list) or len(internal_cases) != 30:
        raise MachineBoundaryConsensusError("full benchmark internal population must contain 30 cases")

    predictions: dict[str, dict[str, dict[str, int | None]]] = {}
    for boundary_kind in BOUNDARY_KINDS:
        cases = internal_cases if boundary_kind == "internal" else outer_cases
        predictions[boundary_kind] = {}
        for resolved in resolved_profiles:
            profile_id = str(resolved["profile_id"])
            predictions[boundary_kind][profile_id] = prediction_executor(
                locked_cases=cases,
                final_audio_path=final_audio,
                final_audio_sha256=final_audio_sha,
                boundary_kind=boundary_kind,
                language=language,
                resolved_profile=resolved,
            )

    records: list[dict[str, Any]] = []
    for boundary_kind in BOUNDARY_KINDS:
        cases = internal_cases if boundary_kind == "internal" else outer_cases
        population = "internal" if boundary_kind == "internal" else "outer"
        for case in cases:
            record_id = f"{case['case_id']}:{boundary_kind}"
            direct = {
                profile_id: predictions[boundary_kind][profile_id].get(record_id)
                for profile_id in profile_ids
            }
            records.append(
                _build_consensus_record(
                    case=case,
                    population=population,
                    boundary_kind=boundary_kind,
                    direct_predictions=direct,
                    final_audio_path=final_audio,
                    policy=policy,
                )
            )

    if len(records) != 90 or len({row["id"] for row in records}) != 90:
        raise MachineBoundaryConsensusError("machine consensus must materialize exactly 90 unique boundary records")
    status_counts: dict[str, int] = {}
    for row in records:
        status = str(row["consensus_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    artifact: dict[str, Any] = {
        "schema_version": MACHINE_BOUNDARY_CONSENSUS_SCHEMA_VERSION,
        "authority": "machine_candidate_only_never_human_gold",
        "policy_id": MACHINE_BOUNDARY_CANDIDATE_POLICY_ID,
        "policy": dict(policy.__dict__),
        "selection_lock_sha256": str(full_lock.get("lock_sha256") or ""),
        "final_audio_sha256": final_audio_sha,
        "language_scope": language,
        "profile_ids": list(profile_ids),
        "profile_identities": [
            {
                "profile_id": row["profile_id"],
                "backend_id": row["backend_id"],
                "family": row["family"],
                "correlation_group": row["correlation_group"],
                "model_id": row["model_id"],
                "model_revision": row["model_revision"],
            }
            for row in resolved_profiles
        ],
        "third_method": {
            "backend_id": ACOUSTIC_BOUNDARY_BACKEND_ID,
            "family": "local_final_mix_signal_boundary",
            "role": "outer_candidate_corroboration_and_internal_context_diagnostic_only",
            "outer_scope_note": "outer acoustic search is centered only on the editor start/end prior and does not consume direct-aligner predictions",
            "internal_scope_note": "internal acoustic search is centered only on the owning editor-cue midpoint; without lexical identity it is a nonsemantic context diagnostic and can never upgrade internal aligner consensus to high_consensus",
        },
        "record_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "records": records,
    }
    artifact["records_sha256"] = _sha_json(records)
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact
