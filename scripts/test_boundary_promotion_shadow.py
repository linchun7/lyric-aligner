from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.boundary_promotion_shadow import (
    DECISIONS_SCHEMA_VERSION,
    BoundaryPromotionShadowError,
    evaluate_boundary_promotion_shadow,
    freeze_boundary_promotion_selection,
    verify_boundary_promotion_selection,
)
from lyric_aligner.evaluation.timing_decision_pack import (
    POLICY_ID as TIMING_PACK_POLICY_ID,
    SCHEMA_VERSION as TIMING_PACK_SCHEMA_VERSION,
)
from lyric_aligner.evaluation.timing_decision_review import (
    RESPONSE_SCHEMA_VERSION,
    build_review_manifest,
    validate_review_response,
)
from scripts.v4_boundary_promotion_shadow import _write as write_shadow_artifact


def stable_sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def make_pack(*, candidate_delta_ms: int = 200) -> dict:
    cases = []
    for index in range(32):
        track = f"track-{index % 4}"
        smart = 10_000 + index * 1_000
        promoted = index < 8
        candidate = smart + candidate_delta_ms if promoted else smart
        cases.append(
            {
                "id": f"case-{index:02d}",
                "track_hash": hashlib.sha256(track.encode()).hexdigest(),
                "target_text": f"line {index}",
                "target_text_sha256": hashlib.sha256(f"line {index}".encode()).hexdigest(),
                "boundary_kind": "start",
                "old_final_ms": smart,
                "hybrid_ms": candidate,
                "absolute_change_ms": abs(candidate - smart),
                "signed_change_ms": candidate - smart,
                "clip_start_ms": max(0, smart - 1000),
                "clip_end_ms": candidate + 1000,
                "selection_reason": (
                    "decision_changed_above_threshold" if promoted else "deterministic_unchanged_control"
                ),
                "private_identity": {"occurrence_id": track, "canonical_line_index": index},
            }
        )
    pack = {
        "schema_version": TIMING_PACK_SCHEMA_VERSION,
        "policy_id": TIMING_PACK_POLICY_ID,
        "purpose": "pre_gold_frozen_decision_validation_selection_never_production_authority",
        "gold_read": False,
        "task_fingerprint_sha256": "a" * 64,
        "final_mix_sha256": "b" * 64,
        "old_audit_sha256": "c" * 64,
        "hybrid_audit_sha256": "d" * 64,
        "selection_config": {
            "min_changed_delta_ms": 100,
            "unchanged_control_count": 24,
            "clip_margin_ms": 1000,
            "single_canonical_line_identity_only": True,
        },
        "shared_unique_identity_count": 32,
        "old_ambiguous_identity_count": 0,
        "hybrid_ambiguous_identity_count": 0,
        "changed_case_count": 8,
        "control_case_count": 24,
        "selected_case_count": 32,
        "cases": cases,
    }
    pack["selection_lock_sha256"] = stable_sha(pack)
    return pack


def make_decisions(pack: dict) -> dict:
    decisions = []
    for case in pack["cases"]:
        promote = case["hybrid_ms"] != case["old_final_ms"]
        row = {
            "id": case["id"],
            "choice": "promote_candidate" if promote else "keep_smart",
            "expected_smart_ms": case["old_final_ms"],
            "expected_candidate_ms": case["hybrid_ms"],
        }
        if promote:
            row.update(
                independent_timing_evidence=True,
                candidate_family="max_candidate",
                candidate_correlation_group="max-source-projection",
                independent_evidence_family="independent_final_mix_observer",
                independent_evidence_correlation_group="final-mix-human-independent",
                independent_evidence_sha256="e" * 64,
                reason_code="independent_boundary_support",
            )
        decisions.append(row)
    return {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "selection_lock_sha256": pack["selection_lock_sha256"],
        "gold_read": False,
        "selector_id": "selector-v1",
        "selector_revision": "r1",
        "selector_code_sha256": "f" * 64,
        "decisions": decisions,
    }


def make_review_manifest(pack: dict, selection: dict, *, partition: str = "blind") -> dict:
    return build_review_manifest(
        pack,
        boundary_promotion_selection_sha256=selection["selection_payload_sha256"],
        boundary_promotion_partition=partition,
    )


