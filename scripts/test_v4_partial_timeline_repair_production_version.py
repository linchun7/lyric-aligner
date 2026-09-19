from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_context import EffectiveRunMappingContext
from lyric_aligner.timeline.partial_repair_production import (
    bridge_effective_artifacts_to_partial_repair,
)


class PartialTimelineRepairProductionVersionTests(unittest.TestCase):
    def test_non_current_algorithm_context_is_rejected_before_fusion_use(self):
        old_context = EffectiveRunMappingContext(
            schema_version="1.0",
            task_fingerprint_sha256="f" * 64,
            algorithm_version="0.0.0-old",
            run_stage="production_orchestration",
            run_artifact_id="run-artifact",
            occurrences=(),
        )
        with patch(
            "lyric_aligner.timeline.partial_repair_production."
            "derive_effective_run_mapping_context",
            return_value=old_context,
        ):
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "non-current algorithm version",
            ):
                bridge_effective_artifacts_to_partial_repair(
                    cues=[],
                    run_path=Path("unused-run.json"),
                    run_artifact_path=Path("unused-run.artifact.json"),
                    fusion_path=Path("must-not-be-read.json"),
                    fusion_artifact_path=Path("must-not-be-read.artifact.json"),
                    explicit_trust=[],
                )


if __name__ == "__main__":
    unittest.main()
