#!/usr/bin/env python3
"""Fail CI when substantive production changes do not update owning docs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath


CHANGE_RECORD = "references/v4-change-record.md"
STATUS_DOC = "references/v4-status.md"
CLI_DOCS = {
    "SKILL.md",
    "references/v4-cli-contract.md",
    "references/v4-runtime-guide.md",
    "references/workflow.md",
}
CONTRACT_DOCS = {
    "references/v4-implementation.md",
    "references/workflow.md",
    "references/documentation-contract.md",
}
ARCH_DOCS = {
    "references/v4-implementation.md",
    "references/v4-architecture-review-2026-08-17.md",
}


@dataclass(frozen=True)
class DocContractResult:
    changed: tuple[str, ...]
    substantive: tuple[str, ...]
    issues: tuple[str, ...]


def _is_doc(path: str) -> bool:
    return path == "SKILL.md" or path.startswith("references/")


def _is_test(path: str) -> bool:
    return path.startswith("scripts/test_") or "/tests/" in path


def _is_ci_only(path: str) -> bool:
    return path.startswith(".github/")


def is_substantive(path: str) -> bool:
    if _is_doc(path) or _is_test(path) or _is_ci_only(path):
        return False
    if path.startswith("lyric_aligner/") and path.endswith(".py"):
        return True
    if path.startswith("scripts/v4_") and path.endswith(".py"):
        return True
    if path in {
        "scripts/redo_karaoke_pipeline.py",
        "scripts/task_contract.py",
        "scripts/init_task.py",
        "scripts/migrate_task.py",
    }:
        return True
    if path.startswith("requirements") and path.endswith(".txt"):
        return True
    return False


def _needs_status(path: str) -> bool:
    return (
        path.startswith("lyric_aligner/")
        or path.startswith("scripts/v4_")
        or path == "scripts/redo_karaoke_pipeline.py"
    )


def _needs_cli_docs(path: str) -> bool:
    return (
        path.startswith("scripts/v4_")
        or path in {
            "scripts/redo_karaoke_pipeline.py",
            "scripts/task_contract.py",
            "scripts/init_task.py",
            "scripts/migrate_task.py",
        }
    )


def _needs_contract_docs(path: str) -> bool:
    return (
        path.startswith("lyric_aligner/contracts/")
        or path.startswith("lyric_aligner/assets/")
        or path.startswith("lyric_aligner/qa/")
        or path in {
            "lyric_aligner/config.py",
            "lyric_aligner/domain.py",
            "scripts/task_contract.py",
            "scripts/v4_validate_release.py",
            "scripts/v4_resolve_assets.py",
        }
    )


def _needs_arch_docs(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2 or parts[0] != "lyric_aligner":
        return False
    return parts[1] in {
        "pipeline",
        "timeline",
        "evidence",
        "alignment",
        "fusion",
        "calibration",
        "cache",
        "legacy",
    }


def validate_changed_paths(paths: list[str]) -> DocContractResult:
    changed = tuple(sorted(set(path.strip() for path in paths if path.strip())))
    changed_set = set(changed)
    substantive = tuple(path for path in changed if is_substantive(path))
    if not substantive:
        return DocContractResult(changed, substantive, ())

    issues: list[str] = []
    if CHANGE_RECORD not in changed_set:
        issues.append(
            f"substantive production changes require {CHANGE_RECORD}"
        )

    if any(_needs_status(path) for path in substantive) and STATUS_DOC not in changed_set:
        issues.append(
            f"v4 production/status changes require {STATUS_DOC}"
        )

    if any(_needs_cli_docs(path) for path in substantive) and not (CLI_DOCS & changed_set):
        issues.append(
            "CLI/workflow changes require one of: " + ", ".join(sorted(CLI_DOCS))
        )

    if any(_needs_contract_docs(path) for path in substantive) and not (
        CONTRACT_DOCS & changed_set
    ):
        issues.append(
            "schema/contract changes require one of: "
            + ", ".join(sorted(CONTRACT_DOCS))
        )

    if any(_needs_arch_docs(path) for path in substantive) and not (ARCH_DOCS & changed_set):
        issues.append(
            "architecture responsibility changes require one of: "
            + ", ".join(sorted(ARCH_DOCS))
        )

    return DocContractResult(changed, substantive, tuple(issues))


def git_changed_paths(base: str, head: str) -> list[str]:
    command = ["git", "diff", "--name-only", f"{base}...{head}"]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "cannot compute documentation-contract diff: "
            + completed.stderr.strip()
        )
    return completed.stdout.splitlines()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    try:
        result = validate_changed_paths(git_changed_paths(args.base, args.head))
    except RuntimeError as exc:
        parser.error(str(exc))

    if result.issues:
        print("Documentation contract FAILED", file=sys.stderr)
        print("Substantive files:", file=sys.stderr)
        for path in result.substantive:
            print(f"  - {path}", file=sys.stderr)
        print("Required documentation updates:", file=sys.stderr)
        for issue in result.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print(
        f"Documentation contract passed: {len(result.substantive)} substantive file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
