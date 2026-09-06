"""Shared production human-gold fixtures for boundary authority tests."""

from __future__ import annotations

import hashlib
import json

from lyric_aligner.evaluation.human_boundary_anchor import (
    HUMAN_ANCHOR_AUDIT_UI_REVISION,
    HUMAN_ANCHOR_AUDIT_UX_REVISION,
    HUMAN_ANCHOR_GOLD_AUTHORITY,
    HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
    HUMAN_ANCHOR_PRODUCTION_POLICY,
    HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
)
from lyric_aligner.evaluation.production_calibration import (
    HUMAN_BOUNDARY_GOLD_AUTHORITY,
    HUMAN_BOUNDARY_GOLD_SCHEMA_VERSION,
    evaluate_human_gold_backend_calibration,
)


def _sha_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _case_id(prefix: str, index: int) -> str:
    return hashlib.sha256(f"{prefix}-{index}".encode("utf-8")).hexdigest()


def human_gold_artifact(
    *,
    language_scope: str = "en",
    final_audio_sha256: str = "5" * 64,
) -> dict:
    records = []
    for index in range(30):
        partition = "calibration" if index < 20 else "holdout"
        track = f"track-{index % 5}"
        case_id = _case_id("outer", index)
        for kind, offset in (("start", 0), ("end", 700)):
            records.append(
                {
                    "id": f"{case_id}:{kind}",
                    "kind": "boundary",
                    "boundary_kind": kind,
                    "gold_ms": 10000 + index * 1000 + offset,
                    "gold_uncertainty_ms": 0,
                    "partition": partition,
                    "track": track,
                    "case_id": case_id,
                    "cue_number": index + 1,
                    "population": "outer",
                }
            )
    for index in range(30):
        partition = "calibration" if index < 20 else "holdout"
        track = f"track-{index % 5}"
        case_id = _case_id("internal", index)
        records.append(
            {
                "id": f"{case_id}:internal",
                "kind": "boundary",
                "boundary_kind": "internal",
                "gold_ms": 50000 + index * 1000,
                "gold_uncertainty_ms": 0,
                "partition": partition,
                "track": track,
                "case_id": case_id,
                "cue_number": index + 101,
                "population": "internal",
                "internal_boundary_index": 1,
            }
        )
    artifact = {
        "schema_version": HUMAN_BOUNDARY_GOLD_SCHEMA_VERSION,
        "authority": HUMAN_BOUNDARY_GOLD_AUTHORITY,
        "language_scope": language_scope,
        "selection_lock_sha256": "1" * 64,
        "selection_lock_file_sha256": "2" * 64,
        "outer_audit_csv_sha256": "3" * 64,
        "internal_audit_csv_sha256": "4" * 64,
        "final_audio_sha256": final_audio_sha256,
        "record_count": 90,
        "boundary_kind_counts": {"start": 30, "end": 30, "internal": 30},
        "population_counts": {"outer": 60, "internal": 30},
        "records": records,
    }
    artifact["gold_sha256"] = _sha_json(records)
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def production_calibration(
    *,
    backend_id: str,
    family: str,
    correlation_group: str,
    boundary_kind: str,
    audio_basis: str,
    language: str,
    backend_version: str,
    model_id: str,
    model_revision: str,
    implementation_revision: str = "a" * 64,
    adapter_contract_revision: str = "b" * 64,
    lexical_id: str | None = None,
    lexical_revision: str | None = None,
    backend_profile_id: str = "fixture-profile-v1",
    window_policy_id: str = "fixture-window-policy-v1",
    error_ms: int = 0,
) -> dict:
    gold = human_gold_artifact(language_scope=language)
    scoped = [row for row in gold["records"] if row["boundary_kind"] == boundary_kind]
    scope = {
        "boundary_kind": boundary_kind,
        "audio_basis": audio_basis,
        "language": language,
        "backend_version": backend_version,
        "backend_profile_id": backend_profile_id,
        "window_policy_id": window_policy_id,
        "model_id": model_id,
        "model_revision": model_revision,
        "implementation_revision": implementation_revision,
        "adapter_contract_revision": adapter_contract_revision,
    }
    if lexical_id is not None:
        scope["lexical_id"] = lexical_id
    if lexical_revision is not None:
        scope["lexical_revision"] = lexical_revision
    return evaluate_human_gold_backend_calibration(
        backend_id=backend_id,
        family=family,
        correlation_group=correlation_group,
        scope=scope,
        human_gold_artifact=gold,
        predictions_ms={row["id"]: row["gold_ms"] + error_ms for row in scoped},
    )


