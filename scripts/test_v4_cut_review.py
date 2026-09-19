import unittest

from lyric_aligner import __version__
from lyric_aligner.audio.cuts import discontinuity_candidate_id
from lyric_aligner.review.decisions import (
    ReviewDecisionError,
    apply_review_template,
    build_review_template,
    normalize_review_issue,
)


FINGERPRINT = "8" * 64
BASE_ARTIFACT = "9" * 64


def discontinuity():
    return {
        "mix_before": 4.0,
        "mix_after": 6.0,
        "source_before": 4.0,
        "source_after": 9.0,
        "observed_source_jump": 5.0,
        "excess_source_jump": 3.0,
        "reason": "forward source jump",
    }


def issue():
    row = discontinuity()
    return {
        "kind": "timewarp_discontinuity",
        "code": "source_position_discontinuity",
        "candidate_id": discontinuity_candidate_id("occ-1", row),
        "occurrence_id": "occ-1",
        "status": "review",
        "selection": "CONFIRMED_CUT_REQUIRED",
        "reason": row["reason"],
        **row,
    }


def run_payload():
    return {
        "schema_version": "1.2",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "calibration_profile_version": "profile-test",
        "calibration_profile_id": "p" * 64,
        "status": "review_required",
        "legacy_fallback_used": False,
        "occurrences": [{"occurrence_id": "occ-1"}],
        "transitions": [],
        "issues": [issue()],
    }


class V4CutReviewTests(unittest.TestCase):
    def test_discontinuity_candidate_id_changes_with_physical_jump(self):
        original = discontinuity()
        changed = {**original, "source_after": 9.5}
        self.assertNotEqual(
            discontinuity_candidate_id("occ-1", original),
            discontinuity_candidate_id("occ-1", changed),
        )

    def test_cut_issue_id_is_task_scoped(self):
        current = normalize_review_issue(
            issue(), task_fingerprint_sha256=FINGERPRINT
        )
        other = normalize_review_issue(
            issue(), task_fingerprint_sha256="a" * 64
        )
        self.assertNotEqual(current["issue_id"], other["issue_id"])

    def test_confirmed_cut_remains_blocked_until_rebuild(self):
        run = run_payload()
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        [item] = template["review_items"]
        self.assertEqual(
            item["allowed_actions"],
            ["confirmed_cut", "rejected_requires_remap"],
        )
        item["decision"] = {
            "action": "confirmed_cut",
            "rationale": "Audible edit and source evidence confirm this exact forward jump is a cut.",
        }
        reviewed = apply_review_template(
            run,
            template,
            base_run_artifact_id=BASE_ARTIFACT,
        )
        self.assertEqual(reviewed["status"], "review_required")
        [remaining] = reviewed["issues"]
        self.assertEqual(remaining["status"], "confirmed")
        self.assertEqual(remaining["decision_action"], "confirmed_cut")
        self.assertTrue(remaining["requires_timeline_rebuild"])
        self.assertEqual(
            remaining["confirmed_discontinuity"],
            {
                "mix_before": 4.0,
                "mix_after": 6.0,
                "source_before": 4.0,
                "source_after": 9.0,
            },
        )

    def test_rejected_cut_cannot_be_cleared_for_render(self):
        run = run_payload()
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["decision"] = {
            "action": "rejected_requires_remap",
            "rationale": "This candidate is not a physical cut; mapping must be recomputed.",
        }
        reviewed = apply_review_template(
            run,
            template,
            base_run_artifact_id=BASE_ARTIFACT,
        )
        self.assertEqual(reviewed["status"], "review_required")
        [remaining] = reviewed["issues"]
        self.assertEqual(remaining["status"], "rejected")
        self.assertTrue(remaining["requires_timeline_rebuild"])

    def test_cut_issue_snapshot_tamper_fails_closed(self):
        run = run_payload()
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["issue"]["source_after"] = 12.0
        with self.assertRaisesRegex(ReviewDecisionError, "snapshot no longer matches"):
            apply_review_template(
                run,
                template,
                base_run_artifact_id=BASE_ARTIFACT,
            )


if __name__ == "__main__":
    unittest.main()
