from __future__ import annotations

import copy
import unittest
from pathlib import Path

from lyric_aligner.evaluation.timing_decision_review import (
    RESPONSE_SCHEMA_VERSION,
    build_review_manifest,
    render_review_html,
    validate_review_response,
)
from scripts.v4_build_timing_decision_review import _validate_boundary_promotion_binding_args


def pack() -> dict:
    payload = {
        "schema_version": "timing-decision-pack-1.0",
        "policy_id": "canonical-identity-decision-sensitive-selection-1.0",
        "purpose": "pre_gold_frozen_decision_validation_selection_never_production_authority",
        "gold_read": False,
        "task_fingerprint_sha256": "f" * 64,
        "final_mix_sha256": "a" * 64,
        "old_audit_sha256": "b" * 64,
        "hybrid_audit_sha256": "c" * 64,
        "selection_config": {
            "min_changed_delta_ms": 100,
            "unchanged_control_count": 0,
            "clip_margin_ms": 2500,
            "single_canonical_line_identity_only": True,
        },
        "shared_unique_identity_count": 1,
        "old_ambiguous_identity_count": 0,
        "hybrid_ambiguous_identity_count": 0,
        "changed_case_count": 1,
        "control_case_count": 0,
        "selected_case_count": 1,
        "cases": [
            {
                "id": "d" * 64,
                "track_hash": "e" * 64,
                "target_text": "测试歌词",
                "target_text_sha256": "1" * 64,
                "boundary_kind": "start",
                "old_final_ms": 5000,
                "hybrid_ms": 5300,
                "absolute_change_ms": 300,
                "signed_change_ms": 300,
                "clip_start_ms": 2500,
                "clip_end_ms": 7800,
                "private_identity": {"occurrence_id": "occ", "canonical_line_index": 3},
                "selection_reason": "decision_changed_above_threshold",
            }
        ],
    }
    import hashlib, json

    payload["selection_lock_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


class TimingDecisionReviewTests(unittest.TestCase):
    def test_review_manifest_hides_candidate_positions_and_private_identity(self):
        manifest = build_review_manifest(pack())
        case = manifest["cases"][0]
        self.assertTrue(manifest["candidate_positions_hidden"])
        self.assertNotIn("old_final_ms", case)
        self.assertNotIn("hybrid_ms", case)
        self.assertNotIn("private_identity", case)
        self.assertEqual(case["target_text"], "测试歌词")
        self.assertEqual(case["clip_duration_ms"], 5300)

    def test_html_does_not_embed_candidate_position_field_names(self):
        html = render_review_html(build_review_manifest(pack()))
        self.assertIn("页面不显示 old/hybrid 候选位置", html)
        self.assertNotIn("old_final_ms", html)
        self.assertNotIn("hybrid_ms", html)
        self.assertIn("timing-decision-review-response-1.0", html)

    def test_complete_response_materializes_absolute_gold_bound_to_lock(self):
        manifest = build_review_manifest(pack())
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [
                {"id": manifest["cases"][0]["id"], "relative_ms": 2700, "uncertainty_ms": 40}
            ],
        }
        gold = validate_review_response(manifest, response, partition="blind")
        self.assertEqual(gold["selection_lock_sha256"], pack()["selection_lock_sha256"])
        self.assertEqual(gold["records"][0]["gold_ms"], 5200)
        self.assertEqual(gold["records"][0]["uncertainty_ms"], 40)

    def test_optional_boundary_promotion_binding_is_hash_and_partition_bound_into_gold(self):
        manifest = build_review_manifest(
            pack(),
            boundary_promotion_selection_sha256="9" * 64,
            boundary_promotion_partition="blind",
        )
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [
                {"id": manifest["cases"][0]["id"], "relative_ms": 2700, "uncertainty_ms": 40}
            ],
        }
        gold = validate_review_response(manifest, response, partition="blind")
        self.assertEqual(manifest["boundary_promotion_selection_sha256"], "9" * 64)
        self.assertEqual(manifest["boundary_promotion_partition"], "blind")
        self.assertEqual(gold["boundary_promotion_selection_sha256"], "9" * 64)
        self.assertEqual(gold["boundary_promotion_partition"], "blind")
        self.assertEqual(gold["review_manifest_sha256"], manifest["manifest_sha256"])

    def test_boundary_promotion_selection_and_partition_must_be_frozen_together(self):
        with self.assertRaisesRegex(ValueError, "must be provided together"):
            build_review_manifest(
                pack(),
                boundary_promotion_selection_sha256="9" * 64,
            )
        with self.assertRaisesRegex(ValueError, "must be provided together"):
            build_review_manifest(
                pack(),
                boundary_promotion_partition="blind",
            )

    def test_cli_boundary_promotion_selection_and_partition_must_be_paired(self):
        _validate_boundary_promotion_binding_args(None, None)
        _validate_boundary_promotion_binding_args(Path("selection.json"), "blind")
        with self.assertRaisesRegex(ValueError, "must be provided together"):
            _validate_boundary_promotion_binding_args(Path("selection.json"), None)
        with self.assertRaisesRegex(ValueError, "must be provided together"):
            _validate_boundary_promotion_binding_args(None, "blind")

    def test_boundary_promotion_partition_cannot_be_relabelled_after_review(self):
        manifest = build_review_manifest(
            pack(),
            boundary_promotion_selection_sha256="9" * 64,
            boundary_promotion_partition="development",
        )
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [
                {"id": manifest["cases"][0]["id"], "relative_ms": 2700, "uncertainty_ms": 40}
            ],
        }
        gold = validate_review_response(manifest, response, partition="development")
        self.assertEqual(gold["partition"], "development")
        with self.assertRaisesRegex(ValueError, "differs from frozen"):
            validate_review_response(manifest, response, partition="blind")

    def test_invalid_case_is_explicitly_retained_in_gold_denominator(self):
        manifest = build_review_manifest(pack())
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [
                {
                    "id": manifest["cases"][0]["id"],
                    "status": "invalid",
                    "invalid_reason": "repeated lyric cannot be disambiguated in clip",
                }
            ],
        }
        gold = validate_review_response(manifest, response, partition="blind")
        self.assertEqual(gold["population_count"], 1)
        self.assertEqual(gold["valid_count"], 0)
        self.assertEqual(gold["invalid_count"], 1)
        self.assertEqual(gold["records"], [])
        self.assertEqual(gold["invalid_records"][0]["id"], manifest["cases"][0]["id"])

    def test_incomplete_response_fails_closed(self):
        manifest = build_review_manifest(pack())
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [],
        }
        with self.assertRaisesRegex(ValueError, "incomplete"):
            validate_review_response(manifest, response, partition="blind")

    def test_response_outside_clip_fails_closed(self):
        manifest = build_review_manifest(pack())
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [
                {"id": manifest["cases"][0]["id"], "relative_ms": 9999, "uncertainty_ms": 0}
            ],
        }
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_review_response(manifest, response, partition="blind")

    def test_manifest_tamper_fails_even_if_response_uses_old_hash(self):
        manifest = build_review_manifest(pack())
        response = {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "manifest_sha256": manifest["manifest_sha256"],
            "records": [
                {"id": manifest["cases"][0]["id"], "relative_ms": 100, "uncertainty_ms": 0}
            ],
        }
        tampered = copy.deepcopy(manifest)
        tampered["cases"][0]["target_text"] = "被篡改"
        with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
            validate_review_response(tampered, response, partition="blind")


if __name__ == "__main__":
    unittest.main()
