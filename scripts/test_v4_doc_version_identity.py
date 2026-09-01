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


if __name__ == "__main__":
    unittest.main()
