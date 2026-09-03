from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import karaoke_subtitle_pipeline as retired


class RetiredKaraokeSubtitlePipelineTests(unittest.TestCase):
    def test_help_is_migration_only(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = retired.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("has been retired", stdout.getvalue())
        self.assertIn("scripts/v4_run.py", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_historical_command_fails_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = retired.main(["inspect"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("has been retired", stderr.getvalue())
        self.assertIn("semantically equivalent", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
