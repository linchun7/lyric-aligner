from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import v4_run_legacy


class V4RunIntervalSerializationTests(unittest.TestCase):
    def test_terminal_mix_end_round_trips_without_exceeding_duration(self) -> None:
        mix_duration = 123.4567896
        command = v4_run_legacy._coarse_command(
            task_manifest=Path("task.json"),
            mix_audio=Path("mix.wav"),
            track_assets=Path("assets.json"),
            asset_artifact=Path("assets.artifact.json"),
            occurrence_id="occ-final",
            out=Path("coarse.json"),
            artifact_out=Path("coarse.artifact.json"),
            git_commit="",
            mix_start=100.0,
            mix_end=mix_duration,
        )

        serialized_end = command[command.index("--mix-end") + 1]
        self.assertEqual(float(serialized_end), mix_duration)
        self.assertLessEqual(float(serialized_end), mix_duration)


if __name__ == "__main__":
    unittest.main()
