"""Production artifact bridge for Partial Timeline Repair P3.

Low-level P1/P2/P3 helpers intentionally accept already-loaded payloads so they
remain easy to test and compose. Production must not trust a mutable P9 fusion
JSON by itself: this module requires the exact fusion artifact, verifies its
formal output hash and lineage, and only then emits P1 trust/candidate inputs.
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
from lyric_aligner.timeline.partial_repair_context import (
    EffectiveRunMappingContext,
    derive_effective_run_mapping_context,
)
from lyric_aligner.timeline.partial_repair_evidence import (
    ExplicitCueTrust,
    bridge_fusion_to_partial_repair,
)


PARTIAL_REPAIR_PRODUCTION_BRIDGE_SCHEMA_VERSION = "1.0"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialTimelineRepairError(
            f"cannot read Partial Timeline Repair input: {path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise PartialTimelineRepairError(
            f"Partial Timeline Repair input must be an object: {path.name}"
        )
    return payload


def _load_verified_fusion(
    *,
    context: EffectiveRunMappingContext,
    fusion_path: Path,
    fusion_artifact_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fusion = _load_json(fusion_path)
    artifact = _load_json(fusion_artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=context.task_fingerprint_sha256,
        expected_algorithm_version=context.algorithm_version,
        expected_stage="evidence_fusion_shadow",
    )
    issues.extend(
        validate_artifact_output(
            artifact,
            role="evidence_fusion",
            path=fusion_path,
        )
    )
    if issues:
        raise PartialTimelineRepairError(
            "invalid P9 fusion artifact: " + "; ".join(issues)
        )

    if fusion.get("task_fingerprint_sha256") != context.task_fingerprint_sha256:
        raise PartialTimelineRepairError("P9 fusion belongs to another task")
    if fusion.get("algorithm_version") != context.algorithm_version:
        raise PartialTimelineRepairError("P9 fusion algorithm version mismatch")
    if fusion.get("source_run_stage") != context.run_stage:
        raise PartialTimelineRepairError("P9 fusion source run stage mismatch")
    if fusion.get("source_run_artifact_id") != context.run_artifact_id:
        raise PartialTimelineRepairError(
            "P9 fusion is not bound to the effective run artifact"
        )

    normalized = artifact.get("normalized_config")
    if not isinstance(normalized, dict):
        raise PartialTimelineRepairError(
            "P9 fusion artifact normalized_config is missing"
        )
    if normalized.get("source_run_artifact_id") != context.run_artifact_id:
        raise PartialTimelineRepairError(
            "P9 fusion artifact config is bound to another effective run"
        )
    upstreams = {
        str(value)
        for value in artifact.get("upstream_artifact_ids", [])
        if str(value).strip()
    }
    if context.run_artifact_id not in upstreams:
        raise PartialTimelineRepairError(
            "effective run artifact is not upstream of P9 fusion"
        )

    evidence = artifact.get("evidence")
    if not isinstance(evidence, dict):
        raise PartialTimelineRepairError("P9 fusion artifact evidence is missing")
    expected_flags = {
        "mode": "shadow_only",
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
    }
    for key, expected in expected_flags.items():
        if evidence.get(key) != expected:
            raise PartialTimelineRepairError(
                f"P9 fusion artifact evidence {key} mismatch"
            )
    return fusion, artifact


def inspect_partial_repair_artifacts(
    *,
    run_path: Path,
    run_artifact_path: Path,
    fusion_path: Path,
    fusion_artifact_path: Path,
) -> tuple[EffectiveRunMappingContext, dict[str, Any], dict[str, Any]]:
    """Validate current-version P3 run/fusion artifacts without building cues.

    This is a read-only inspection primitive for Doctor/readiness tooling. It
    performs the same mapping/fusion lineage checks used by the production
    bridge but does not create trust or timing candidates.
    """

    context = derive_effective_run_mapping_context(
        run_path=run_path,
        run_artifact_path=run_artifact_path,
    )
    if context.algorithm_version != __version__:
        raise PartialTimelineRepairError(
            "Partial Timeline Repair production inputs use a non-current "
            "algorithm version"
        )
    fusion, fusion_artifact = _load_verified_fusion(
        context=context,
        fusion_path=fusion_path,
        fusion_artifact_path=fusion_artifact_path,
    )
    return context, fusion, fusion_artifact


def bridge_effective_artifacts_to_partial_repair(
    *,
    cues: Sequence[Cue],
    run_path: Path,
    run_artifact_path: Path,
    fusion_path: Path,
    fusion_artifact_path: Path,
    explicit_trust: Iterable[ExplicitCueTrust],
) -> tuple[list[CueTrust], list[TimingCandidate], dict[str, Any]]:
    """Build P3 repair inputs from fully verified current-version artifacts."""

    context, fusion, fusion_artifact = inspect_partial_repair_artifacts(
        run_path=run_path,
        run_artifact_path=run_artifact_path,
        fusion_path=fusion_path,
        fusion_artifact_path=fusion_artifact_path,
    )
    trust, candidates, report = bridge_fusion_to_partial_repair(
        cues=cues,
        fusion=fusion,
        mapping_kind_by_occurrence=context.mapping_kind_by_occurrence,
        explicit_trust=explicit_trust,
        confirmed_cut_occurrence_ids=context.confirmed_cut_occurrence_ids,
    )
    report["production_bridge_schema_version"] = (
        PARTIAL_REPAIR_PRODUCTION_BRIDGE_SCHEMA_VERSION
    )
    report["mapping_context_source"] = "formal_effective_run_artifact_lineage"
    report["fusion_context_source"] = "formal_evidence_fusion_artifact_lineage"
    report["effective_run_mapping_context"] = context.to_report()
    report["fusion_artifact_id"] = str(fusion_artifact.get("artifact_id") or "")
    report["production_inputs_artifact_verified"] = True
    report["production_algorithm_version_current"] = True
    return trust, candidates, report
