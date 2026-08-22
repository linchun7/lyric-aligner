import unittest

from v4_render import (
    _artifact_config,
    _json_int,
    _validate_combined_metadata,
    _validate_cut_metadata,
    _validate_review_only,
)


class V4RenderMetadataTypeTests(unittest.TestCase):
    def test_json_int_rejects_python_coercion_values(self):
        self.assertEqual(
            _json_int({"count": 0}, "count", label="synthetic", minimum=0),
            0,
        )
        for invalid in (False, 0.0, 0.5, "0", None):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "must be a JSON integer"):
                    _json_int(
                        {"count": invalid},
                        "count",
                        label="synthetic",
                        minimum=0,
                    )

    def test_review_resolution_requires_integer_remaining_count(self):
        artifact = {"normalized_config": {"base_run_artifact_id": "base-run"}}
        for invalid in (False, 0.0, "0", None):
            with self.subTest(value=invalid):
                run = {
                    "review_resolution": {
                        "base_run_artifact_id": "base-run",
                        "remaining_issue_count": invalid,
                    }
                }
                with self.assertRaisesRegex(ValueError, "must be a JSON integer"):
                    _validate_review_only(run, artifact, {"base-run"})

    def test_cut_rebuild_authority_counts_require_integers(self):
        base_metadata = {
            "source_review_artifact_id": "review-artifact",
            "remaining_issue_count": 0,
            "canonical_fragment_issue_count": 0,
            "rebuilt_occurrence_count": 1,
            "new_mapping_artifact_ids": ["mapping-artifact"],
            "new_timeline_artifact_ids": ["timeline-artifact"],
        }
        artifact = {
            "normalized_config": {
                "source_review_artifact_id": "review-artifact",
            }
        }
        upstreams = {
            "review-artifact",
            "mapping-artifact",
            "timeline-artifact",
        }
        for key in (
            "remaining_issue_count",
            "canonical_fragment_issue_count",
            "rebuilt_occurrence_count",
        ):
            with self.subTest(key=key):
                metadata = dict(base_metadata)
                metadata[key] = False
                with self.assertRaisesRegex(ValueError, "must be a JSON integer"):
                    _validate_cut_metadata(
                        {"cut_rebuild": metadata},
                        artifact,
                        upstreams,
                        require_resolved=True,
                    )

    def test_combined_occurrence_count_requires_integer(self):
        run = {
            "combined_recomposition": {
                "remaining_issue_count": 0,
                "source_review_artifact_id": "review-artifact",
                "source_cut_artifact_id": "cut-artifact",
                "source_overlap_artifact_id": "overlap-artifact",
                "new_timeline_artifact_ids": [],
                "combined_occurrence_count": False,
            }
        }
        artifact = {
            "normalized_config": {
                "source_review_artifact_id": "review-artifact",
                "source_cut_artifact_id": "cut-artifact",
                "source_overlap_artifact_id": "overlap-artifact",
            }
        }
        with self.assertRaisesRegex(ValueError, "must be a JSON integer"):
            _validate_combined_metadata(
                run,
                artifact,
                {"review-artifact", "cut-artifact", "overlap-artifact"},
                source_review_id="review-artifact",
            )

    def test_run_artifact_config_must_be_object(self):
        with self.assertRaisesRegex(ValueError, "invalid normalized_config"):
            _artifact_config({"normalized_config": []}, label="synthetic artifact")


if __name__ == "__main__":
    unittest.main()
