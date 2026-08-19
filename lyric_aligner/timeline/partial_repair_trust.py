"""Calibration-locked cue-trust inputs for Partial Timeline Repair P4.

P4 does not invent timing/trust thresholds. It verifies that a candidate was
selected on the strict calibration split, remained revision/runtime locked, and
passed the independent blind gate. Only explicit language scopes that were
actually gated on blind_test become eligible for calibrated cue-trust decisions.

The selected candidate's decision engine remains external/private. Its decision
payload is accepted only when it binds this lock and the exact P9 fusion artifact.
This module still does not authorize automatic SRT timing mutation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Sequence

from lyric_aligner import __version__
from lyric_aligner.evaluation.strict_workflow import (
    StrictEvaluationError,
    canonical_sha256,
    evaluate_gates,
    file_sha256,
    load_policy,
    load_selection,
    load_strict_evaluation,
    selection_hash,
    validate_blind_baseline_lock,
    validate_blind_lock,
)
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import (
    CueTrust,
    PartialTimelineRepairError,
    TimingCandidate,
)
from lyric_aligner.timeline.partial_repair_evidence import ExplicitCueTrust
from lyric_aligner.timeline.partial_repair_production import (
    bridge_effective_artifacts_to_partial_repair,
)


TRUST_POLICY_LOCK_SCHEMA_VERSION = "1.0"
TRUST_DECISION_SCHEMA_VERSION = "1.0"


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialTimelineRepairError(f"cannot read {label}: {path.name}") from exc
    if not isinstance(payload, dict):
        raise PartialTimelineRepairError(f"{label} must be a JSON object")
    return payload


def _require_isolated(evaluation: dict[str, Any], *, label: str) -> None:
    validation = evaluation.get("dataset_validation")
    if not isinstance(validation, dict) or validation.get(
        "source_group_isolation_enforced"
    ) is not True:
        raise PartialTimelineRepairError(
            f"{label} does not prove source_group split isolation"
        )


def _blind_gate_hash(payload: dict[str, Any]) -> str:
    return selection_hash(payload)


def _load_blind_gate(path: Path) -> dict[str, Any]:
    payload = _load_json(path, label="strict blind gate")
    if payload.get("schema_version") != "1.0":
        raise PartialTimelineRepairError("strict blind gate schema_version mismatch")
    expected = str(payload.get("blind_gate_payload_sha256") or "").strip()
    if not expected or expected != _blind_gate_hash(payload):
        raise PartialTimelineRepairError("strict blind gate payload hash mismatch")
    return payload


def _runtime_identity(value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise PartialTimelineRepairError(f"{label} runtime identity is missing")
    output: dict[str, str] = {}
    for key in (
        "algorithm_version",
        "calibration_profile_version",
        "calibration_profile_id",
    ):
        text = str(value.get(key) or "").strip()
        if not text:
            raise PartialTimelineRepairError(
                f"{label} runtime identity is missing {key}"
            )
        output[key] = text
    return output


def _same_identity(left: object, right: object, *, label: str) -> None:
    if _runtime_identity(left, label=f"{label} left") != _runtime_identity(
        right, label=f"{label} right"
    ):
        raise PartialTimelineRepairError(f"{label} runtime identity mismatch")


def _calibration_lock_checks(
    *,
    selection: dict[str, Any],
    baseline_path: Path,
    candidate_path: Path,
    policy_path: Path,
) -> dict[str, Any]:
    baseline = load_strict_evaluation(baseline_path)
    candidate = load_strict_evaluation(candidate_path)
    policy = load_policy(policy_path, "calibration")
    _require_isolated(baseline, label="calibration baseline")
    _require_isolated(candidate, label="calibration candidate")
    if baseline.get("evaluated_split") != "calibration":
        raise PartialTimelineRepairError("calibration baseline split mismatch")
    if candidate.get("evaluated_split") != "calibration":
        raise PartialTimelineRepairError("calibration candidate split mismatch")
    if file_sha256(baseline_path) != selection.get(
        "baseline_calibration_evaluation_sha256"
    ):
        raise PartialTimelineRepairError("calibration baseline file lock mismatch")
    if file_sha256(candidate_path) != selection.get(
        "selected_calibration_evaluation_sha256"
    ):
        raise PartialTimelineRepairError("selected calibration file lock mismatch")
    if file_sha256(policy_path) != selection.get("policy_sha256"):
        raise PartialTimelineRepairError("calibration policy file lock mismatch")
    if candidate.get("candidate_id") != selection.get("selected_candidate_id"):
        raise PartialTimelineRepairError("selected calibration candidate id mismatch")
    if candidate.get("candidate_revision") != selection.get(
        "selected_candidate_revision"
    ):
        raise PartialTimelineRepairError(
            "selected calibration candidate revision mismatch"
        )
    _same_identity(
        candidate.get("runtime_identity"),
        selection.get("selected_runtime_identity"),
        label="selected calibration candidate",
    )
    if baseline.get("candidate_id") != selection.get("baseline_candidate_id"):
        raise PartialTimelineRepairError("calibration baseline id mismatch")
    if baseline.get("candidate_revision") != selection.get(
        "baseline_candidate_revision"
    ):
        raise PartialTimelineRepairError("calibration baseline revision mismatch")
    _same_identity(
        baseline.get("runtime_identity"),
        selection.get("baseline_runtime_identity"),
        label="calibration baseline",
    )
    if baseline.get("dataset") != selection.get("dataset") or candidate.get(
        "dataset"
    ) != selection.get("dataset"):
        raise PartialTimelineRepairError("calibration dataset name mismatch")
    if baseline.get("dataset_revision") != selection.get(
        "dataset_revision"
    ) or candidate.get("dataset_revision") != selection.get("dataset_revision"):
        raise PartialTimelineRepairError("calibration dataset revision mismatch")
    baseline_identity = baseline.get("dataset_identity") or {}
    candidate_identity = candidate.get("dataset_identity") or {}
    calibration_ground_truth = str(
        selection.get("calibration_dataset_ground_truth_sha256") or ""
    )
    calibration_case_ids = str(selection.get("calibration_case_ids_sha256") or "")
    if (
        baseline_identity.get("dataset_ground_truth_sha256")
        != calibration_ground_truth
        or candidate_identity.get("dataset_ground_truth_sha256")
        != calibration_ground_truth
    ):
        raise PartialTimelineRepairError("calibration ground-truth identity mismatch")
    if baseline_identity.get("case_ids_sha256") != calibration_case_ids or candidate_identity.get(
        "case_ids_sha256"
    ) != calibration_case_ids:
        raise PartialTimelineRepairError("calibration case identity mismatch")
    computed_gate = evaluate_gates(baseline, candidate, policy)
    if computed_gate.get("passed") is not True:
        raise PartialTimelineRepairError(
            "selected calibration candidate no longer passes calibration gates"
        )
    selected_gate = selection.get("selection", {}).get("selected_gate")
    if selected_gate != computed_gate:
        raise PartialTimelineRepairError(
            "selection artifact calibration gate result mismatch"
        )
    return {
        "baseline": baseline,
        "candidate": candidate,
        "policy": policy,
        "computed_gate": computed_gate,
    }


def _blind_lock_checks(
    *,
    selection_path: Path,
    selection: dict[str, Any],
    blind_gate_path: Path,
    baseline_path: Path,
    candidate_path: Path,
    policy_path: Path,
) -> dict[str, Any]:
    blind_gate = _load_blind_gate(blind_gate_path)
    baseline = load_strict_evaluation(baseline_path)
    candidate = load_strict_evaluation(candidate_path)
    policy = load_policy(policy_path, "blind_test")
    _require_isolated(baseline, label="blind baseline")
    _require_isolated(candidate, label="blind candidate")
    if baseline.get("evaluated_split") != "blind_test":
        raise PartialTimelineRepairError("blind baseline split mismatch")
    if candidate.get("evaluated_split") != "blind_test":
        raise PartialTimelineRepairError("blind candidate split mismatch")
    try:
        validate_blind_baseline_lock(selection, baseline)
        validate_blind_lock(selection, candidate)
    except StrictEvaluationError as exc:
        raise PartialTimelineRepairError(str(exc)) from exc
    if blind_gate.get("passed") is not True:
        raise PartialTimelineRepairError("strict blind gate did not pass")
    if blind_gate.get("selection_payload_sha256") != selection.get(
        "selection_payload_sha256"
    ):
        raise PartialTimelineRepairError("blind gate selection identity mismatch")
    if blind_gate.get("selection_file_sha256") != file_sha256(selection_path):
        raise PartialTimelineRepairError("blind gate selection file lock mismatch")
    if blind_gate.get("baseline_blind_evaluation_sha256") != file_sha256(
        baseline_path
    ):
        raise PartialTimelineRepairError("blind baseline file lock mismatch")
    if blind_gate.get("candidate_blind_evaluation_sha256") != file_sha256(
        candidate_path
    ):
        raise PartialTimelineRepairError("blind candidate file lock mismatch")
    if blind_gate.get("blind_policy_sha256") != file_sha256(policy_path):
        raise PartialTimelineRepairError("blind policy file lock mismatch")
    for key in (
        "dataset",
        "dataset_revision",
        "baseline_candidate_id",
        "baseline_candidate_revision",
        "selected_candidate_id",
        "selected_candidate_revision",
    ):
        if blind_gate.get(key) != selection.get(key):
            raise PartialTimelineRepairError(f"blind gate {key} lock mismatch")
    _same_identity(
        blind_gate.get("baseline_runtime_identity"),
        selection.get("baseline_runtime_identity"),
        label="blind gate baseline",
    )
    _same_identity(
        blind_gate.get("selected_runtime_identity"),
        selection.get("selected_runtime_identity"),
        label="blind gate selected candidate",
    )
    computed_gate = evaluate_gates(baseline, candidate, policy)
    if computed_gate.get("passed") is not True:
        raise PartialTimelineRepairError(
            "locked candidate no longer passes independent blind gates"
        )
    if blind_gate.get("gate") != computed_gate:
        raise PartialTimelineRepairError("blind gate result does not recompute exactly")
    identity = baseline.get("dataset_identity") or {}
    if identity.get("dataset_ground_truth_sha256") != blind_gate.get(
        "blind_dataset_ground_truth_sha256"
    ):
        raise PartialTimelineRepairError("blind ground-truth identity mismatch")
    if identity.get("case_ids_sha256") != blind_gate.get("blind_case_ids_sha256"):
        raise PartialTimelineRepairError("blind case identity mismatch")
    return {
        "blind_gate": blind_gate,
        "baseline": baseline,
        "candidate": candidate,
        "policy": policy,
        "computed_gate": computed_gate,
    }


def _eligible_language_scopes(
    policy: dict[str, Any], computed_gate: dict[str, Any]
) -> list[str]:
    gate_rows = computed_gate.get("gates")
    if not isinstance(gate_rows, list):
        raise PartialTimelineRepairError("blind gate rows are missing")
    passed_scopes = {
        str(row.get("scope") or "")
        for row in gate_rows
        if isinstance(row, dict) and row.get("passed") is True
    }
    declared_scopes = {
        str(row.get("scope") or "")
        for row in policy.get("gates", [])
        if isinstance(row, dict)
    }
    scopes = sorted(
        scope
        for scope in declared_scopes & passed_scopes
        if scope.startswith("language:") and scope != "language:"
    )
    return scopes


def _lock_hash(payload: dict[str, Any]) -> str:
    return canonical_sha256(
        {key: value for key, value in payload.items() if key != "trust_policy_lock_sha256"}
    )


def build_calibrated_trust_policy_lock(
    *,
    selection_path: Path,
    calibration_baseline_path: Path,
    calibration_candidate_path: Path,
    calibration_policy_path: Path,
    blind_gate_path: Path,
    blind_baseline_path: Path,
    blind_candidate_path: Path,
    blind_policy_path: Path,
) -> dict[str, Any]:
    """Verify strict calibration+blind artifacts and create a trust-policy lock."""

    try:
        selection = load_selection(selection_path)
    except StrictEvaluationError as exc:
        raise PartialTimelineRepairError(str(exc)) from exc
    calibration = _calibration_lock_checks(
        selection=selection,
        baseline_path=calibration_baseline_path,
        candidate_path=calibration_candidate_path,
        policy_path=calibration_policy_path,
    )
    blind = _blind_lock_checks(
        selection_path=selection_path,
        selection=selection,
        blind_gate_path=blind_gate_path,
        baseline_path=blind_baseline_path,
        candidate_path=blind_candidate_path,
        policy_path=blind_policy_path,
    )
    selected_runtime = _runtime_identity(
        selection.get("selected_runtime_identity"), label="selected candidate"
    )
    if selected_runtime["algorithm_version"] != __version__:
        raise PartialTimelineRepairError(
            "calibrated trust candidate uses a non-current algorithm version"
        )
    calibration_ground_truth = str(
        selection.get("calibration_dataset_ground_truth_sha256") or ""
    )
    calibration_case_ids = str(selection.get("calibration_case_ids_sha256") or "")
    blind_ground_truth = str(
        blind["blind_gate"].get("blind_dataset_ground_truth_sha256") or ""
    )
    blind_case_ids = str(blind["blind_gate"].get("blind_case_ids_sha256") or "")
    if calibration_ground_truth == blind_ground_truth:
        raise PartialTimelineRepairError(
            "calibration and blind ground-truth identities must differ"
        )
    if calibration_case_ids == blind_case_ids:
        raise PartialTimelineRepairError(
            "calibration and blind case identities must differ"
        )
    eligible_scopes = _eligible_language_scopes(
        blind["policy"], blind["computed_gate"]
    )
    payload: dict[str, Any] = {
        "schema_version": TRUST_POLICY_LOCK_SCHEMA_VERSION,
        "mode": "partial_timeline_repair_calibrated_trust_lock",
        "dataset": selection["dataset"],
        "dataset_revision": selection["dataset_revision"],
        "candidate_id": selection["selected_candidate_id"],
        "candidate_revision": selection["selected_candidate_revision"],
        "runtime_identity": deepcopy(selected_runtime),
        "selection_payload_sha256": selection["selection_payload_sha256"],
        "selection_file_sha256": file_sha256(selection_path),
        "selected_calibration_evaluation_sha256": file_sha256(
            calibration_candidate_path
        ),
        "calibration_baseline_evaluation_sha256": file_sha256(
            calibration_baseline_path
        ),
        "calibration_policy_sha256": file_sha256(calibration_policy_path),
        "blind_gate_payload_sha256": blind["blind_gate"][
            "blind_gate_payload_sha256"
        ],
        "blind_gate_file_sha256": file_sha256(blind_gate_path),
        "blind_candidate_evaluation_sha256": file_sha256(blind_candidate_path),
        "blind_baseline_evaluation_sha256": file_sha256(blind_baseline_path),
        "blind_policy_sha256": file_sha256(blind_policy_path),
        "calibration_dataset_ground_truth_sha256": calibration_ground_truth,
        "calibration_case_ids_sha256": calibration_case_ids,
        "blind_dataset_ground_truth_sha256": blind_ground_truth,
        "blind_case_ids_sha256": blind_case_ids,
        "eligible_language_scopes": eligible_scopes,
        "cue_trust_generation_allowed": bool(eligible_scopes),
        "policy_calibrated": True,
        "independent_blind_gate_passed": True,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
        "privacy": "aggregate identities/scopes only; no lyric or local path content",
    }
    payload["trust_policy_lock_sha256"] = _lock_hash(payload)
    return payload


def load_calibrated_trust_policy_lock(path: Path) -> dict[str, Any]:
    payload = _load_json(path, label="calibrated trust policy lock")
    if payload.get("schema_version") != TRUST_POLICY_LOCK_SCHEMA_VERSION:
        raise PartialTimelineRepairError("trust policy lock schema_version mismatch")
    if payload.get("mode") != "partial_timeline_repair_calibrated_trust_lock":
        raise PartialTimelineRepairError("trust policy lock mode mismatch")
    expected = str(payload.get("trust_policy_lock_sha256") or "").strip()
    if not expected or expected != _lock_hash(payload):
        raise PartialTimelineRepairError("trust policy lock payload hash mismatch")
    if payload.get("policy_calibrated") is not True or payload.get(
        "independent_blind_gate_passed"
    ) is not True:
        raise PartialTimelineRepairError(
            "trust policy lock does not prove calibration + blind gate"
        )
    if payload.get("automatic_timing_change_allowed") is not False:
        raise PartialTimelineRepairError(
            "trust policy lock must not authorize automatic timing mutation"
        )
    if payload.get("release_gate_eligible") is not False:
        raise PartialTimelineRepairError(
            "trust policy lock must not authorize release gating"
        )
    runtime = _runtime_identity(payload.get("runtime_identity"), label="trust lock")
    if runtime["algorithm_version"] != __version__:
        raise PartialTimelineRepairError("trust policy lock algorithm version is stale")
    scopes = payload.get("eligible_language_scopes")
    if not isinstance(scopes, list) or any(
        not isinstance(scope, str) or not scope.startswith("language:")
        for scope in scopes
    ):
        raise PartialTimelineRepairError("trust policy lock language scopes are invalid")
    if bool(scopes) != bool(payload.get("cue_trust_generation_allowed")):
        raise PartialTimelineRepairError(
            "trust policy lock scope/actionability flags disagree"
        )
    return payload


def _decision_hash(payload: dict[str, Any]) -> str:
    return canonical_sha256(
        {key: value for key, value in payload.items() if key != "decision_payload_sha256"}
    )


def _editor_cue_number(line: dict[str, Any]) -> int | None:
    families = line.get("families")
    if not isinstance(families, list):
        raise PartialTimelineRepairError("fusion line families are invalid")
    editors = [
        family
        for family in families
        if isinstance(family, dict) and family.get("family") == "editor"
    ]
    if len(editors) > 1:
        raise PartialTimelineRepairError("fusion line has duplicate editor families")
    if not editors or editors[0].get("available") is not True:
        return None
    try:
        cue_number = int(editors[0]["cue_number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PartialTimelineRepairError("fusion editor cue_number is invalid") from exc
    return cue_number if cue_number > 0 else None


def calibrated_decisions_to_explicit_trust(
    *,
    decision_path: Path,
    lock: dict[str, Any],
    fusion: dict[str, Any],
    fusion_artifact_id: str,
) -> tuple[list[ExplicitCueTrust], dict[str, Any]]:
    """Validate selected-candidate decisions and convert safe rows to cue trust."""

    payload = _load_json(decision_path, label="calibrated cue-trust decisions")
    if payload.get("schema_version") != TRUST_DECISION_SCHEMA_VERSION:
        raise PartialTimelineRepairError("trust decision schema_version mismatch")
    if payload.get("mode") != "partial_timeline_repair_calibrated_trust_decisions":
        raise PartialTimelineRepairError("trust decision mode mismatch")
    expected = str(payload.get("decision_payload_sha256") or "").strip()
    if not expected or expected != _decision_hash(payload):
        raise PartialTimelineRepairError("trust decision payload hash mismatch")
    if payload.get("trust_policy_lock_sha256") != lock.get(
        "trust_policy_lock_sha256"
    ):
        raise PartialTimelineRepairError("trust decision policy-lock identity mismatch")
    if payload.get("candidate_id") != lock.get("candidate_id") or payload.get(
        "candidate_revision"
    ) != lock.get("candidate_revision"):
        raise PartialTimelineRepairError("trust decision candidate identity mismatch")
    _same_identity(
        payload.get("runtime_identity"),
        lock.get("runtime_identity"),
        label="trust decision candidate",
    )
    if payload.get("source_fusion_artifact_id") != fusion_artifact_id:
        raise PartialTimelineRepairError("trust decision fusion artifact identity mismatch")
    if payload.get("automatic_timing_change_allowed") is not False:
        raise PartialTimelineRepairError(
            "trust decision payload must not authorize timing mutation"
        )

    lines = fusion.get("lines")
    if not isinstance(lines, list):
        raise PartialTimelineRepairError("fusion lines are missing")
    lines_by_cue: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        if not isinstance(line, dict):
            raise PartialTimelineRepairError("fusion line is invalid")
        cue_number = _editor_cue_number(line)
        if cue_number is not None:
            lines_by_cue.setdefault(cue_number, []).append(line)

    rows = payload.get("decisions")
    if not isinstance(rows, list):
        raise PartialTimelineRepairError("trust decision rows must be a list")
    covered = set(lock.get("eligible_language_scopes") or [])
    output: list[ExplicitCueTrust] = []
    seen: set[int] = set()
    counts = {
        "trusted": 0,
        "untrusted": 0,
        "unknown": 0,
        "uncovered_scope": 0,
        "conflict_downgraded": 0,
        "ambiguous_binding": 0,
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PartialTimelineRepairError(f"trust decision {index} must be an object")
        try:
            cue_number = int(row["cue_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PartialTimelineRepairError(
                f"trust decision {index} cue_number is invalid"
            ) from exc
        if cue_number <= 0 or cue_number in seen:
            raise PartialTimelineRepairError(
                "trust decision cue numbers must be unique and positive"
            )
        seen.add(cue_number)
        status = str(row.get("status") or "").strip()
        if status not in {"trusted", "untrusted", "unknown"}:
            raise PartialTimelineRepairError(
                f"trust decision {cue_number} status is invalid"
            )
        scope = str(row.get("scope") or "").strip()
        if not scope.startswith("language:") or scope == "language:":
            raise PartialTimelineRepairError(
                f"trust decision {cue_number} language scope is invalid"
            )
        reason_code = str(row.get("reason_code") or "").strip()
        if not reason_code:
            raise PartialTimelineRepairError(
                f"trust decision {cue_number} reason_code is missing"
            )
        bound = lines_by_cue.get(cue_number, [])
        effective_status = status
        reason = reason_code
        if len(bound) != 1:
            effective_status = "unknown"
            reason = "ambiguous_or_missing_p9_editor_binding"
            counts["ambiguous_binding"] += 1
        else:
            language_scope = f"language:{str(bound[0].get('language_profile') or '').strip()}"
            if language_scope != scope:
                raise PartialTimelineRepairError(
                    f"trust decision {cue_number} scope does not match P9 language"
                )
            if scope not in covered or lock.get("cue_trust_generation_allowed") is not True:
                effective_status = "unknown"
                reason = "blind_gate_does_not_cover_language_scope"
                counts["uncovered_scope"] += 1
            elif bound[0].get("shadow_level") == "CONFLICT" and status == "trusted":
                effective_status = "unknown"
                reason = "p9_conflict_cannot_be_auto_trusted"
                counts["conflict_downgraded"] += 1
        counts[effective_status] += 1
        output.append(
            ExplicitCueTrust(
                cue_number=cue_number,
                status=effective_status,
                reason=reason,
                source="calibrated_policy",
            )
        )
    return output, {
        "decision_count": len(rows),
        "counts": counts,
        "eligible_language_scopes": sorted(covered),
        "candidate_id": lock["candidate_id"],
        "candidate_revision": lock["candidate_revision"],
        "trust_policy_lock_sha256": lock["trust_policy_lock_sha256"],
        "decision_payload_sha256": payload["decision_payload_sha256"],
    }


def _merge_trust(
    calibrated: Iterable[ExplicitCueTrust],
    human: Iterable[ExplicitCueTrust],
) -> tuple[list[ExplicitCueTrust], int]:
    by_number = {row.cue_number: row for row in calibrated}
    human_count = 0
    for row in human:
        if row.source != "human_review":
            raise PartialTimelineRepairError(
                "human override must use source=human_review"
            )
        by_number[row.cue_number] = row
        human_count += 1
    return [by_number[key] for key in sorted(by_number)], human_count


def bridge_calibrated_trust_to_partial_repair(
    *,
    cues: Sequence[Cue],
    run_path: Path,
    run_artifact_path: Path,
    fusion_path: Path,
    fusion_artifact_path: Path,
    trust_lock_path: Path,
    decision_path: Path,
    human_overrides: Iterable[ExplicitCueTrust] = (),
) -> tuple[list[CueTrust], list[TimingCandidate], dict[str, Any]]:
    """P4 production wrapper: calibrated trust proposals -> P3 artifact bridge."""

    lock = load_calibrated_trust_policy_lock(trust_lock_path)
    fusion = _load_json(fusion_path, label="P9 fusion")
    fusion_artifact = _load_json(fusion_artifact_path, label="P9 fusion artifact")
    fusion_artifact_id = str(fusion_artifact.get("artifact_id") or "").strip()
    if not fusion_artifact_id:
        raise PartialTimelineRepairError("P9 fusion artifact id is missing")
    calibrated, decision_report = calibrated_decisions_to_explicit_trust(
        decision_path=decision_path,
        lock=lock,
        fusion=fusion,
        fusion_artifact_id=fusion_artifact_id,
    )
    merged, human_count = _merge_trust(calibrated, human_overrides)
    trust, candidates, report = bridge_effective_artifacts_to_partial_repair(
        cues=cues,
        run_path=run_path,
        run_artifact_path=run_artifact_path,
        fusion_path=fusion_path,
        fusion_artifact_path=fusion_artifact_path,
        explicit_trust=merged,
    )
    report["calibrated_trust_policy"] = {
        **decision_report,
        "policy_calibrated": True,
        "independent_blind_gate_passed": True,
        "human_override_count": human_count,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
    }
    return trust, candidates, report
