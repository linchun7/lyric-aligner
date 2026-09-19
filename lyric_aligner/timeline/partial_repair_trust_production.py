"""Formal production entry for P4 calibrated cue-trust decisions.

The low-level trust module validates selection/blind locks and decision payload
semantics. This layer adds the same formal artifact/output/upstream contract used
by the rest of V4 before a private calibrated decision payload may enter P3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import (
    CueTrust,
    PartialTimelineRepairError,
    TimingCandidate,
)
from lyric_aligner.timeline.partial_repair_evidence import ExplicitCueTrust
from lyric_aligner.timeline.partial_repair_trust import (
    bridge_calibrated_trust_to_partial_repair,
    load_calibrated_trust_policy_lock,
)


TRUST_DECISION_ARTIFACT_STAGE = "partial_timeline_trust_decisions"
TRUST_DECISION_ARTIFACT_ROLE = "cue_trust_decisions"


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialTimelineRepairError(f"cannot read {label}: {path.name}") from exc
    if not isinstance(payload, dict):
        raise PartialTimelineRepairError(f"{label} must be a JSON object")
    return payload


def validate_calibrated_trust_decision_artifact(
    *,
    decision_path: Path,
    decision_artifact_path: Path,
    fusion_artifact_path: Path,
    trust_lock: dict[str, Any],
) -> dict[str, Any]:
    """Validate formal decision output/lineage without generating cue trust."""

    decision_artifact = _load_json(
        decision_artifact_path, label="calibrated trust decision artifact"
    )
    fusion_artifact = _load_json(fusion_artifact_path, label="P9 fusion artifact")
    fusion_artifact_id = str(fusion_artifact.get("artifact_id") or "").strip()
    task_fingerprint = str(
        fusion_artifact.get("task_fingerprint_sha256") or ""
    ).strip()
    if not fusion_artifact_id or not task_fingerprint:
        raise PartialTimelineRepairError(
            "P9 fusion artifact is missing artifact/task identity"
        )
    if fusion_artifact.get("stage") != "evidence_fusion_shadow":
        raise PartialTimelineRepairError(
            "P9 fusion artifact stage is not evidence_fusion_shadow"
        )

    issues = validate_upstream_artifact(
        decision_artifact,
        expected_task_fingerprint=task_fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=TRUST_DECISION_ARTIFACT_STAGE,
    )
    issues.extend(
        validate_artifact_output(
            decision_artifact,
            role=TRUST_DECISION_ARTIFACT_ROLE,
            path=decision_path,
        )
    )
    if issues:
        raise PartialTimelineRepairError(
            "invalid calibrated trust decision artifact: " + "; ".join(issues)
        )

    upstreams = {
        str(value)
        for value in decision_artifact.get("upstream_artifact_ids", [])
        if str(value).strip()
    }
    if fusion_artifact_id not in upstreams:
        raise PartialTimelineRepairError(
            "P9 fusion artifact is not upstream of calibrated trust decisions"
        )

    config = decision_artifact.get("normalized_config")
    if not isinstance(config, dict):
        raise PartialTimelineRepairError(
            "calibrated trust decision artifact config is missing"
        )
    expected_config = {
        "trust_policy_lock_sha256": trust_lock["trust_policy_lock_sha256"],
        "source_fusion_artifact_id": fusion_artifact_id,
        "candidate_id": trust_lock["candidate_id"],
        "candidate_revision": trust_lock["candidate_revision"],
        "runtime_identity": trust_lock["runtime_identity"],
    }
    for key, expected in expected_config.items():
        if config.get(key) != expected:
            raise PartialTimelineRepairError(
                f"calibrated trust decision artifact {key} binding mismatch"
            )

    evidence = decision_artifact.get("evidence")
    if not isinstance(evidence, dict):
        raise PartialTimelineRepairError(
            "calibrated trust decision artifact evidence is missing"
        )
    required_evidence = {
        "policy_calibrated": True,
        "independent_blind_gate_passed": True,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
    }
    for key, expected in required_evidence.items():
        if evidence.get(key) != expected:
            raise PartialTimelineRepairError(
                f"calibrated trust decision artifact evidence {key} mismatch"
            )
    return decision_artifact


def bridge_calibrated_trust_artifacts_to_partial_repair(
    *,
    cues: Sequence[Cue],
    run_path: Path,
    run_artifact_path: Path,
    fusion_path: Path,
    fusion_artifact_path: Path,
    trust_lock_path: Path,
    decision_path: Path,
    decision_artifact_path: Path,
    human_overrides: Iterable[ExplicitCueTrust] = (),
) -> tuple[list[CueTrust], list[TimingCandidate], dict[str, Any]]:
    """Artifact-verified P4 production wrapper; still no timing write-back."""

    trust_lock = load_calibrated_trust_policy_lock(trust_lock_path)
    decision_artifact = validate_calibrated_trust_decision_artifact(
        decision_path=decision_path,
        decision_artifact_path=decision_artifact_path,
        fusion_artifact_path=fusion_artifact_path,
        trust_lock=trust_lock,
    )
    trust, candidates, report = bridge_calibrated_trust_to_partial_repair(
        cues=cues,
        run_path=run_path,
        run_artifact_path=run_artifact_path,
        fusion_path=fusion_path,
        fusion_artifact_path=fusion_artifact_path,
        trust_lock_path=trust_lock_path,
        decision_path=decision_path,
        human_overrides=human_overrides,
    )
    report["calibrated_trust_policy"]["decision_artifact_id"] = str(
        decision_artifact.get("artifact_id") or ""
    )
    report["calibrated_trust_policy"][
        "decision_artifact_verified"
    ] = True
    return trust, candidates, report
