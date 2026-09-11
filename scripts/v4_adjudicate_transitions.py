#!/usr/bin/env python3
"""Build task-bound shadow lexical evidence for Max transition ambiguities."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    sha256_file,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.review.decisions import build_review_template, normalize_review_issue
from lyric_aligner.review.transition_lexical import (
    POLICY_ID,
    SCHEMA_VERSION,
    TransitionLexicalEvidenceError,
    TransitionLexicalPolicy,
    build_transition_lexical_evidence,
)
from lyric_aligner.text_repair import parse_srt_text
from task_contract import load_task_manifest, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _task_path(task: dict, key: str) -> Path:
    row = task.get("inputs", {}).get(key)
    if not isinstance(row, dict) or not str(row.get("path") or ""):
        raise ValueError(f"task manifest is missing input {key}")
    path = (REPOSITORY_ROOT / str(row["path"])).resolve()
    if not path.is_file():
        raise ValueError(f"task input does not exist: {path}")
    if sha256_file(path) != str(row.get("sha256") or ""):
        raise ValueError(f"task input hash mismatch: {key}")
    return path


def _load_timeline(
    occurrence: dict,
    *,
    fingerprint: str,
) -> tuple[dict, dict]:
    timeline_path = occurrence.get("timeline_path")
    artifact_path = occurrence.get("timeline_artifact_path")
    if not timeline_path or not artifact_path:
        raise ValueError("transition occurrence is missing canonical timeline provenance")
    timeline = Path(str(timeline_path))
    artifact_file = Path(str(artifact_path))
    payload = _load(timeline)
    artifact = _load(artifact_file)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage="canonical_timeline_projection",
    )
    issues.extend(
        validate_artifact_output(artifact, role="canonical_timeline", path=timeline)
    )
    if issues:
        raise ValueError("invalid canonical timeline artifact: " + "; ".join(issues))
    return payload, artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--review-template-out", required=True, type=Path)
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        manifest_issues = verify_manifest_inputs(args.task_manifest, task)
        if manifest_issues:
            raise ValueError(
                "task manifest validation failed: " + "; ".join(manifest_issues)
            )
        fingerprint = str(task["task_fingerprint_sha256"])
        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        artifact_issues = validate_upstream_artifact(
            run_artifact,
            expected_task_fingerprint=fingerprint,
            expected_algorithm_version=__version__,
            expected_stage="production_orchestration",
        )
        artifact_issues.extend(
            validate_artifact_output(
                run_artifact, role="v4_production_run", path=args.run
            )
        )
        if artifact_issues:
            raise ValueError(
                "invalid production run artifact: " + "; ".join(artifact_issues)
            )
        if run.get("status") != "review_required":
            raise ValueError("transition lexical evidence requires review_required run")
        if run.get("legacy_fallback_used") is not False:
            raise ValueError("transition lexical evidence refuses legacy fallback")
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("production run belongs to another task")

        source_srt = _task_path(task, "source_srt")
        protected = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected.update(
            {"production_run": args.run, "production_run_artifact": args.run_artifact}
        )
        validate_separate_artifact_paths(
            inputs=protected,
            outputs={
                "transition_lexical_evidence": args.out,
                "transition_lexical_artifact": args.artifact_out,
                "transition_review_template": args.review_template_out,
            },
        )

        _, editor_cues = parse_srt_text(source_srt.read_text(encoding="utf-8-sig"))
        occurrences = {
            str(row.get("occurrence_id") or ""): row
            for row in run.get("occurrences", [])
            if isinstance(row, dict) and str(row.get("occurrence_id") or "")
        }
        transitions = [
            row for row in run.get("transitions", []) if isinstance(row, dict)
        ]
        transition_by_pair: dict[tuple[str, str], dict] = {}
        for transition in transitions:
            pair = (
                str(transition.get("left_occurrence_id") or ""),
                str(transition.get("right_occurrence_id") or ""),
            )
            if not all(pair) or pair in transition_by_pair:
                raise ValueError("production run contains invalid/duplicate transition pair")
            transition_by_pair[pair] = transition

        raw_issues = [
            row
            for row in run.get("issues", [])
            if isinstance(row, dict) and row.get("kind") == "transition_ambiguity"
        ]
        if not raw_issues:
            raise ValueError("production run contains no transition ambiguity issues")

        policy = TransitionLexicalPolicy()
        evidence_rows: list[dict] = []
        timeline_artifact_ids: set[str] = set()
        for issue in raw_issues:
            pair = (
                str(issue.get("left_occurrence_id") or ""),
                str(issue.get("right_occurrence_id") or ""),
            )
            transition = transition_by_pair.get(pair)
            if transition is None:
                raise ValueError(
                    "transition ambiguity has no exact transition summary"
                )
            left_occurrence = occurrences.get(pair[0])
            right_occurrence = occurrences.get(pair[1])
            if left_occurrence is None or right_occurrence is None:
                raise ValueError("transition ambiguity references unknown occurrence")
            left_timeline, left_artifact = _load_timeline(
                left_occurrence, fingerprint=fingerprint
            )
            right_timeline, right_artifact = _load_timeline(
                right_occurrence, fingerprint=fingerprint
            )
            timeline_artifact_ids.update(
                {
                    str(left_artifact["artifact_id"]),
                    str(right_artifact["artifact_id"]),
                }
            )
            row = build_transition_lexical_evidence(
                issue=issue,
                transition=transition,
                left_occurrence=left_occurrence,
                right_occurrence=right_occurrence,
                left_timeline=left_timeline,
                right_timeline=right_timeline,
                editor_cues=editor_cues,
                policy=policy,
            )
            normalized_issue = normalize_review_issue(
                issue, task_fingerprint_sha256=fingerprint
            )
            row["review_issue_id"] = normalized_issue["issue_id"]
            evidence_rows.append(row)

        evidence_rows.sort(key=lambda row: int(row["nominal_boundary_ms"]))
        summary = {
            "transition_count": len(evidence_rows),
            "clear_candidate_count": sum(
                row["recommendation"] == "clear_candidate"
                for row in evidence_rows
            ),
            "review_count": sum(
                row["recommendation"] == "review" for row in evidence_rows
            ),
            "automatic_authority_granted_count": 0,
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "policy_id": POLICY_ID,
            "task_fingerprint_sha256": fingerprint,
            "source_srt_sha256": sha256_file(source_srt),
            "run_sha256": sha256_file(args.run),
            "run_artifact_id": str(run_artifact["artifact_id"]),
            "run_artifact_sha256": sha256_file(args.run_artifact),
            "automatic_authority_granted": False,
            "timing_mutation_performed": False,
            "summary": summary,
            "transitions": evidence_rows,
        }
        atomic_write_json(args.out, payload)

        # Emit the standard, still-empty review template. Evidence never pre-fills
        # decisions: a reviewer must explicitly accept task-specific clear candidates.
        review_template = build_review_template(
            run, base_run_artifact_id=str(run_artifact["artifact_id"])
        )
        atomic_write_json(args.review_template_out, review_template)

        upstream_ids = set(timeline_artifact_ids)
        upstream_ids.add(str(run_artifact["artifact_id"]))
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="adjacent_transition_editor_lexical_evidence",
            algorithm_version=__version__,
            outputs=(
                ("transition_editor_lexical_evidence", args.out),
                ("transition_review_template", args.review_template_out),
            ),
            normalized_config={
                "policy_id": POLICY_ID,
                "policy": asdict(policy),
                "automatic_authority_granted": False,
                "timing_mutation_performed": False,
            },
            upstream_artifact_ids=tuple(sorted(upstream_ids)),
            evidence=summary,
        )
        atomic_write_json(args.artifact_out, artifact)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        TransitionLexicalEvidenceError,
    ) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
