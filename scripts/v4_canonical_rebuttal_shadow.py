#!/usr/bin/env python3
"""Build an auditable shadow subtitle from authorized canonical semantic rebuttals."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.text.canonical_rebuttal import (
    build_truth_overlay,
    load_rebuttal_policy,
    materialize_rebuttal_shadow,
    sha256_file,
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _task_fingerprint(rows: list[dict[str, str]]) -> str:
    values = {str(row.get("task_fingerprint_sha256") or "").strip() for row in rows}
    values.discard("")
    if len(values) != 1:
        raise ValueError("final audit must contain exactly one task fingerprint")
    return next(iter(values))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-evaluation-audit", required=True, type=Path)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--source-audit", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--truth-overlay-out", required=True, type=Path)
    parser.add_argument("--shadow-srt-out", required=True, type=Path)
    parser.add_argument("--shadow-report-out", required=True, type=Path)
    args = parser.parse_args()
    try:
        inputs = {
            args.canonical_evaluation_audit.resolve(),
            args.source_srt.resolve(),
            args.source_audit.resolve(),
            args.policy.resolve(),
        }
        outputs = {
            args.truth_overlay_out.resolve(),
            args.shadow_srt_out.resolve(),
            args.shadow_report_out.resolve(),
        }
        if len(outputs) != 3 or inputs & outputs:
            raise ValueError("canonical rebuttal outputs must be distinct and must not overwrite inputs")
        for path in inputs:
            if not path.is_file():
                raise ValueError(f"required input does not exist: {path}")
        final_rows = _read_csv(args.source_audit)
        task_fingerprint = _task_fingerprint(final_rows)
        policy = load_rebuttal_policy(
            args.policy,
            canonical_evaluation_audit=args.canonical_evaluation_audit,
            expected_task_fingerprint=task_fingerprint,
        )
        overlay = build_truth_overlay(
            canonical_evaluation_audit=args.canonical_evaluation_audit,
            policy=policy,
        )
        source_bytes = args.source_srt.read_bytes()
        source_text = source_bytes.decode("utf-8-sig")
        had_bom = source_bytes.startswith(b"\xef\xbb\xbf")
        shadow_text, report = materialize_rebuttal_shadow(
            source_srt_text=source_text,
            final_audit_rows=final_rows,
            policy=policy,
        )
        shadow_payload = shadow_text.encode("utf-8")
        if had_bom:
            shadow_payload = b"\xef\xbb\xbf" + shadow_payload
        args.shadow_srt_out.parent.mkdir(parents=True, exist_ok=True)
        args.shadow_srt_out.write_bytes(shadow_payload)
        report.update(
            {
                "task_fingerprint_sha256": task_fingerprint,
                "canonical_evaluation_audit_sha256": sha256_file(args.canonical_evaluation_audit),
                "source_srt_sha256": sha256_file(args.source_srt),
                "source_audit_sha256": sha256_file(args.source_audit),
                "policy_file_sha256": sha256_file(args.policy),
                "shadow_srt_sha256": sha256_file(args.shadow_srt_out),
            }
        )
        _write_json(args.truth_overlay_out, overlay)
        _write_json(args.shadow_report_out, report)
    except (OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "publish_ready": report["publish_ready"],
                "authorized_lexical_rebuttal_count": report["authorized_lexical_rebuttal_count"],
                "materialized_count": report["materialized_count"],
                "deferred_count": report["deferred_count"],
                "timeline_unchanged": report["timeline_unchanged"],
                "shadow_srt_sha256": report["shadow_srt_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
