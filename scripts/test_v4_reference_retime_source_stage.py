import unittest

from scripts.v4_render import _validate_reference_retime_metadata


def payload(source_stage: str, source_run_id: str, source_review_id: str):
    metadata = {
        "source_run_artifact_id": source_run_id,
        "source_run_stage": source_stage,
        "source_review_artifact_id": source_review_id,
        "reference_task_fingerprint_sha256": "reference-fp",
        "reference_timeline_artifact_id": "reference-timeline",
        "retime_spec_sha256": "spec-sha",
        "retimed_occurrence_count": 1,
        "timeline_artifact_ids": ["retimed-timeline"],
    }
    run = {"reference_retime": metadata}
    artifact = {
        "normalized_config": {
            "source_run_artifact_id": source_run_id,
            "source_run_stage": source_stage,
            "source_review_artifact_id": source_review_id,
            "reference_task_fingerprint_sha256": "reference-fp",
            "reference_timeline_artifact_id": "reference-timeline",
            "retime_spec_sha256": "spec-sha",
            "retimed_occurrence_count": 1,
        }
    }
    upstreams = {source_run_id, source_review_id, "reference-timeline", "retimed-timeline"}
    return run, artifact, upstreams


class ReferenceRetimeSourceStageTests(unittest.TestCase):
    def test_review_resolution_can_directly_source_reference_retime(self):
        run, artifact, upstreams = payload(
            "review_resolution", "review-artifact", "review-artifact"
        )
        metadata, timeline_ids = _validate_reference_retime_metadata(
            run, artifact, upstreams
        )
        self.assertEqual(metadata["source_run_stage"], "review_resolution")
        self.assertEqual(timeline_ids, {"retimed-timeline"})

    def test_review_resolution_must_identify_itself_as_review_artifact(self):
        run, artifact, upstreams = payload(
            "review_resolution", "review-artifact", "other-review"
        )
        with self.assertRaisesRegex(ValueError, "identify itself"):
            _validate_reference_retime_metadata(run, artifact, upstreams)

    def test_overlap_recomposition_keeps_existing_review_lineage_contract(self):
        run, artifact, upstreams = payload(
            "overlap_recomposition", "overlap-artifact", "review-artifact"
        )
        run["overlap_recomposition"] = {
            "source_review_artifact_id": "review-artifact"
        }
        metadata, timeline_ids = _validate_reference_retime_metadata(
            run, artifact, upstreams
        )
        self.assertEqual(metadata["source_run_stage"], "overlap_recomposition")
        self.assertEqual(timeline_ids, {"retimed-timeline"})

    def test_unknown_source_stage_fails_closed(self):
        run, artifact, upstreams = payload(
            "final_render", "render-artifact", "review-artifact"
        )
        with self.assertRaisesRegex(ValueError, "review_resolution or overlap_recomposition"):
            _validate_reference_retime_metadata(run, artifact, upstreams)


if __name__ == "__main__":
    unittest.main()
