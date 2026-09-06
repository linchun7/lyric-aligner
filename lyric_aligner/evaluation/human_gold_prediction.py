"""Blind batched backend prediction for locked human acoustic boundary gold.

A production-calibration prediction run is allowed only after a structurally valid
versioned human-gold artifact exists. Gold timing values never cross the backend
process boundary. One backend process receives the complete locked scope for that
gold schema as a batch (30 cases for the full diagnostic gold or 12 for the
production anchor) so large alignment models load once rather than once per cue.
Per-case lexical/alignment failures are explicit ``unaligned`` records and become
``predicted_ms=null``; protocol/model/audio/process failures abort the complete
scope. Pre-human full-population machine consensus is a separate candidate-only
workflow and never creates human gold or production authority.
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
from typing import Any, Callable, Mapping, Sequence

from lyric_aligner.alignment.window_policy import FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID
from lyric_aligner.command_line import CommandLineParseError, split_external_command
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.audio.forced_alignment import padded_window_edge_clamp_reason
from lyric_aligner.evaluation.production_calibration import validate_human_boundary_gold_artifact
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    AlignmentLexicalError,
    alignment_units,
)


HUMAN_GOLD_PREDICTION_PROTOCOL_VERSION = "human-boundary-prediction-batch-1.0"
HUMAN_GOLD_PREDICTION_SCHEMA_VERSION = "human-boundary-backend-predictions-1.1"
LEGACY_HUMAN_GOLD_PREDICTION_SCHEMA_VERSION = "human-boundary-backend-predictions-1.0"
LEGACY_EXPECTED_SCOPE_SIZE = 30


class HumanGoldPredictionError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExternalHumanGoldPredictionConfig:
    command: str
    family: str
    correlation_group: str
    backend_id: str
    backend_version: str
    model_id: str
    model_revision: str
    language: str
    implementation_revision: str = ""
    adapter_contract_revision: str = ""
    audio_basis: str = "final_mix"
    profile_id: str = ""
    window_policy_id: str = FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID
    timeout_seconds: float = 900.0

    def validate(self) -> None:
        required = (
            self.command,
            self.family,
            self.correlation_group,
            self.backend_id,
            self.backend_version,
            self.model_id,
            self.model_revision,
            self.language,
            self.audio_basis,
            self.window_policy_id,
        )
        if any(not str(value or "").strip() for value in required):
            raise HumanGoldPredictionError("human-gold prediction backend identity is incomplete")
        if self.audio_basis != "final_mix":
            raise HumanGoldPredictionError("current human-gold prediction runner accepts final_mix backends only")
        if not math.isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise HumanGoldPredictionError("timeout_seconds must be finite and > 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_command(command: str) -> list[str]:
    try:
        argv = split_external_command(command)
    except CommandLineParseError as exc:
        raise HumanGoldPredictionError("external prediction command cannot be parsed") from exc
    if not argv:
        raise HumanGoldPredictionError("external prediction command is empty")
    executable = shutil.which(argv[0])
    if executable is None:
        raise HumanGoldPredictionError(f"external prediction executable not found: {argv[0]}")
    return [executable, *argv[1:]]


def _case_map(
    cases: Sequence[Mapping[str, Any]],
    *,
    boundary_kind: str,
    expected_scope_size: int,
) -> dict[str, Mapping[str, Any]]:
    if len(cases) != expected_scope_size:
        raise HumanGoldPredictionError("prediction scope does not match the locked human-gold size")
    result: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise HumanGoldPredictionError("locked prediction case must be an object")
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in result:
            raise HumanGoldPredictionError("locked prediction case IDs must be unique/non-empty")
        segments = case.get("segments")
        if not isinstance(segments, list) or not segments:
            raise HumanGoldPredictionError("locked prediction case has no canonical segments")
        if boundary_kind == "internal":
            try:
                index = int(case.get("internal_boundary_index"))
            except (TypeError, ValueError) as exc:
                raise HumanGoldPredictionError("internal prediction case has invalid boundary index") from exc
            if len(segments) < 2 or not 1 <= index < len(segments):
                raise HumanGoldPredictionError("internal prediction case boundary index is outside segment range")
        result[case_id] = case
    return result


def _segment_lexical_units(case: Mapping[str, Any], *, language: str) -> list[list[str]]:
    output: list[list[str]] = []
    for segment in case["segments"]:
        if not isinstance(segment, Mapping):
            raise HumanGoldPredictionError("canonical segment must be an object")
        text = str(segment.get("text") or "").strip()
        if not text:
            raise HumanGoldPredictionError("canonical segment text is empty")
        try:
            output.append(alignment_units(language, text))
        except AlignmentLexicalError as exc:
            raise HumanGoldPredictionError(f"canonical lexical preparation failed: {exc}") from exc
    return output


def _validate_response_record(
    payload: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> int | None:
    expected = {
        "record_id": request["record_id"],
        "boundary_kind": request["boundary_kind"],
        "lexical_units_sha256": request["lexical_units_sha256"],
    }
    for key, value in expected.items():
        if str(payload.get(key) or "") != str(value):
            raise HumanGoldPredictionError(f"backend response record {key} mismatch")
    if payload.get("mix_window_ms") != request["mix_window_ms"]:
        raise HumanGoldPredictionError("backend response record window mismatch")
    status = str(payload.get("status") or "")
    if status == "unaligned":
        if payload.get("predicted_ms") is not None:
            raise HumanGoldPredictionError("unaligned backend record must have null predicted_ms")
        if not str(payload.get("reason") or "").strip():
            raise HumanGoldPredictionError("unaligned backend record must explain its reason")
        return None
    if status != "aligned":
        raise HumanGoldPredictionError("backend response record status is invalid")
    predicted = payload.get("predicted_ms")
    if isinstance(predicted, bool) or not isinstance(predicted, int) or predicted < 0:
        raise HumanGoldPredictionError("aligned backend predicted_ms must be a nonnegative integer")
    start_ms, end_ms = [int(value) for value in request["mix_window_ms"]]
    if not start_ms <= predicted <= end_ms:
        raise HumanGoldPredictionError("backend prediction lies outside requested final-mix window")
    if len(str(payload.get("evidence_sha256") or "")) != 64:
        raise HumanGoldPredictionError("aligned backend response lacks evidence SHA")
    if str(request["boundary_kind"]) in {"start", "end"}:
        edge_reason = padded_window_edge_clamp_reason(
            predicted,
            mix_window_ms=request["mix_window_ms"],
            row_interval_ms=request["row_interval_ms"],
            boundary_kind=str(request["boundary_kind"]),
        )
        if edge_reason:
            return None
    # row_interval_ms is editor provenance, not acoustic ground truth.  For
    # internal boundaries, retain any aligned prediction inside the locked
    # final-mix window so calibration can measure editor-cue timing errors
    # instead of silently converting them into missing coverage.
    return predicted


def execute_human_gold_prediction_scope(
    *,
    human_gold_artifact: Mapping[str, Any],
    locked_cases: Sequence[Mapping[str, Any]],
    final_audio_path: str | Path,
    boundary_kind: str,
    config: ExternalHumanGoldPredictionConfig,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run one backend once over exactly one locked 30-record scope."""

    if boundary_kind not in {"start", "end", "internal"}:
        raise HumanGoldPredictionError("boundary_kind must be start, end, or internal")
    config.validate()
    verified_gold = validate_human_boundary_gold_artifact(human_gold_artifact)
    language = str(config.language or "").strip().lower()
    if language != verified_gold["language_scope"]:
        raise HumanGoldPredictionError("backend language does not match audited human-gold scope")
    audio_path = Path(final_audio_path)
    if not audio_path.is_file():
        raise HumanGoldPredictionError("final mix audio is missing")
    final_audio_sha = sha256_file(audio_path)
    if final_audio_sha != verified_gold["final_audio_sha256"]:
        raise HumanGoldPredictionError("final mix audio identity differs from audited human gold")

    expected_scope_size = int(verified_gold["expected_count_per_kind"])
    case_by_id = _case_map(
        locked_cases,
        boundary_kind=boundary_kind,
        expected_scope_size=expected_scope_size,
    )
    scoped_gold = [
        row for row in verified_gold["records"] if str(row.get("boundary_kind") or "") == boundary_kind
    ]
    if len(scoped_gold) != expected_scope_size:
        raise HumanGoldPredictionError("audited human-gold scope has the wrong record count")
    if {str(row["case_id"]) for row in scoped_gold} != set(case_by_id):
        raise HumanGoldPredictionError("locked cases do not match the audited human-gold scope")

    request_records: list[dict[str, Any]] = []
    for gold_row in scoped_gold:
        case = case_by_id[str(gold_row["case_id"])]
        units = _segment_lexical_units(case, language=language)
        request_record = {
            "record_id": str(gold_row["id"]),
            "case_id": str(gold_row["case_id"]),
            "boundary_kind": boundary_kind,
            "lexical_units_sha256": _sha_json(units),
            "segment_lexical_units": units,
            "mix_window_ms": [int(case["clip_start_ms"]), int(case["clip_end_ms"])],
            "row_interval_ms": [int(case["start_ms"]), int(case["end_ms"])],
            "boundary_index": int(case["internal_boundary_index"]) if boundary_kind == "internal" else None,
        }
        if any("gold" in str(key).casefold() for key in request_record):
            raise HumanGoldPredictionError("backend request record accidentally contains human-gold fields")
        request_records.append(request_record)

    batch_request: dict[str, Any] = {
        "protocol_version": HUMAN_GOLD_PREDICTION_PROTOCOL_VERSION,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "family": config.family,
        "correlation_group": config.correlation_group,
        "audio_basis": config.audio_basis,
        "language": language,
        "backend_profile_id": str(config.profile_id or ""),
        "window_policy_id": config.window_policy_id,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "final_audio_path": str(audio_path),
        "final_audio_sha256": final_audio_sha,
        "record_count": len(request_records),
        "records": request_records,
    }
    if any("gold" in str(key).casefold() for key in batch_request):
        raise HumanGoldPredictionError("backend batch request accidentally contains human-gold fields")

    argv = _resolve_command(config.command)
    runner = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="human-gold-prediction-") as temporary:
        root = Path(temporary)
        request_path = root / "request.json"
        response_path = root / "response.json"
        request_path.write_text(json.dumps(batch_request, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        completed = runner(
            [*argv, "--request", str(request_path), "--response", str(response_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=float(config.timeout_seconds),
        )
        if int(getattr(completed, "returncode", -1)) != 0:
            stderr = str(getattr(completed, "stderr", "") or "")
            stdout = str(getattr(completed, "stdout", "") or "")
            raise HumanGoldPredictionError(
                "external backend prediction process failed: " + (stderr or stdout)[-2000:]
            )
        if not response_path.is_file():
            raise HumanGoldPredictionError("external backend prediction produced no response")
        try:
            response = json.loads(response_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HumanGoldPredictionError("external backend prediction response is invalid JSON") from exc
    if not isinstance(response, Mapping):
        raise HumanGoldPredictionError("external backend prediction response must be an object")

    expected_batch_identity = {
        "protocol_version": HUMAN_GOLD_PREDICTION_PROTOCOL_VERSION,
        "backend_id": config.backend_id,
        "backend_version": config.backend_version,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "family": config.family,
        "correlation_group": config.correlation_group,
        "audio_basis": config.audio_basis,
        "language": language,
        "backend_profile_id": str(config.profile_id or ""),
        "window_policy_id": config.window_policy_id,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
        "final_audio_sha256": final_audio_sha,
    }
    for key, value in expected_batch_identity.items():
        if str(response.get(key) or "") != str(value):
            raise HumanGoldPredictionError(f"backend batch response {key} mismatch")
    response_records = response.get("records")
    if not isinstance(response_records, list) or len(response_records) != expected_scope_size:
        raise HumanGoldPredictionError("backend batch response has the wrong record count")
    if int(response.get("record_count") or -1) != expected_scope_size:
        raise HumanGoldPredictionError("backend batch response record_count mismatch")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in response_records:
        if not isinstance(row, Mapping):
            raise HumanGoldPredictionError("backend batch response record must be an object")
        record_id = str(row.get("record_id") or "")
        if not record_id or record_id in by_id:
            raise HumanGoldPredictionError("backend batch response IDs must be unique/non-empty")
        by_id[record_id] = row
    if set(by_id) != {str(row["record_id"]) for row in request_records}:
        raise HumanGoldPredictionError("backend batch response record set differs from request")

    request_by_id = {str(row["record_id"]): row for row in request_records}
    predictions = {
        record_id: _validate_response_record(by_id[record_id], request=request_by_id[record_id])
        for record_id in request_by_id
    }
    rows = [
        {"id": str(row["id"]), "predicted_ms": predictions[str(row["id"])]}
        for row in scoped_gold
    ]
    scope = {
        "boundary_kind": boundary_kind,
        "audio_basis": config.audio_basis,
        "language": language,
        "backend_version": config.backend_version,
        "backend_profile_id": str(config.profile_id or ""),
        "window_policy_id": config.window_policy_id,
        "model_id": config.model_id,
        "model_revision": config.model_revision,
        "lexical_id": ALIGNMENT_LEXICAL_ID,
        "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
    }
    provenance_complete = all(
        len(str(value or "")) == 64
        and all(ch in "0123456789abcdef" for ch in str(value or ""))
        for value in (config.implementation_revision, config.adapter_contract_revision)
    )
    if provenance_complete:
        scope["implementation_revision"] = config.implementation_revision
        scope["adapter_contract_revision"] = config.adapter_contract_revision
    artifact: dict[str, Any] = {
        "schema_version": (
            HUMAN_GOLD_PREDICTION_SCHEMA_VERSION
            if provenance_complete
            else LEGACY_HUMAN_GOLD_PREDICTION_SCHEMA_VERSION
        ),
        "selection_lock_sha256": verified_gold["selection_lock_sha256"],
        "backend_id": config.backend_id,
        "family": config.family,
        "correlation_group": config.correlation_group,
        "scope": scope,
        "record_count": len(rows),
        "records": rows,
        "predictions_sha256": _sha_json(rows),
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact
