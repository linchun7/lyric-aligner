#!/usr/bin/env python3
"""Seal a recovery subtitle release with independently calibrated V4 internal-boundary authority.

This seal is intentionally narrower than the full Max semantic-sync release contract. It proves
that the recovery baseline, Human Gold joint selector, raw direct-final-mix evidence, adjudication,
materialization, post-materialization text correction and final structural/text QA form one exact
hash-bound chain. Outer start/end authority remains closed unless separately proven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.evaluation.selective_consensus_calibration import (
    internal_selective_consensus_calibration_is_authoritative,
)
from scripts.task_contract import load_task_manifest, verify_manifest_inputs

SCHEMA_VERSION = "boundary-authority-overlay-release-seal-1.0"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_self_sha(payload: Mapping[str, Any], field: str, *, label: str) -> str:
    claimed = str(payload.get(field) or "")
    if len(claimed) != 64:
        raise ValueError(f"{label} has no valid {field}")
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if sha256_json(unsigned) != claimed:
        raise ValueError(f"{label} {field} mismatch")
    return claimed


def _manifest_input_sha(manifest: Mapping[str, Any], key: str) -> str:
    record = (manifest.get("inputs") or {}).get(key)
    if not isinstance(record, Mapping):
        raise ValueError(f"task manifest has no {key} input")
    value = str(record.get("sha256") or "")
    if len(value) != 64:
        raise ValueError(f"task manifest {key} SHA is invalid")
    return value


def seal(args: argparse.Namespace) -> dict[str, Any]:
    if args.out.exists():
        raise FileExistsError("seal output path must be new")
    manifest = load_task_manifest(args.task_manifest)
    issues = verify_manifest_inputs(args.task_manifest, manifest)
    if issues:
        raise ValueError("task manifest validation failed: " + "; ".join(issues))
    fingerprint = str(manifest["task_fingerprint_sha256"])
    source_srt_sha = _manifest_input_sha(manifest, "source_srt")
    final_audio_sha = _manifest_input_sha(manifest, "audio")

    suite = _load(args.calibration_suite_summary)
    suite_sha = str(suite.get("suite_sha256") or "")
    if suite.get("final_audio_sha256") != final_audio_sha or len(suite_sha) != 64:
        raise ValueError("calibration suite belongs to another final mix")
    kind_ready = suite.get("boundary_kind_authority_ready")
    if kind_ready != {"start": False, "end": False, "internal": False}:
        raise ValueError("single-backend suite readiness changed; overlay seal assumptions are stale")

    joint = _load(args.joint_calibration)
    if not internal_selective_consensus_calibration_is_authoritative(
        joint,
        expected_final_audio_sha256=final_audio_sha,
        expected_source_suite_sha256=suite_sha,
    ):
        raise ValueError("joint internal calibration is not authoritative")
    joint_sha = str(joint["artifact_sha256"])
    audit_confirmation = (joint.get("human_gold_artifact") or {}).get("audit_confirmation") or {}
    if audit_confirmation.get("confirmed_case_count") != 24 or audit_confirmation.get("pending_case_count") != 0:
        raise ValueError("Human Gold audit is not 24/24 complete")

    plan = _load(args.internal_plan)
    plan_sha = sha256_json(plan)
    if (
        plan.get("schema_version") != "internal-segmentation-plan-1.0"
        or plan.get("task_fingerprint_sha256") != fingerprint
        or plan.get("source_srt_sha256") != source_srt_sha
        or plan.get("final_audio_sha256") != final_audio_sha
    ):
        raise ValueError("internal plan identity mismatch")

    expected_profiles = {
        str(row["profile_id"]): row for row in suite.get("backend_profiles", []) if isinstance(row, Mapping)
    }
    if set(expected_profiles) != {"sofa_mandarin_v1", "hubertfa_mandarin_v1"}:
        raise ValueError("calibration suite backend profiles are unexpected")
    raw_seals: dict[str, Any] = {}
    for raw_path in args.backend_run:
        run = _load(raw_path)
        profile = str(run.get("backend_profile_id") or "")
        expected = expected_profiles.get(profile)
        if expected is None or profile in raw_seals:
            raise ValueError("raw backend profile set is invalid")
        if (
            run.get("schema_version") != "internal-alignment-backend-run-1.0"
            or run.get("plan_sha256") != plan_sha
            or run.get("final_audio_sha256") != final_audio_sha
            or run.get("timing_authority") != "diagnostic_uncalibrated_direct_final_mix_evidence"
            or run.get("adapter_contract_revision") != expected.get("adapter_contract_revision")
            or run.get("model_revision") != expected.get("model_revision")
            or int(run.get("job_count") or -1) != len(plan.get("jobs") or [])
        ):
            raise ValueError(f"raw backend identity mismatch: {profile}")
        raw_seals[profile] = {
            "file_sha256": sha256_file(raw_path),
            "job_count": int(run["job_count"]),
            "aligned_job_count": int(run["aligned_job_count"]),
            "unaligned_job_count": int(run["unaligned_job_count"]),
            "adapter_contract_revision": str(run["adapter_contract_revision"]),
            "model_revision": str(run["model_revision"]),
            "item_isolation_applied": bool(run.get("batch_item_isolation_applied", False)),
        }
    if set(raw_seals) != set(expected_profiles):
        raise ValueError("both calibrated raw backend profiles are required")

    evidence = _load(args.evidence)
    decisions = _load(args.decisions)
    bundle = _load(args.bundle)
    bundle_sha = verify_self_sha(bundle, "bundle_sha256", label="adjudication bundle")
    evidence_sha = sha256_json(evidence)
    decisions_sha = sha256_json(decisions)
    if (
        bundle.get("mode") != "internal"
        or bundle.get("plan_sha256") != plan_sha
        or bundle.get("final_audio_sha256") != final_audio_sha
        or bundle.get("calibration_suite_sha256") != suite_sha
        or bundle.get("selective_consensus_calibration_sha256") != joint_sha
        or bundle.get("evidence_sha256") != evidence_sha
        or bundle.get("decisions_sha256") != decisions_sha
        or set(bundle.get("backend_profile_ids") or []) != set(raw_seals)
        or bool(bundle.get("subtitle_mutation_performed"))
    ):
        raise ValueError("adjudication bundle chain mismatch")
    dsummary = decisions.get("summary") or {}
    if dsummary.get("authority_mode") != "human_gold_joint_lexical_selector_v1":
        raise ValueError("internal decisions do not use joint Human Gold selector")

    materialization = _load(args.materialization_artifact)
    materialization_sha = verify_self_sha(materialization, "artifact_sha256", label="materialization")
    if (
        materialization.get("mode") != "internal"
        or materialization.get("task_fingerprint_sha256") != fingerprint
        or materialization.get("source_srt_sha256") != source_srt_sha
        or materialization.get("final_audio_sha256") != final_audio_sha
        or materialization.get("plan_sha256") != plan_sha
        or materialization.get("evidence_sha256") != evidence_sha
        or materialization.get("decisions_sha256") != decisions_sha
        or materialization.get("adjudication_bundle_sha256") != bundle_sha
        or materialization.get("calibration_suite_sha256") != suite_sha
        or int(materialization.get("automatic_mutation_count") or 0) <= 0
    ):
        raise ValueError("materialization chain mismatch")

    correction = _load(args.correction_artifact)
    correction_sha = verify_self_sha(correction, "artifact_sha256", label="post-materialization correction")
    if (
        correction.get("source_materialization_artifact_sha256") != materialization_sha
        or correction.get("task_fingerprint_sha256") != fingerprint
        or int(correction.get("timing_changed_count", -1)) != 0
        or bool(correction.get("subtitle_timing_mutation_performed"))
        or correction.get("output_srt_sha256") != sha256_file(args.final_srt)
        or correction.get("output_report_sha256") != sha256_file(args.final_report)
    ):
        raise ValueError("post-materialization correction chain mismatch")

    qa = _load(args.final_qa)
    if not (
        qa.get("task_fingerprint_sha256") == fingerprint
        and qa.get("passed") is True
        and qa.get("structurally_valid") is True
        and qa.get("fully_reviewed") is True
        and qa.get("publish_ready") is True
        and qa.get("issues") == []
        and int(qa.get("review_candidate_count", -1)) == 0
        and int(qa.get("unverified_timing_mutation_count", -1)) == 0
        and int((qa.get("project_regression") or {}).get("passed") or 0)
        == int((qa.get("project_regression") or {}).get("case_count") or -1)
    ):
        raise ValueError("final structural/text QA is not ready")

    release = _load(args.legacy_release)
    output_by_role = {str(row.get("role")): row for row in release.get("outputs", []) if isinstance(row, Mapping)}
    expected_output_shas = {
        "final_srt": sha256_file(args.final_srt),
        "audit_csv": sha256_file(args.final_report),
        "qa_json": sha256_file(args.final_qa),
    }
    for role, expected_sha in expected_output_shas.items():
        if str((output_by_role.get(role) or {}).get("sha256") or "") != expected_sha:
            raise ValueError(f"legacy release output binding mismatch: {role}")

    seal_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": "recovery_baseline_plus_human_gold_calibrated_internal_boundary_overlay",
        "task_fingerprint_sha256": fingerprint,
        "source_srt_sha256": source_srt_sha,
        "final_audio_sha256": final_audio_sha,
        "authority": {
            "internal": {
                "ready": True,
                "mode": "human_gold_joint_lexical_selector_v1",
                "joint_calibration_artifact_sha256": joint_sha,
                "calibration_suite_sha256": suite_sha,
                "automatic_boundary_mutation_count": int(materialization["automatic_mutation_count"]),
                "audio_verified_internal_split_row_count": int(materialization["audio_verified_internal_split_row_count"]),
            },
            "outer_start": {"ready": False, "fallback": "keep_editor"},
            "outer_end": {"ready": False, "fallback": "keep_editor"},
        },
        "human_gold": {
            "artifact_sha256": str(joint["human_gold_artifact_sha256"]),
            "records_sha256": str(joint["human_gold_records_sha256"]),
            "selection_lock_sha256": str(joint["selection_lock_sha256"]),
            "confirmed_cases": 24,
            "pending_cases": 0,
        },
        "internal_plan": {"semantic_sha256": plan_sha, "file_sha256": sha256_file(args.internal_plan)},
        "raw_backend_runs": raw_seals,
        "adjudication": {
            "bundle_sha256": bundle_sha,
            "bundle_file_sha256": sha256_file(args.bundle),
            "evidence_sha256": evidence_sha,
            "decisions_sha256": decisions_sha,
            "automatic_split_boundary_count": int(dsummary["automatic_split_boundary_count"]),
            "internal_boundary_count": int(dsummary["internal_boundary_count"]),
        },
        "materialization": {
            "artifact_sha256": materialization_sha,
            "artifact_file_sha256": sha256_file(args.materialization_artifact),
            "input_row_count": int(materialization["input_row_count"]),
            "output_row_count": int(materialization["output_row_count"]),
        },
        "post_materialization_correction": {
            "artifact_sha256": correction_sha,
            "artifact_file_sha256": sha256_file(args.correction_artifact),
            "correction_count": int(correction["correction_count"]),
            "timing_changed_count": 0,
        },
        "final": {
            "srt_sha256": expected_output_shas["final_srt"],
            "report_sha256": expected_output_shas["audit_csv"],
            "qa_sha256": expected_output_shas["qa_json"],
            "legacy_release_file_sha256": sha256_file(args.legacy_release),
            "cue_count": int(qa["final_cue_count"]),
            "publish_ready": True,
            "review_candidate_count": 0,
            "project_regression": dict(qa["project_regression"]),
        },
        "full_max_semantic_sync_release": {
            "claimed": False,
            "reason": "This recovery-overlay chain has no exact current Max run+fusion pair; it must not impersonate v4_audit_semantic_sync/v4_validate_release semantic authority.",
        },
        "known_conservative_fallbacks": [
            "outer start/end remain editor timing because both single-backend Human Gold calibration scopes are non-authoritative",
            "the English 'whenever you come whatever we talk' long cue remains unsplit because SOFA lacks required lexical coverage; no single-backend evidence is promoted",
        ],
    }
    seal_payload["seal_sha256"] = sha256_json(seal_payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(seal_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return seal_payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task-manifest", type=Path, required=True)
    p.add_argument("--calibration-suite-summary", type=Path, required=True)
    p.add_argument("--joint-calibration", type=Path, required=True)
    p.add_argument("--internal-plan", type=Path, required=True)
    p.add_argument("--backend-run", type=Path, action="append", required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--decisions", type=Path, required=True)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--materialization-artifact", type=Path, required=True)
    p.add_argument("--correction-artifact", type=Path, required=True)
    p.add_argument("--final-srt", type=Path, required=True)
    p.add_argument("--final-report", type=Path, required=True)
    p.add_argument("--final-qa", type=Path, required=True)
    p.add_argument("--legacy-release", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    result = seal(args)
    print(json.dumps({"out": str(args.out), "seal_sha256": result["seal_sha256"], "publish_ready": result["final"]["publish_ready"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
