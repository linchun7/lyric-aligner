#!/usr/bin/env python3
"""Repository-local structural validator for the lyric-aligner Skill."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # Base installs still receive a useful structural check.
    yaml = None


REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/redo_karaoke_pipeline.py",
    "scripts/task_contract.py",
    "references/workflow.md",
)


def validate(root: Path) -> list[str]:
    issues: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            issues.append(f"missing required Skill file: {relative}")

    skill_path = root / "SKILL.md"
    if skill_path.is_file():
        text = skill_path.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not match:
            issues.append("SKILL.md must start with YAML frontmatter")
        elif yaml is None:
            frontmatter = match.group(1)
            if not re.search(r"(?m)^name:\s*lyric-aligner\s*$", frontmatter):
                issues.append("SKILL.md name must be lyric-aligner")
            if not re.search(r"(?m)^description:\s*\S", frontmatter):
                issues.append("SKILL.md description must be non-empty")
        else:
            try:
                metadata = yaml.safe_load(match.group(1))
            except yaml.YAMLError as exc:
                issues.append(f"invalid SKILL.md frontmatter: {exc}")
            else:
                if not isinstance(metadata, dict):
                    issues.append("SKILL.md frontmatter must be an object")
                else:
                    if metadata.get("name") != "lyric-aligner":
                        issues.append("SKILL.md name must be lyric-aligner")
                    if not str(metadata.get("description", "")).strip():
                        issues.append("SKILL.md description must be non-empty")

    agent_path = root / "agents/openai.yaml"
    if agent_path.is_file():
        agent_text = agent_path.read_text(encoding="utf-8")
        if yaml is None:
            if "$lyric-aligner" not in agent_text:
                issues.append("agents/openai.yaml must explicitly invoke $lyric-aligner")
        else:
            try:
                agent = yaml.safe_load(agent_text)
            except yaml.YAMLError as exc:
                issues.append(f"invalid agents/openai.yaml: {exc}")
            else:
                if not isinstance(agent, dict):
                    issues.append("agents/openai.yaml must contain an object")
                if "$lyric-aligner" not in json.dumps(agent, ensure_ascii=False):
                    issues.append("agents/openai.yaml must explicitly invoke $lyric-aligner")
    return issues


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    issues = validate(root)
    if issues:
        print(json.dumps({"ok": False, "issues": issues}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "root": str(root)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
