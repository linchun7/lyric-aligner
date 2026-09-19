import unittest

from lyric_aligner.alignment.batch_executor import BatchAlignmentExecutionError
from scripts.v4_run_alignment_backend_evidence import (
    _HUBERTFA_ITEM_LOCAL_UNALIGNED_REASON,
    execute_batch_with_known_item_isolation,
)


class BatchItemIsolationTests(unittest.TestCase):
    def plan(self):
        return {
            "jobs": [
                {"boundary_id": "a", "cue_number": 1, "boundary_kind": "internal"},
                {"boundary_id": "bad", "cue_number": 2, "boundary_kind": "internal"},
                {"boundary_id": "c", "cue_number": 3, "boundary_kind": "internal"},
                {"boundary_id": "d", "cue_number": 4, "boundary_kind": "internal"},
            ]
        }

    @staticmethod
    def base():
        return {
            "schema_version": "fixture-run",
            "protocol_version": "fixture-protocol",
            "backend_profile_id": "hubertfa_mandarin_v1",
            "execution_strategy": "batch",
            "jobs": [],
            "job_count": 0,
            "aligned_job_count": 0,
            "unaligned_job_count": 0,
        }

    def test_known_item_local_failure_is_bisected_to_one_unaligned_boundary(self):
        calls = []

        def fake_batch(**kwargs):
            ids = list(kwargs["selected_boundary_ids"])
            calls.append(ids)
            if "bad" in ids:
                raise BatchAlignmentExecutionError(
                    "batch aligner exited nonzero: 1: Exception: No duplicate groups"
                )
            artifact = self.base()
            artifact["job_count"] = len(ids)
            artifact["aligned_job_count"] = len(ids)
            artifact["jobs"] = [
                {
                    "boundary_id": boundary_id,
                    "cue_number": {"a": 1, "c": 3, "d": 4}[boundary_id],
                    "boundary_kind": "internal",
                    "status": "aligned",
                    "reason": "",
                    "point": {"boundary_ms": 1000},
                }
                for boundary_id in ids
            ]
            return artifact

        artifact = execute_batch_with_known_item_isolation(
            plan=self.plan(),
            mode="internal",
            final_audio_path=None,
            final_audio_sha256="a" * 64,
            config=object(),
            selected_boundary_ids=None,
            execute_batch=fake_batch,
        )
        self.assertEqual([row["boundary_id"] for row in artifact["jobs"]], ["a", "bad", "c", "d"])
        self.assertEqual(artifact["aligned_job_count"], 3)
        self.assertEqual(artifact["unaligned_job_count"], 1)
        self.assertTrue(artifact["batch_item_isolation_applied"])
        self.assertEqual(artifact["isolated_unaligned_boundary_ids"], ["bad"])
        failed = artifact["jobs"][1]
        self.assertEqual(failed["status"], "unaligned")
        self.assertIsNone(failed["point"])
        self.assertEqual(failed["reason"], _HUBERTFA_ITEM_LOCAL_UNALIGNED_REASON)
        self.assertIn([], calls)
        self.assertIn(["bad"], calls)

    def test_unknown_batch_failure_is_not_masked(self):
        def fake_batch(**kwargs):
            raise BatchAlignmentExecutionError("batch aligner exited nonzero: CUDA runtime failure")

        with self.assertRaisesRegex(BatchAlignmentExecutionError, "CUDA runtime failure"):
            execute_batch_with_known_item_isolation(
                plan=self.plan(),
                mode="internal",
                final_audio_path=None,
                final_audio_sha256="a" * 64,
                config=object(),
                selected_boundary_ids=None,
                execute_batch=fake_batch,
            )

    def test_selected_subset_preserves_plan_order_and_excludes_unrequested_jobs(self):
        def fake_batch(**kwargs):
            ids = list(kwargs["selected_boundary_ids"])
            artifact = self.base()
            artifact["job_count"] = len(ids)
            artifact["aligned_job_count"] = len(ids)
            artifact["jobs"] = [
                {
                    "boundary_id": boundary_id,
                    "cue_number": {"a": 1, "c": 3}[boundary_id],
                    "boundary_kind": "internal",
                    "status": "aligned",
                    "reason": "",
                    "point": {"boundary_ms": 1000},
                }
                for boundary_id in ids
            ]
            return artifact

        artifact = execute_batch_with_known_item_isolation(
            plan=self.plan(),
            mode="internal",
            final_audio_path=None,
            final_audio_sha256="a" * 64,
            config=object(),
            selected_boundary_ids=["c", "a"],
            execute_batch=fake_batch,
        )
        self.assertEqual([row["boundary_id"] for row in artifact["jobs"]], ["a", "c"])
        self.assertNotIn("batch_item_isolation_applied", artifact)


if __name__ == "__main__":
    unittest.main()
