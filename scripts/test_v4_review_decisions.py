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


def transition_issue(
    reason="adjacent transition has overlap evidence",
    *,
    candidate_id="candidate-one",
    start=9.0,
    end=11.0,
    kind="transition_overlap",
):
    return {
        "kind": kind,
        "code": (
            "cross_track_overlap_candidate"
            if kind == "transition_overlap"
            else "ambiguous_source_occurrence"
        ),
        "candidate_id": candidate_id,
        "left_occurrence_id": "occ-left",
        "right_occurrence_id": "occ-right",
        "interval_start": start,
        "interval_end": end,
        "status": "review",
        "reason": reason,
    }


def timewarp_issue():
    return {
        "kind": "timewarp",
        "code": "effective_mapping_blocked",
        "occurrence_id": "occ-left",
        "status": "review",
        "selection": "blocked",
        "reason": "effective Source-to-Mix mapping is blocked",
    }


def run_with(*issues):
    return {
        "schema_version": "1.1",
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

    def test_two_candidates_on_same_boundary_have_distinct_issue_ids(self):
        first = normalize_review_issue(
            transition_issue(candidate_id="candidate-one", start=9.0, end=10.0),
            task_fingerprint_sha256=FINGERPRINT,
        )
        second = normalize_review_issue(
            transition_issue(candidate_id="candidate-two", start=11.0, end=12.0),
            task_fingerprint_sha256=FINGERPRINT,
        )
        self.assertNotEqual(first["issue_id"], second["issue_id"])

    def test_transition_resolved_clear_can_make_reviewed_run_renderable(self):
        run = run_with(transition_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        item = template["review_items"][0]
        item["decision"] = {
            "action": "resolved_clear",
            "rationale": "Reviewed this exact candidate interval; only one track is active.",
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
        [transition_resolution] = reviewed["transitions"][0]["review_resolutions"]
        self.assertEqual(transition_resolution["action"], "resolved_clear")
        self.assertEqual(transition_resolution["candidate_id"], "candidate-one")
        self.assertFalse(transition_resolution["effective_blocked"])

    def test_clearing_one_candidate_does_not_clear_second_candidate(self):
        run = run_with(
            transition_issue(candidate_id="candidate-one", start=9.0, end=10.0),
            transition_issue(candidate_id="candidate-two", start=11.0, end=12.0),
        )
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["decision"] = {
            "action": "resolved_clear",
            "rationale": "First interval is a false positive.",
        }
        reviewed = apply_review_template(
            run,
            template,
            base_run_artifact_id=BASE_ARTIFACT,
        )
        self.assertEqual(reviewed["status"], "review_required")
        self.assertEqual(len(reviewed["issues"]), 1)
        self.assertEqual(reviewed["issues"][0]["candidate_id"], "candidate-two")

    def test_confirmed_overlap_remains_blocked_for_recomposition(self):
        run = run_with(transition_issue())
        template = build_review_template(run, base_run_artifact_id=BASE_ARTIFACT)
        template["review_items"][0]["decision"] = {
            "action": "confirmed_overlap",
            "rationale": "Both vocal streams are audible in this exact interval.",
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
        self.assertEqual(issue["confirmed_interval"], [9.0, 11.0])

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
        template["review_items"][0]["issue"]["interval_end"] = 99.0
        with self.assertRaisesRegex(ReviewDecisionError, "snapshot no longer matches"):
            apply_review_template(
                run,
                template,
                base_run_artifact_id=BASE_ARTIFACT,
            )


if __name__ == "__main__":
    unittest.main()
