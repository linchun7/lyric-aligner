#!/usr/bin/env python3
"""Re-verify the current subtitle release set from a prior audit inventory.

The prior audit is used only as the inventory/expected identity. Current files,
manifest inputs, QA/release bindings, masking and invalidation state are read
again on every run. Historical `passed=true` is never trusted as current state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.task_contract import load_task_manifest, verify_manifest_inputs

OUTPUT_ROOT = REPOSITORY_ROOT / "output"
PRIVATE_ROOT = REPOSITORY_ROOT / "private"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def repo_label(path: Path) -> str:
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return str(path)


def build_manifest_index() -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    index: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    if not PRIVATE_ROOT.is_dir():
        return index
    for path in PRIVATE_ROOT.rglob("task_manifest.json"):
        try:
            payload = load_task_manifest(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        fingerprint = str(payload.get("task_fingerprint_sha256") or "").strip()
        if len(fingerprint) == 64:
            index.setdefault(fingerprint, []).append((path, payload))
    return index


def release_json_candidates(final_srt: Path) -> list[Path]:
    """Return bounded JSON candidates in the exact final release ancestry."""

    if not OUTPUT_ROOT.is_dir():
        return []
    output_root = OUTPUT_ROOT.resolve()
    current = final_srt.parent
    candidates: set[Path] = set()
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            break
        if resolved != output_root and output_root not in resolved.parents:
            break
        candidates.update(path for path in current.glob("*.json") if path.is_file())
        qa_dir = current / "qa"
        if qa_dir.is_dir():
            candidates.update(path for path in qa_dir.glob("*.json") if path.is_file())
        if resolved == output_root:
            break
        current = current.parent
    return sorted(
        path for path in candidates if not path.name.upper().startswith("INVALIDATED")
    )


def invalidation_markers(final_srt: Path) -> list[str]:
    markers: list[str] = []
    current = final_srt.parent
    output_root = OUTPUT_ROOT.resolve()
    while True:
        for candidate in current.glob("*INVALIDATED*"):
            if candidate.is_file():
                markers.append(repo_label(candidate))
        try:
            resolved = current.resolve()
        except OSError:
            break
        if resolved == output_root or output_root not in resolved.parents:
            break
        current = current.parent
    return sorted(set(markers))


def invalidation_references(*, fingerprint: str, final_sha: str) -> list[str]:
    """Return tracked invalidation references bound to the exact release identity."""

    matches: list[str] = []
    for path in (REPOSITORY_ROOT / "references").glob("*invalidation*.json"):
        try:
            payload = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            str(payload.get("task_fingerprint_sha256") or "") == fingerprint
            and str(payload.get("final_srt_sha256") or "") == final_sha
            and (
                payload.get("release_publish_ready") is False
                or payload.get("invalidates_publish_ready_claim") is True
            )
        ):
            matches.append(repo_label(path))
    return sorted(matches)


def raw_unmasked_fword_count(text: str) -> int:
    # `f*`/`f**k` are masked; count only an unmasked lexical f-word token.
    return len(re.findall(r"(?i)(?<![\w*])fuck(?:ing|ed|er|s)?(?![\w*])", text))


def output_binding_status(
    *,
    fingerprint: str,
    final_sha: str,
    final_srt: Path,
) -> tuple[bool, bool, list[str]]:
    qa_ready = False
    release_bound = False
    evidence_paths: list[str] = []
    for path in release_json_candidates(final_srt):
        try:
            payload = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(payload.get("task_fingerprint_sha256") or "") != fingerprint:
            continue
        outputs = payload.get("outputs")
        if not isinstance(outputs, list):
            continue
        output_by_role = {
            str(row.get("role") or ""): row
            for row in outputs
            if isinstance(row, Mapping)
        }
        final_record = output_by_role.get("final_srt")
        if not isinstance(final_record, Mapping) or str(final_record.get("sha256") or "") != final_sha:
            continue
        release_bound = True
        evidence_paths.append(repo_label(path))

        qa_record = output_by_role.get("qa_json")
        if not isinstance(qa_record, Mapping):
            continue
        raw_qa_path = str(qa_record.get("path") or "").strip()
        qa_sha = str(qa_record.get("sha256") or "").strip()
        if not raw_qa_path or len(qa_sha) != 64:
            continue
        qa_path = resolve_repo_path(raw_qa_path)
        if not qa_path.is_file() or sha256_file(qa_path) != qa_sha:
            continue
        try:
            qa = load_json(qa_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            str(qa.get("task_fingerprint_sha256") or "") == fingerprint
            and qa.get("publish_ready") is True
            and int(qa.get("review_candidate_count", 0) or 0) == 0
        ):
            qa_ready = True
            evidence_paths.append(repo_label(qa_path))
    return qa_ready, release_bound, sorted(set(evidence_paths))


def reverify(source_audit: Mapping[str, Any]) -> dict[str, Any]:
    manifest_index = build_manifest_index()
    output_rows: list[dict[str, Any]] = []
    for source_row in source_audit.get("rows", []):
        if not isinstance(source_row, Mapping):
            continue
        name = str(source_row.get("name") or "")
        fingerprint = str(source_row.get("task_fingerprint_sha256") or "")
        final_srt = resolve_repo_path(str(source_row.get("final_srt") or ""))
        expected_sha = str(source_row.get("final_srt_sha256") or "")
        problems: list[str] = []
        actual_sha = ""
        raw_fword_count = -1
        if not final_srt.is_file():
            problems.append("final SRT is missing")
        else:
            actual_sha = sha256_file(final_srt)
            if actual_sha != expected_sha:
                problems.append("final SRT SHA differs from audit inventory")
            raw_fword_count = raw_unmasked_fword_count(final_srt.read_text(encoding="utf-8-sig"))
            if raw_fword_count:
                problems.append("final SRT contains an unmasked f-word")

        markers = invalidation_markers(final_srt) if final_srt.parent.exists() else []
        if markers:
            problems.append("release has INVALIDATED marker")
        invalidation_refs = invalidation_references(
            fingerprint=fingerprint, final_sha=actual_sha or expected_sha
        )
        if invalidation_refs:
            problems.append("release has tracked invalidation reference")

        manifest_candidates = manifest_index.get(fingerprint, [])
        manifest_checks = []
        for manifest_path, manifest in manifest_candidates:
            current_issues = verify_manifest_inputs(manifest_path, manifest)
            manifest_checks.append(
                {
                    "path": repo_label(manifest_path),
                    "issues": current_issues,
                    "current": not current_issues,
                }
            )
        manifest_current = any(row["current"] for row in manifest_checks)
        if not manifest_candidates:
            problems.append("no task manifest found for task fingerprint")
        elif not manifest_current:
            problems.append("no matching task manifest has current input SHAs")

        qa_ready, release_bound, binding_paths = output_binding_status(
            fingerprint=fingerprint,
            final_sha=actual_sha or expected_sha,
            final_srt=final_srt,
        )
        if not qa_ready:
            problems.append("no current publish-ready zero-review QA artifact found")
        if not release_bound:
            problems.append("no release artifact binds the current final SRT SHA")

        output_rows.append(
            {
                "name": name,
                "task_fingerprint_sha256": fingerprint,
                "final_srt": str(source_row.get("final_srt") or ""),
                "expected_final_srt_sha256": expected_sha,
                "actual_final_srt_sha256": actual_sha,
                "manifest_inputs_current": manifest_current,
                "manifest_checks": manifest_checks,
                "qa_publish_ready_zero_review": qa_ready,
                "release_binds_final_srt": release_bound,
                "binding_evidence_paths": binding_paths,
                "raw_unmasked_fword_count": raw_fword_count,
                "invalidation_markers": markers,
                "invalidation_references": invalidation_refs,
                "problems": problems,
                "passed": not problems,
            }
        )
    return {
        "schema_version": "subtitle-release-set-reverification-1.2-bounded-release-binding",
        "source_audit_schema_version": source_audit.get("schema_version"),
        "release_count": len(output_rows),
        "passed_count": sum(bool(row["passed"]) for row in output_rows),
        "all_passed": bool(output_rows) and all(bool(row["passed"]) for row in output_rows),
        "rows": output_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit",
        type=Path,
        default=REPOSITORY_ROOT / "references" / "subtitle-release-set-audit-2026-09-06.json",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    source = load_json(args.audit)
    result = reverify(source)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
