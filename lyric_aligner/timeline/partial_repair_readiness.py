"""Read-only Partial Timeline Repair readiness diagnostics for Doctor P5.

This module composes the exact P3/P4 production validators. It never creates or
mutates a subtitle, trust lock, decision artifact, or timing candidate. The
report answers whether formal lineage exists, whether a private trust lock is
valid/actionable for explicit language scopes, and whether a formal calibrated
trust decision artifact is ready to feed the proposal-only P1-P4 chain.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_production import (
    inspect_partial_repair_artifacts,
)
from lyric_aligner.timeline.partial_repair_trust import (
    calibrated_decisions_to_explicit_trust,
    load_calibrated_trust_policy_lock,
)
from lyric_aligner.timeline.partial_repair_trust_production import (
    validate_calibrated_trust_decision_artifact,
)


PARTIAL_REPAIR_READINESS_SCHEMA_VERSION = "1.0"


def _prefix(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:12] if len(text) >= 12 else None


def _line_summary(fusion: dict[str, Any]) -> tuple[int, list[str]]:
    rows = fusion.get("lines")
    if not isinstance(rows, list):
        raise PartialTimelineRepairError("P9 fusion lines must be a list")
    conflicts = 0
    scopes: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PartialTimelineRepairError("P9 fusion line must be an object")
        if row.get("shadow_level") == "CONFLICT":
            conflicts += 1
        language = str(row.get("language_profile") or "").strip()
        if language:
            scopes.add(f"language:{language}")
    return conflicts, sorted(scopes)


def _mapping_summary(context: Any) -> dict[str, Any]:
    kinds: Counter[str] = Counter()
    unavailable = 0
    for row in context.occurrences:
        if row.status == "ready" and row.mapping_kind:
            kinds[str(row.mapping_kind)] += 1
        else:
            unavailable += 1
    return {
        "occurrence_count": len(context.occurrences),
        "ready_occurrence_count": sum(kinds.values()),
        "unavailable_occurrence_count": unavailable,
        "mapping_kind_counts": {
            key: kinds.get(key, 0)
            for key in ("AFFINE", "PIECEWISE_RATE", "CUT_AWARE")
        },
        "confirmed_cut_occurrence_count": len(
            context.confirmed_cut_occurrence_ids
        ),
    }


def _action(
    *,
    requested: bool,
    lineage: dict[str, Any],
    trust_lock: dict[str, Any],
    decisions: dict[str, Any],
) -> dict[str, str]:
    if not requested:
        return {
            "action": "not_requested",
            "detail": "supply effective-run/fusion artifacts when partial timing repair is needed",
        }
    if not lineage["valid"]:
        return {
            "action": "repair_partial_repair_lineage",
            "detail": "supply exact current effective-run and P9 fusion payload/artifact pairs",
        }
    if not trust_lock["provided"]:
        return {
            "action": "human_review_or_build_private_trust_lock",
            "detail": "no calibrated trust lock is present; P9 shadow levels alone cannot create trust",
        }
    if not trust_lock["valid"]:
        return {
            "action": "rebuild_partial_trust_lock",
            "detail": "re-run strict private calibration/blind validation and rebuild the trust lock",
        }
    if not trust_lock["actionable"]:
        return {
            "action": "human_review_or_expand_blind_language_gates",
            "detail": "the valid lock has no explicitly blind-gated language scope",
        }
    if not decisions["provided"]:
        return {
            "action": "generate_calibrated_trust_decisions",
            "detail": "run the locked private candidate for covered language scopes and emit a formal decision artifact",
        }
    if not decisions["valid"]:
        return {
            "action": "regenerate_calibrated_trust_decisions",
            "detail": "decision payload/artifact identity, scope, or lineage is not production-valid",
        }
    return {
        "action": "build_partial_repair_proposal",
        "detail": "P3/P4 inputs are ready for proposal-only local timing repair; automatic write-back remains disabled",
    }


def inspect_partial_timeline_repair_readiness(
    *,
    run_path: Path | None,
    run_artifact_path: Path | None,
    fusion_path: Path | None,
    fusion_artifact_path: Path | None,
    trust_lock_path: Path | None = None,
    decision_path: Path | None = None,
    decision_artifact_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect P3/P4 readiness without exposing local paths or raw text."""

    requested = any(
        value is not None
        for value in (
            run_path,
            run_artifact_path,
            fusion_path,
            fusion_artifact_path,
            trust_lock_path,
            decision_path,
            decision_artifact_path,
        )
    )
    lineage = {
        "provided": any(
            value is not None
            for value in (
                run_path,
                run_artifact_path,
                fusion_path,
                fusion_artifact_path,
            )
        ),
        "valid": False,
        "detail": "not_provided",
        "run_stage": None,
        "run_artifact_id_prefix": None,
        "fusion_artifact_id_prefix": None,
        "fusion_conflict_count": 0,
        "fusion_language_scopes": [],
        "mapping": {
            "occurrence_count": 0,
            "ready_occurrence_count": 0,
            "unavailable_occurrence_count": 0,
            "mapping_kind_counts": {
                "AFFINE": 0,
                "PIECEWISE_RATE": 0,
                "CUT_AWARE": 0,
            },
            "confirmed_cut_occurrence_count": 0,
        },
    }
    context = None
    fusion = None
    fusion_artifact = None
    lineage_values = (
        run_path,
        run_artifact_path,
        fusion_path,
        fusion_artifact_path,
    )
    if any(value is not None for value in lineage_values):
        if not all(value is not None for value in lineage_values):
            lineage["detail"] = "effective_run_and_fusion_pairs_incomplete"
        else:
            try:
                context, fusion, fusion_artifact = inspect_partial_repair_artifacts(
                    run_path=run_path,  # type: ignore[arg-type]
                    run_artifact_path=run_artifact_path,  # type: ignore[arg-type]
                    fusion_path=fusion_path,  # type: ignore[arg-type]
                    fusion_artifact_path=fusion_artifact_path,  # type: ignore[arg-type]
                )
                conflicts, language_scopes = _line_summary(fusion)
                lineage.update(
                    {
                        "valid": True,
                        "detail": "effective_run_and_fusion_lineage_ok",
                        "run_stage": context.run_stage,
                        "run_artifact_id_prefix": _prefix(context.run_artifact_id),
                        "fusion_artifact_id_prefix": _prefix(
                            fusion_artifact.get("artifact_id")
                        ),
                        "fusion_conflict_count": conflicts,
                        "fusion_language_scopes": language_scopes,
                        "mapping": _mapping_summary(context),
                    }
                )
            except (OSError, ValueError, PartialTimelineRepairError) as exc:
                lineage["detail"] = str(exc)

    trust_lock = {
        "provided": trust_lock_path is not None,
        "valid": False,
        "detail": "not_provided",
        "actionable": False,
        "eligible_language_scopes": [],
        "lock_sha256_prefix": None,
        "policy_calibrated": False,
        "independent_blind_gate_passed": False,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
    }
    lock = None
    if trust_lock_path is not None:
        try:
            lock = load_calibrated_trust_policy_lock(trust_lock_path)
            scopes = sorted(str(value) for value in lock["eligible_language_scopes"])
            trust_lock.update(
                {
                    "valid": True,
                    "detail": "strict_calibration_blind_trust_lock_ok",
                    "actionable": bool(lock["cue_trust_generation_allowed"]),
                    "eligible_language_scopes": scopes,
                    "lock_sha256_prefix": _prefix(
                        lock.get("trust_policy_lock_sha256")
                    ),
                    "policy_calibrated": True,
                    "independent_blind_gate_passed": True,
                }
            )
        except (OSError, ValueError, PartialTimelineRepairError) as exc:
            trust_lock["detail"] = str(exc)

    decisions = {
        "provided": decision_path is not None or decision_artifact_path is not None,
        "paired": decision_path is not None and decision_artifact_path is not None,
        "valid": False,
        "detail": "not_provided",
        "decision_artifact_id_prefix": None,
        "decision_count": 0,
        "counts": {
            "trusted": 0,
            "untrusted": 0,
            "unknown": 0,
            "uncovered_scope": 0,
            "conflict_downgraded": 0,
            "ambiguous_binding": 0,
        },
    }
    if decisions["provided"]:
        if not decisions["paired"]:
            decisions["detail"] = "decision_payload_artifact_pair_incomplete"
        elif not lineage["valid"]:
            decisions["detail"] = "valid_partial_repair_lineage_required"
        elif lock is None or not trust_lock["valid"]:
            decisions["detail"] = "valid_trust_lock_required"
        else:
            try:
                decision_artifact = validate_calibrated_trust_decision_artifact(
                    decision_path=decision_path,  # type: ignore[arg-type]
                    decision_artifact_path=decision_artifact_path,  # type: ignore[arg-type]
                    fusion_artifact_path=fusion_artifact_path,  # type: ignore[arg-type]
                    trust_lock=lock,
                )
                _, decision_report = calibrated_decisions_to_explicit_trust(
                    decision_path=decision_path,  # type: ignore[arg-type]
                    lock=lock,
                    fusion=fusion,  # type: ignore[arg-type]
                    fusion_artifact_id=str(
                        fusion_artifact.get("artifact_id") or ""  # type: ignore[union-attr]
                    ),
                )
                decisions.update(
                    {
                        "valid": True,
                        "detail": "calibrated_trust_decision_artifact_and_scope_ok",
                        "decision_artifact_id_prefix": _prefix(
                            decision_artifact.get("artifact_id")
                        ),
                        "decision_count": int(
                            decision_report.get("decision_count") or 0
                        ),
                        "counts": dict(decision_report.get("counts") or {}),
                    }
                )
            except (OSError, ValueError, PartialTimelineRepairError) as exc:
                decisions["detail"] = str(exc)

    action = _action(
        requested=requested,
        lineage=lineage,
        trust_lock=trust_lock,
        decisions=decisions,
    )
    if not requested:
        status = "not_requested"
    elif not lineage["valid"]:
        status = "blocked"
    elif not trust_lock["provided"]:
        status = "human_review_or_calibration_required"
    elif not trust_lock["valid"]:
        status = "blocked"
    elif not trust_lock["actionable"]:
        status = "human_review_required"
    elif not decisions["provided"]:
        status = "calibrated_decisions_required"
    elif not decisions["valid"]:
        status = "blocked"
    else:
        status = "proposal_inputs_ready"

    return {
        "schema_version": PARTIAL_REPAIR_READINESS_SCHEMA_VERSION,
        "mode": "read_only_partial_timeline_repair_readiness",
        "requested": requested,
        "status": status,
        "lineage": lineage,
        "trust_lock": trust_lock,
        "decisions": decisions,
        "recommended_next_action": action,
        "proposal_only": True,
        "publish_ready": False,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
            "p9_fusion": "uncalibrated_shadow_diagnostics_only",
            "p4_trust_lock": "cue_trust_proposal_eligibility_only",
        },
        "privacy": "no raw lyric text, subtitle text, local absolute paths, or artifact output paths are emitted",
    }
