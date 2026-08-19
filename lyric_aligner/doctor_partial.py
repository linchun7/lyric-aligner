"""Backward-compatible Doctor extension for Partial Timeline Repair readiness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from lyric_aligner.doctor import DoctorError, build_doctor_report
from lyric_aligner.timeline.partial_repair_readiness import (
    inspect_partial_timeline_repair_readiness,
)


_PARTIAL_REQUIREMENTS = {
    "partial_repair:lineage": lambda row: bool(row["lineage"]["valid"]),
    "partial_repair:trust_lock": lambda row: bool(row["trust_lock"]["valid"]),
    "partial_repair:actionable_scope": lambda row: bool(
        row["trust_lock"]["valid"] and row["trust_lock"]["actionable"]
    ),
    "partial_repair:decisions": lambda row: bool(row["decisions"]["valid"]),
    "partial_repair:proposal_inputs": lambda row: row["status"]
    == "proposal_inputs_ready",
}


def build_doctor_report_with_partial_repair(
    *,
    partial_trust_lock: Path | None = None,
    partial_trust_decisions: Path | None = None,
    partial_trust_decisions_artifact: Path | None = None,
    requirements: Iterable[str] = (),
    **doctor_kwargs: Any,
) -> dict[str, Any]:
    """Build the existing Doctor report plus a read-only partial-repair section."""

    requested_requirements = [str(value) for value in requirements]
    partial_requirements = [
        value for value in requested_requirements if value.startswith("partial_repair:")
    ]
    unknown_partial = [
        value for value in partial_requirements if value not in _PARTIAL_REQUIREMENTS
    ]
    if unknown_partial:
        raise DoctorError(f"unknown doctor requirement {unknown_partial[0]}")
    base_requirements = [
        value for value in requested_requirements if not value.startswith("partial_repair:")
    ]

    report = build_doctor_report(
        requirements=base_requirements,
        **doctor_kwargs,
    )
    partial = inspect_partial_timeline_repair_readiness(
        run_path=doctor_kwargs.get("run"),
        run_artifact_path=doctor_kwargs.get("run_artifact"),
        fusion_path=doctor_kwargs.get("fusion"),
        fusion_artifact_path=doctor_kwargs.get("fusion_artifact"),
        trust_lock_path=partial_trust_lock,
        decision_path=partial_trust_decisions,
        decision_artifact_path=partial_trust_decisions_artifact,
    )
    partial_results = {
        value: bool(_PARTIAL_REQUIREMENTS[value](partial))
        for value in partial_requirements
    }
    combined = dict(report["requirements"]["results"])
    combined.update(partial_results)
    report["requirements"] = {
        "passed": all(combined.values()) if combined else True,
        "results": combined,
    }
    report["partial_timeline_repair"] = partial
    return report
