#!/usr/bin/env python3
"""Fail when tracked Skill source contains local data or sensitive paths."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


FORBIDDEN_PREFIXES = ("private/", "output/")
FORBIDDEN_SUFFIXES = (
    ".srt",
    ".lrc",
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".mp4",
    ".mov",
    ".mkv",
)


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan(root: Path) -> list[str]:
    issues: list[str] = []
    local_path_patterns = (
        re.compile(re.escape("C:" + "\\" + "Users" + "\\"), re.IGNORECASE),
        re.compile("/" + "Users" + "/", re.IGNORECASE),
        re.compile("/" + "home" + "/", re.IGNORECASE),
    )
    credential_patterns = (
        re.compile("github_" + "pat_" + r"[A-Za-z0-9_]{20,}"),
        re.compile("gh" + "p_" + r"[A-Za-z0-9]{20,}"),
        re.compile(r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----"),
    )
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        folded = relative.casefold()
        if folded.startswith(FORBIDDEN_PREFIXES):
            issues.append(f"tracked local-data path: {relative}")
        if folded.endswith(FORBIDDEN_SUFFIXES):
            issues.append(f"tracked media/subtitle file: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in (*local_path_patterns, *credential_patterns):
            if pattern.search(text):
                issues.append(f"sensitive content pattern in {relative}")
                break
    return issues


def main() -> int:
    root = Path(".").resolve()
    issues = scan(root)
    print(json.dumps({"ok": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
