import unittest

from lyric_aligner import __version__
from lyric_aligner.review.decisions import (
    ReviewDecisionError,
    apply_review_template,
    build_review_template,
    normalize_review_issue,
)


FINGERPRINT = "e" * 64
BASE_ARTIFACT = "b" * 64


def transition_issue(reason="adjacent transition has overlap/ambiguity evidence"):
    return {
        "kind": "transition",
        "left_occurrence_id": "occ-left",
        "right_occurrence_id": "occ-right",
        "status": "review",
        "reason": reason,
        "overlap_candidate_count": 1,
    }


def timewarp_issue():
    return {
        "kind": "timewarp",
        "occurrence_id": "occ-left",
        "status": "review",
        "selection": "blocked",
        "reason": "effective Source-to-Mix mapping is blocked",
    }


def run_with(*issues):
    return {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "calibration_profile_version": "profile-test",
        "calibration_profile_id": "p" * 64,
        "status": "review_required",
        "legacy_fallback_used": False,
        "plan": {},
        "occurrences": [
            {"occurrence_id": "occ-left"},
            {"occurrence_id": "occ-right"},
        ],
        "transitions": [
            {
                "left_occurrence_id": "occ-left",
                "right_occurrence_id": "occ-right",
                "blocked": True,
            }
        ],
        "issues": list(issues),
    }


class V4ReviewDecisionTests(unittest.TestCase):
    def test_issue_id_is_task_scoped_and_does_not_hash_display_reason(self):
        left = normalize_review_issue(
            transition_issue("first wording"),
            task_fingerprint_sha256=FINGERPRINT,
        )
        right = normalize_review_issue(
            transition_issue("different wording"),
            task_fingerprint_sha256=FINGERPRINT,
        )
        other_task = normalize_review_issue(
            transition_issue("first wording"),
            task_fingerprint_sha256="f" * 64,
        )
        self.assertEqual(left["issue_id"], right["issue_id"])
        self.assertNotEqual(left["issue_id"], other_task["issue_id"])

    def test_transition_resolved_clear_can_make_reviewed_run_renderable(self):
        run = run_with(transition_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        item = template["review_items"][0]
        item["decision"] = {
            "action": "resolved_clear",
            "rationale": "Reviewed both boundary candidates; only one track is audibly active.",
        }
        reviewed = apply_review_template(
            run,
            template,
            base_run_artifact_id=BASE_ARTIFACT,
        )
        self.assertEqual(reviewed["status"], "ready_for_render")
        self.assertEqual(reviewed["issues"], [])
        resolution = reviewed["review_resolution"]
        self.assertEqual(resolution["resolved_issue_count"], 1)
        self.assertEqual(resolution["remaining_issue_count"], 0)
        transition = reviewed["transitions"][0]["review_resolution"]
        self.assertEqual(transition["action"], "resolved_clear")
        self.assertFalse(transition["effective_blocked"])

    def test_confirmed_overlap_remains_blocked_for_recomposition(self):
        run = run_with(transition_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["decision"] = {
            "action": "confirmed_overlap",
            "rationale": "Both vocal streams are audible in the shared boundary window.",
        }
        reviewed = apply_review_template(
            run,
            template,
            base_run_artifact_id=BASE_ARTIFACT,
        )
        self.assertEqual(reviewed["status"], "review_required")
        [issue] = reviewed["issues"]
        self.assertTrue(issue["requires_recomposition"])
        self.assertEqual(issue["status"], "confirmed")
        self.assertEqual(issue["decision_action"], "confirmed_overlap")

    def test_timewarp_cannot_be_cleared_by_transition_style_override(self):
        run = run_with(timewarp_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        item = template["review_items"][0]
        item["decision"] = {
            "action": "resolved_clear",
            "rationale": "Attempt to bypass blocked mapping.",
        }
        with self.assertRaisesRegex(ReviewDecisionError, "not allowed"):
            apply_review_template(
                run,
                template,
                base_run_artifact_id=BASE_ARTIFACT,
            )

    def test_confirmed_timewarp_problem_stays_blocked_for_timeline_rebuild(self):
        run = run_with(timewarp_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["decision"] = {
            "action": "confirmed_requires_rebuild",
            "rationale": "Source-position discontinuity is real and needs a rebuilt mapping/timeline.",
        }
        reviewed = apply_review_template(
            run,
            template,
            base_run_artifact_id=BASE_ARTIFACT,
        )
        self.assertEqual(reviewed["status"], "review_required")
        [issue] = reviewed["issues"]
        self.assertTrue(issue["requires_timeline_rebuild"])
        self.assertEqual(issue["status"], "confirmed")

    def test_decisions_are_bound_to_exact_base_run_artifact(self):
        run = run_with(transition_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        with self.assertRaisesRegex(ReviewDecisionError, "another production run artifact"):
            apply_review_template(
                run,
                template,
                base_run_artifact_id="c" * 64,
            )

    def test_template_snapshot_tamper_is_blocked(self):
        run = run_with(transition_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["issue"]["overlap_candidate_count"] = 999
        with self.assertRaisesRegex(ReviewDecisionError, "snapshot no longer matches"):
            apply_review_template(
                run,
                template,
                base_run_artifact_id=BASE_ARTIFACT,
            )


if __name__ == "__main__":
    unittest.main()