def make_review_truth(
    pack: dict,
    review_manifest: dict,
    *,
    partition: str | None = None,
    candidate_wins: bool = True,
    gold_overrides: dict[str, int] | None = None,
) -> tuple[dict, dict]:
    case_by_id = {case["id"]: case for case in pack["cases"]}
    manifest_by_id = {case["id"]: case for case in review_manifest["cases"]}
    response_records = []
    for case_id, case in case_by_id.items():
        changed = case["hybrid_ms"] != case["old_final_ms"]
        if gold_overrides and case_id in gold_overrides:
            gold_ms = gold_overrides[case_id]
        elif changed and candidate_wins:
            gold_ms = case["hybrid_ms"]
        else:
            gold_ms = case["old_final_ms"]
        manifest_case = manifest_by_id[case_id]
        response_records.append(
            {
                "id": case_id,
                "status": "valid",
                "relative_ms": gold_ms - int(manifest_case["clip_start_ms"]),
                "uncertainty_ms": 0,
            }
        )
    response = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "manifest_sha256": review_manifest["manifest_sha256"],
        "records": response_records,
    }
    if partition is None:
        partition = str(review_manifest.get("boundary_promotion_partition") or "blind")
    gold = validate_review_response(review_manifest, response, partition=partition)
    return response, gold