def human_anchor_gold_artifact(
    *,
    language_scope: str = "en",
    final_audio_sha256: str = "5" * 64,
    selection_lock_sha256: str = "1" * 64,
) -> dict:
    """Build a structurally valid 12+12 Human Anchor Gold fixture."""

    records = []
    for index in range(12):
        partition = "calibration" if index < 8 else "holdout"
        track = f"outer-track-{index}"
        case_id = _case_id("anchor-outer", index)
        for kind, offset in (("start", 0), ("end", 700)):
            records.append(
                {
                    "id": f"{case_id}:{kind}",
                    "kind": "boundary",
                    "boundary_kind": kind,
                    "gold_ms": 10_000 + index * 1_000 + offset,
                    "gold_uncertainty_ms": 50,
                    "gold_clarity": "clear",
                    "partition": partition,
                    "track": track,
                    "case_id": case_id,
                    "cue_number": index + 1,
                    "population": "outer",
                }
            )
    for index in range(12):
        partition = "calibration" if index < 8 else "holdout"
        case_id = _case_id("anchor-internal", index)
        records.append(
            {
                "id": f"{case_id}:internal",
                "kind": "boundary",
                "boundary_kind": "internal",
                "gold_ms": 100_000 + index * 10_000,
                "gold_uncertainty_ms": 50,
                "gold_clarity": "clear",
                "partition": partition,
                "track": f"internal-track-{index}",
                "case_id": case_id,
                "cue_number": index + 101,
                "population": "internal",
                "internal_boundary_index": 1,
            }
        )
    artifact = {
        "schema_version": HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
        "authority": HUMAN_ANCHOR_GOLD_AUTHORITY,
        "language_scope": language_scope,
        "selection_lock_sha256": selection_lock_sha256,
        "selection_lock_file_sha256": "2" * 64,
        "source_full_selection_lock_sha256": "6" * 64,
        "outer_audit_csv_sha256": "3" * 64,
        "internal_audit_csv_sha256": "4" * 64,
        "audit_confirmation": {
            "recheck_file_sha256": "7" * 64,
            "recheck_schema_version": "human-boundary-anchor-audit-recheck-1.0",
            "required_ui_revision": HUMAN_ANCHOR_AUDIT_UI_REVISION,
            "required_ui_ux_revision": HUMAN_ANCHOR_AUDIT_UX_REVISION,
            "last_confirmed_ui_revision": HUMAN_ANCHOR_AUDIT_UI_REVISION,
            "last_confirmed_ui_ux_revision": HUMAN_ANCHOR_AUDIT_UX_REVISION,
            "pending_case_count": 0,
            "confirmed_case_count": 24,
        },
        "final_audio_sha256": final_audio_sha256,
        "uncertainty_policy_id": HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
        "production_policy": dict(HUMAN_ANCHOR_PRODUCTION_POLICY.__dict__),
        "record_count": 36,
        "boundary_kind_counts": {"start": 12, "end": 12, "internal": 12},
        "population_counts": {"outer": 24, "internal": 12},
        "records": records,
    }
    artifact["gold_sha256"] = _sha_json(records)
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def production_anchor_calibration(
    *,
    human_gold: dict,
    backend_id: str,
    family: str,
    correlation_group: str,
    language: str,
    backend_version: str,
    backend_profile_id: str,
    window_policy_id: str,
    model_id: str,
    model_revision: str,
    implementation_revision: str,
    adapter_contract_revision: str,
    lexical_id: str,
    lexical_revision: str,
    predictions_ms: dict[str, int | None],
) -> dict:
    scope = {
        "boundary_kind": "internal",
        "audio_basis": "final_mix",
        "language": language,
        "backend_version": backend_version,
        "backend_profile_id": backend_profile_id,
        "window_policy_id": window_policy_id,
        "model_id": model_id,
        "model_revision": model_revision,
        "implementation_revision": implementation_revision,
        "adapter_contract_revision": adapter_contract_revision,
        "lexical_id": lexical_id,
        "lexical_revision": lexical_revision,
    }
    return evaluate_human_gold_backend_calibration(
        backend_id=backend_id,
        family=family,
        correlation_group=correlation_group,
        scope=scope,
        human_gold_artifact=human_gold,
        predictions_ms=predictions_ms,
    )
