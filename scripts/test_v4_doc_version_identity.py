from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"$', re.MULTILINE)


class V4DocumentationVersionIdentityTests(unittest.TestCase):
    def test_authoritative_docs_match_runtime_algorithm_version(self) -> None:
        init_text = (REPOSITORY_ROOT / "lyric_aligner/__init__.py").read_text(
            encoding="utf-8"
        )
        match = VERSION_RE.search(init_text)
        self.assertIsNotNone(match)
        expected = f"主线算法版本：`{match.group(1)}`"
        for relative in (
            "references/v4-status.md",
            "references/v4-runtime-guide.md",
        ):
            with self.subTest(path=relative):
                content = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(expected, content)

        algorithm_version = match.group(1)
        task_template = (REPOSITORY_ROOT / "references/task-template.md").read_text(
            encoding="utf-8"
        )
        workflow = (REPOSITORY_ROOT / "references/workflow.md").read_text(
            encoding="utf-8"
        )
        roadmap = (REPOSITORY_ROOT / "references/multilingual-roadmap.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"当前 Max 主线版本：`{algorithm_version}`", task_template)
        self.assertIn(f"当前 Max：`{algorithm_version}`", workflow)
        self.assertIn(f"Max      -> Full V4 Alignment {algorithm_version}", roadmap)
        self.assertNotIn("新任务默认使用算法 `v3.9`", task_template)
        self.assertNotIn("当前完整生产算法为 `v3.9`", workflow)
        self.assertNotIn("## 当前状态：v3.9", roadmap)


if __name__ == "__main__":
    unittest.main()