class BoundaryPromotionShadowTests(unittest.TestCase):
    def test_cli_writer_uses_safe_new_output_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.json"
            output = root / "out.json"
            source.write_text("{}\n", encoding="utf-8")
            write_shadow_artifact(output, {"ok": True}, inputs={"input": source})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"ok": True})
            with self.assertRaisesRegex(ValueError, "already exists"):
                write_shadow_artifact(output, {"ok": False}, inputs={"input": source})

    def test_cli_writer_rejects_output_input_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.json"
            source.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "collides with input"):
                write_shadow_artifact(source, {"ok": True}, inputs={"input": source})

    def test_freeze_selection_is_complete_pre_gold_and_shadow_only(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        self.assertFalse(selection["gold_read"])
        self.assertFalse(selection["production_authority_granted"])
        self.assertEqual(selection["case_count"], 32)
        self.assertEqual(selection["promoted_case_count"], 8)
        self.assertEqual(len(selection["decisions"]), 32)
        self.assertEqual(verify_boundary_promotion_selection(pack, selection), selection["selection_payload_sha256"])

    def test_incomplete_decisions_fail_closed(self):
        pack = make_pack()
        decisions = make_decisions(pack)
        decisions["decisions"].pop()
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "account for every frozen case"):
            freeze_boundary_promotion_selection(pack, decisions)

    def test_stale_smart_boundary_fails_closed(self):
        pack = make_pack()
        decisions = make_decisions(pack)
        decisions["decisions"][0]["expected_smart_ms"] += 1
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "Smart boundary is stale"):
            freeze_boundary_promotion_selection(pack, decisions)

    def test_promotion_requires_independent_uncorrelated_evidence(self):
        pack = make_pack()
        decisions = make_decisions(pack)
        decisions["decisions"][0]["independent_evidence_correlation_group"] = decisions["decisions"][0][
            "candidate_correlation_group"
        ]
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "correlated"):
            freeze_boundary_promotion_selection(pack, decisions)

        decisions = make_decisions(pack)
        decisions["decisions"][0]["independent_timing_evidence"] = False
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "lacks independent timing evidence"):
            freeze_boundary_promotion_selection(pack, decisions)

    def test_tampered_selection_hash_fails_closed(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        selection["decisions"][0]["reason_code"] = "tampered"
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "payload hash mismatch"):
            verify_boundary_promotion_selection(pack, selection)

    def test_rehashed_metadata_drift_still_fails_closed(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        selection["case_count"] = 31
        bare = dict(selection)
        bare.pop("selection_payload_sha256", None)
        selection["selection_payload_sha256"] = stable_sha(bare)
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "case_count mismatch"):
            verify_boundary_promotion_selection(pack, selection)

    def test_rehashed_identity_and_promoted_count_drift_still_fails_closed(self):
        pack = make_pack()
        drift_cases = (
            ("selector_id", "", "missing selector identity"),
            ("selector_revision", "", "missing selector identity"),
            ("task_fingerprint_sha256", "0" * 64, "task fingerprint mismatch"),
            ("final_mix_sha256", "1" * 64, "final-mix identity mismatch"),
            ("promoted_case_count", 7, "promoted_case_count mismatch"),
        )
        for field, value, message in drift_cases:
            with self.subTest(field=field):
                selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
                selection[field] = value
                bare = dict(selection)
                bare.pop("selection_payload_sha256", None)
                selection["selection_payload_sha256"] = stable_sha(bare)
                with self.assertRaisesRegex(BoundaryPromotionShadowError, message):
                    verify_boundary_promotion_selection(pack, selection)

    def test_rehashed_promotion_cannot_drop_evidence_lineage(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        promoted = next(row for row in selection["decisions"] if row["choice"] == "promote_candidate")
        promoted["reason_code"] = ""
        bare = dict(selection)
        bare.pop("selection_payload_sha256", None)
        selection["selection_payload_sha256"] = stable_sha(bare)
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "lost evidence lineage fields"):
            verify_boundary_promotion_selection(pack, selection)

    def test_rehashed_selection_cannot_promote_unchanged_control(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        control = next(
            row
            for row in selection["decisions"]
            if row["expected_candidate_ms"] == row["expected_smart_ms"]
        )
        control.update(
            choice="promote_candidate",
            independent_timing_evidence=True,
            candidate_family="max_candidate",
            candidate_correlation_group="max-source-projection",
            independent_evidence_family="independent_final_mix_observer",
            independent_evidence_correlation_group="final-mix-human-independent",
            independent_evidence_sha256="e" * 64,
            reason_code="tampered_control_promotion",
        )
        selection["promoted_case_count"] += 1
        bare = dict(selection)
        bare.pop("selection_payload_sha256", None)
        selection["selection_payload_sha256"] = stable_sha(bare)
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "unchanged candidate"):
            verify_boundary_promotion_selection(pack, selection)

    def test_gold_cannot_be_reused_with_post_gold_reselected_selector(self):
        pack = make_pack()
        frozen_selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, frozen_selection)
        review_response, gold = make_review_truth(pack, review_manifest)

        reselection_decisions = make_decisions(pack)
        reselection_decisions["selector_revision"] = "post-gold-r2"
        reselection_decisions["decisions"][0]["choice"] = "keep_smart"
        reselection = freeze_boundary_promotion_selection(pack, reselection_decisions)
        self.assertNotEqual(
            reselection["selection_payload_sha256"],
            frozen_selection["selection_payload_sha256"],
        )
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "not bound to the frozen promotion selection"):
            evaluate_boundary_promotion_shadow(
                pack,
                reselection,
                gold,
                review_manifest=review_manifest,
                review_response=review_response,
            )

    def test_rehashed_review_manifest_cannot_leak_candidate_position(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection)
        review_response, gold = make_review_truth(pack, review_manifest)
        tampered_manifest = json.loads(json.dumps(review_manifest))
        tampered_manifest["cases"][0]["old_final_ms"] = pack["cases"][0]["old_final_ms"]
        bare = dict(tampered_manifest)
        bare.pop("manifest_sha256", None)
        tampered_manifest["manifest_sha256"] = stable_sha(bare)
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "leaks candidate/private fields"):
            evaluate_boundary_promotion_shadow(
                pack,
                selection,
                gold,
                review_manifest=tampered_manifest,
                review_response=review_response,
            )

    def test_rehashed_review_manifest_cannot_change_candidate_blind_instructions(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection)
        review_response, gold = make_review_truth(pack, review_manifest)
        tampered_manifest = json.loads(json.dumps(review_manifest))
        tampered_manifest["cases"][0]["instructions"] = "Candidate boundary is at 123 ms"
        bare = dict(tampered_manifest)
        bare.pop("manifest_sha256", None)
        tampered_manifest["manifest_sha256"] = stable_sha(bare)
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "deterministic candidate-blind material"):
            evaluate_boundary_promotion_shadow(
                pack,
                selection,
                gold,
                review_manifest=tampered_manifest,
                review_response=review_response,
            )

    def test_unbound_review_manifest_cannot_be_used_for_p1(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = build_review_manifest(pack)
        review_response, gold = make_review_truth(pack, review_manifest)
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "not bound to the frozen promotion selection"):
            evaluate_boundary_promotion_shadow(
                pack,
                selection,
                gold,
                review_manifest=review_manifest,
                review_response=review_response,
            )

    def test_gold_boundary_tamper_fails_against_candidate_blind_response(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection)
        review_response, gold = make_review_truth(pack, review_manifest)
        gold["records"][0]["gold_ms"] += 1
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "does not match candidate-blind review response"):
            evaluate_boundary_promotion_shadow(
                pack,
                selection,
                gold,
                review_manifest=review_manifest,
                review_response=review_response,
            )

    def test_all_keep_smart_cannot_pass_by_having_zero_harm(self):
        pack = make_pack()
        decisions = make_decisions(pack)
        # Rebuild a complete KEEP ledger while retaining the frozen candidate values.
        decisions["decisions"] = [
            {
                "id": case["id"],
                "choice": "keep_smart",
                "expected_smart_ms": case["old_final_ms"],
                "expected_candidate_ms": case["hybrid_ms"],
            }
            for case in pack["cases"]
        ]
        selection = freeze_boundary_promotion_selection(pack, decisions)
        review_manifest = make_review_manifest(pack, selection)
        review_response, gold = make_review_truth(pack, review_manifest, partition="blind")
        result = evaluate_boundary_promotion_shadow(
            pack,
            selection,
            gold,
            review_manifest=review_manifest,
            review_response=review_response,
        )
        self.assertFalse(result["shadow_gate_passed"])
        self.assertEqual(result["promoted_scored_count"], 0)
        gate = next(row for row in result["gate_results"] if row["gate"] == "min_promoted_count")
        self.assertFalse(gate["passed"])
        self.assertFalse(result["production_authority_granted"])

    def test_blind_shadow_gate_can_pass_but_never_grants_production_authority(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection)
        review_response, gold = make_review_truth(pack, review_manifest, partition="blind")
        result = evaluate_boundary_promotion_shadow(
            pack,
            selection,
            gold,
            review_manifest=review_manifest,
            review_response=review_response,
        )
        self.assertTrue(result["shadow_gate_passed"])
        self.assertFalse(result["production_authority_granted"])
        self.assertFalse(result["production_writeback_permitted"])
        self.assertEqual(result["valid_truth_count"], 32)
        self.assertEqual(result["promoted_scored_count"], 8)
        self.assertEqual(result["promoted_distinct_track_count"], 4)
        self.assertGreater(result["changed_mean_gain_ms"], 0)
        self.assertEqual(result["new_over_500ms_error_count"], 0)

    def test_development_truth_is_diagnostic_only(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection, partition="development")
        review_response, gold = make_review_truth(pack, review_manifest)
        result = evaluate_boundary_promotion_shadow(
            pack,
            selection,
            gold,
            review_manifest=review_manifest,
            review_response=review_response,
        )
        self.assertFalse(result["shadow_gate_passed"])
        gate = next(row for row in result["gate_results"] if row["gate"] == "eligible_partition")
        self.assertFalse(gate["passed"])
        self.assertFalse(result["production_authority_granted"])

    def test_development_review_cannot_be_relabelled_blind_for_p1(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection, partition="development")
        review_response, gold = make_review_truth(pack, review_manifest)
        relabelled_gold = json.loads(json.dumps(gold))
        relabelled_gold["partition"] = "blind"
        relabelled_gold["boundary_promotion_partition"] = "blind"
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "differs from frozen review partition"):
            evaluate_boundary_promotion_shadow(
                pack,
                selection,
                relabelled_gold,
                review_manifest=review_manifest,
                review_response=review_response,
            )

    def test_catastrophic_new_error_blocks_shadow_gate(self):
        pack = make_pack(candidate_delta_ms=700)
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection)
        first_case = pack["cases"][0]
        # Freeze truth so seven promoted boundaries are correct but one candidate creates
        # a new >500 ms error while Smart is exact.
        review_response, gold = make_review_truth(
            pack,
            review_manifest,
            partition="blind",
            candidate_wins=True,
            gold_overrides={first_case["id"]: first_case["old_final_ms"]},
        )
        result = evaluate_boundary_promotion_shadow(
            pack,
            selection,
            gold,
            review_manifest=review_manifest,
            review_response=review_response,
        )
        self.assertFalse(result["shadow_gate_passed"])
        self.assertEqual(result["new_over_500ms_error_count"], 1)
        self.assertFalse(result["production_authority_granted"])

    def test_gold_from_another_selection_lock_fails_closed(self):
        pack = make_pack()
        selection = freeze_boundary_promotion_selection(pack, make_decisions(pack))
        review_manifest = make_review_manifest(pack, selection)
        review_response, gold = make_review_truth(pack, review_manifest)
        gold["selection_lock_sha256"] = "0" * 64
        with self.assertRaisesRegex(BoundaryPromotionShadowError, "does not match candidate-blind review response"):
            evaluate_boundary_promotion_shadow(
                pack,
                selection,
                gold,
                review_manifest=review_manifest,
                review_response=review_response,
            )


if __name__ == "__main__":
    unittest.main()
