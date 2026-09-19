import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.evaluation.human_boundary_anchor import (
    ANCHOR_TOTAL_BOUNDARY_POINTS,
    HUMAN_ANCHOR_AUDIT_UI_REVISION,
    HUMAN_ANCHOR_AUDIT_UX_REVISION,
    HUMAN_ANCHOR_GOLD_AUTHORITY,
    HUMAN_ANCHOR_GOLD_LEGACY_SCHEMA_VERSION,
    HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
    HUMAN_ANCHOR_PRODUCTION_POLICY,
    HUMAN_ANCHOR_REPLACEMENT_SELECTION_POLICY_ID,
    HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION,
    HUMAN_ANCHOR_SELECTION_POLICY_ID,
    HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
    clarity_to_uncertainty_ms,
    project_locked_anchor_selection,
)
from lyric_aligner.evaluation.production_calibration import (
    evaluate_human_gold_backend_calibration,
    production_calibration_artifact_is_authoritative,
    validate_human_boundary_gold_artifact,
)
from scripts.v4_build_human_boundary_anchor_pack import (
    ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
    ANCHOR_UI_REVISION,
    ANCHOR_UI_UX_REVISION,
    _recheck_membership,
)
from scripts.v4_ingest_human_boundary_anchor import _internal_records, _verify_recheck_complete
from scripts.v4_human_boundary_anchor_audit_ui import (
    AUDIT_UX_REVISION,
    HTML,
    AnchorAuditApp,
    _bind_server,
)


def _sha_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _case_id(label, index):
    return hashlib.sha256(f"{label}-{index}".encode()).hexdigest()


def _locked_record(label, index, *, segment_count):
    segments = [
        {"lrc_index": j, "text": f"{label} {index} segment {j}"}
        for j in range(segment_count)
    ]
    return {
        "case_id": _case_id(label, index),
        "partition": "calibration" if index % 3 else "holdout",
        "track": f"track-{index:02d}",
        "cue_number": index + 1,
        "start_ms": 10000 + index * 1000,
        "end_ms": 10800 + index * 1000,
        "canonical_text": " ".join(row["text"] for row in segments),
        "segment_count": segment_count,
        "segments": segments,
        "internal_boundary_index": 1 if segment_count >= 2 else None,
        "boundary_kinds": ["internal"] if label == "internal" else ["start", "end"],
        "clip_relpath": f"{label}/clips/{index:02d}.wav",
        "clip_start_ms": 9000 + index * 1000,
        "clip_end_ms": 11800 + index * 1000,
        "clip_sha256": hashlib.sha256(f"clip-{label}-{index}".encode()).hexdigest(),
    }


def full_lock_fixture():
    outer = []
    for index in range(20):
        outer.append(_locked_record("outer", index, segment_count=1))
    for index in range(20, 30):
        outer.append(_locked_record("outer", index, segment_count=2))
    internal = [_locked_record("internal", index, segment_count=2) for index in range(30)]
    return {
        "lock_sha256": "a" * 64,
        "populations": {
            "outer": {"purpose": "outer", "records": outer},
            "internal": {"purpose": "internal", "records": internal},
        },
    }


def anchor_gold_fixture():
    records = []
    for index in range(12):
        partition = "calibration" if index < 8 else "holdout"
        track = f"track-{index % 4}"
        case_id = _case_id("gold-outer", index)
        for kind, offset in (("start", 0), ("end", 700)):
            records.append(
                {
                    "id": f"{case_id}:{kind}",
                    "kind": "boundary",
                    "boundary_kind": kind,
                    "gold_ms": 10000 + index * 1000 + offset,
                    "gold_uncertainty_ms": 50,
                    "gold_clarity": "clear",
                    "partition": partition,
                    "track": track,
                    "case_id": case_id,
                    "cue_number": index + 1,
                    "population": "outer",
                }
            )
    for index in range(12):
        partition = "calibration" if index < 8 else "holdout"
        track = f"track-{index % 4}"
        case_id = _case_id("gold-internal", index)
        records.append(
            {
                "id": f"{case_id}:internal",
                "kind": "boundary",
                "boundary_kind": "internal",
                "gold_ms": 50000 + index * 1000,
                "gold_uncertainty_ms": 50,
                "gold_clarity": "clear",
                "partition": partition,
                "track": track,
                "case_id": case_id,
                "cue_number": index + 101,
                "population": "internal",
                "internal_boundary_index": 1,
            }
        )
    artifact = {
        "schema_version": HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
        "authority": HUMAN_ANCHOR_GOLD_AUTHORITY,
        "language_scope": "zh",
        "selection_lock_sha256": "1" * 64,
        "selection_lock_file_sha256": "2" * 64,
        "source_full_selection_lock_sha256": "6" * 64,
        "outer_audit_csv_sha256": "3" * 64,
        "internal_audit_csv_sha256": "4" * 64,
        "audit_confirmation": {
            "recheck_file_sha256": "7" * 64,
            "recheck_schema_version": ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
            "required_ui_revision": HUMAN_ANCHOR_AUDIT_UI_REVISION,
            "required_ui_ux_revision": HUMAN_ANCHOR_AUDIT_UX_REVISION,
            "last_confirmed_ui_revision": HUMAN_ANCHOR_AUDIT_UI_REVISION,
            "last_confirmed_ui_ux_revision": HUMAN_ANCHOR_AUDIT_UX_REVISION,
            "pending_case_count": 0,
            "confirmed_case_count": 24,
        },
        "final_audio_sha256": "5" * 64,
        "uncertainty_policy_id": HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
        "production_policy": dict(HUMAN_ANCHOR_PRODUCTION_POLICY.__dict__),
        "record_count": ANCHOR_TOTAL_BOUNDARY_POINTS,
        "boundary_kind_counts": {"start": 12, "end": 12, "internal": 12},
        "population_counts": {"outer": 24, "internal": 12},
        "records": records,
    }
    artifact["gold_sha256"] = _sha_json(records)
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


class HumanBoundaryAnchorTests(unittest.TestCase):
    def test_projection_is_24_clips_and_preserves_first_seven_outer_rows(self):
        full = full_lock_fixture()
        selection = project_locked_anchor_selection(full)
        outer = selection["populations"]["outer"]
        internal = selection["populations"]["internal"]

        self.assertEqual(selection["policy_id"], HUMAN_ANCHOR_SELECTION_POLICY_ID)
        self.assertEqual(len(outer), 12)
        self.assertEqual(len(internal), 12)
        self.assertEqual(sum(row["segment_count"] == 1 for row in outer), 8)
        self.assertEqual(sum(row["segment_count"] >= 2 for row in outer), 4)
        self.assertEqual(
            [row["case_id"] for row in outer[:7]],
            [row["case_id"] for row in full["populations"]["outer"]["records"][:7]],
        )
        self.assertEqual(sum(row["partition"] == "calibration" for row in outer), 8)
        self.assertEqual(sum(row["partition"] == "holdout" for row in outer), 4)
        self.assertEqual(sum(row["partition"] == "calibration" for row in internal), 8)
        self.assertEqual(sum(row["partition"] == "holdout" for row in internal), 4)

    def test_projection_is_deterministic_and_does_not_need_audit_or_model_outcomes(self):
        full = full_lock_fixture()
        first = project_locked_anchor_selection(full)
        second = project_locked_anchor_selection(json.loads(json.dumps(full)))
        self.assertEqual(first, second)
        forbidden = {
            "backend_id",
            "backend_version",
            "backend_profile_id",
            "model_id",
            "model_revision",
            "predicted_ms",
            "prediction_ms",
            "gold_ms",
            "gold_start_clip_ms",
            "gold_end_clip_ms",
            "gold_internal_clip_ms",
        }
        for purpose in ("outer", "internal"):
            for row in first["populations"][purpose]:
                self.assertFalse(forbidden.intersection(row))

    def test_question_invalid_case_is_replaced_from_locked_order_without_outcomes(self):
        full = full_lock_fixture()
        baseline = project_locked_anchor_selection(full)
        invalid_id = baseline["populations"]["internal"][0]["case_id"]
        replacement = project_locked_anchor_selection(
            full,
            question_invalid_case_ids=[invalid_id],
        )
        replacement_again = project_locked_anchor_selection(
            json.loads(json.dumps(full)),
            question_invalid_case_ids=[invalid_id],
        )

        self.assertEqual(replacement, replacement_again)
        self.assertEqual(
            replacement["schema_version"],
            HUMAN_ANCHOR_REPLACEMENT_SELECTION_SCHEMA_VERSION,
        )
        self.assertEqual(
            replacement["policy_id"],
            HUMAN_ANCHOR_REPLACEMENT_SELECTION_POLICY_ID,
        )
        internal_ids = {
            row["case_id"] for row in replacement["populations"]["internal"]
        }
        self.assertNotIn(invalid_id, internal_ids)
        self.assertEqual(len(internal_ids), 12)
        self.assertEqual(replacement["question_invalid_case_ids"], [invalid_id])
        with self.assertRaisesRegex(ValueError, "absent from source lock"):
            project_locked_anchor_selection(
                full,
                question_invalid_case_ids=["f" * 64],
            )

    def test_carry_forward_confirmation_supersedes_legacy_pending_membership(self):
        retained = _case_id("retained", 1)
        replacement = _case_id("replacement", 1)
        confirmed, pending = _recheck_membership(
            selected_ids={retained, replacement},
            prefilled_ids={retained, replacement},
            source_pending={retained},
            source_confirmed=set(),
            carry_confirmed={retained},
            replacement_ids={replacement},
        )
        self.assertEqual(confirmed, [retained])
        self.assertEqual(pending, [replacement])

    def test_clarity_mapping_removes_human_numeric_uncertainty_estimation(self):
        self.assertEqual(clarity_to_uncertainty_ms("clear"), 50)
        self.assertEqual(clarity_to_uncertainty_ms("ambiguous"), 100)
        with self.assertRaises(ValueError):
            clarity_to_uncertainty_ms("67")

    def test_recheck_sentinel_requires_explicit_ux32_confirmation_contract(self):
        selected = {_case_id("selected-ux", 0)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json"
            sentinel.write_text(
                json.dumps(
                    {
                        "schema_version": ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
                        "required_ui_revision": ANCHOR_UI_REVISION,
                        "pending_case_ids": [],
                        "confirmed_case_ids": sorted(selected),
                        "last_confirmed_ui_revision": ANCHOR_UI_REVISION,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "requires another UI UX revision"):
                _verify_recheck_complete(root, selected)

    def test_recheck_sentinel_requires_every_selected_case_to_be_human_confirmed(self):
        selected = {_case_id("selected", index) for index in range(3)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "HUMAN_ANCHOR_AUDIT_RECHECK_REQUIRED.json"
            sentinel.write_text(
                json.dumps(
                    {
                        "schema_version": ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
                        "required_ui_revision": ANCHOR_UI_REVISION,
                        "required_ui_ux_revision": ANCHOR_UI_UX_REVISION,
                        "pending_case_ids": [],
                        "confirmed_case_ids": sorted(selected)[:2],
                        "last_confirmed_ui_revision": ANCHOR_UI_REVISION,
                        "last_confirmed_ui_ux_revision": ANCHOR_UI_UX_REVISION,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "lacks UI human-confirmed records for 1 selected cases"):
                _verify_recheck_complete(root, selected)

            sentinel.write_text(
                json.dumps(
                    {
                        "schema_version": ANCHOR_AUDIT_RECHECK_SCHEMA_VERSION,
                        "required_ui_revision": ANCHOR_UI_REVISION,
                        "required_ui_ux_revision": ANCHOR_UI_UX_REVISION,
                        "pending_case_ids": [],
                        "confirmed_case_ids": sorted(selected),
                        "last_confirmed_ui_revision": ANCHOR_UI_REVISION,
                        "last_confirmed_ui_ux_revision": ANCHOR_UI_UX_REVISION,
                    }
                ),
                encoding="utf-8",
            )
            verified = _verify_recheck_complete(root, selected)
            self.assertEqual(set(verified["confirmed_case_ids"]), selected)

    def test_anchor_gold_is_valid_production_authority_input(self):
        gold = anchor_gold_fixture()
        verified = validate_human_boundary_gold_artifact(gold)
        self.assertEqual(verified["expected_count_per_kind"], 12)
        self.assertEqual(verified["production_policy"], HUMAN_ANCHOR_PRODUCTION_POLICY)

        scope = {
            "boundary_kind": "start",
            "audio_basis": "final_mix",
            "language": "zh",
            "backend_version": "fixture-v1",
            "backend_profile_id": "fixture-profile-v1",
            "window_policy_id": "fixture-window-v1",
            "model_id": "fixture-model",
            "model_revision": "fixture-revision",
            "implementation_revision": "a" * 64,
            "adapter_contract_revision": "b" * 64,
        }
        scoped = [row for row in gold["records"] if row["boundary_kind"] == "start"]
        calibration = evaluate_human_gold_backend_calibration(
            backend_id="anchor-backend",
            family="final_mix_forced_alignment",
            correlation_group="anchor-group",
            scope=scope,
            human_gold_artifact=gold,
            predictions_ms={row["id"]: row["gold_ms"] for row in scoped},
        )
        self.assertTrue(calibration["passed"])
        self.assertTrue(
            production_calibration_artifact_is_authoritative(
                calibration,
                backend_id="anchor-backend",
                family="final_mix_forced_alignment",
                correlation_group="anchor-group",
                expected_scope=scope,
            )
        )

    def test_ui_candidate_uses_editor_prior_when_machine_aligners_disagree(self):
        app = object.__new__(AnchorAuditApp)
        case_id = "c" * 64
        app.consensus = {
            f"{case_id}:start": {
                "candidate_ms": 11500,
                "consensus_status": "disagreement",
                "direct_prediction_count": 2,
            }
        }
        row = {
            "case_id": case_id,
            "clip_start_ms": "9000",
            "clip_end_ms": "13000",
            "editor_start_ms_reference_only": "10000",
            "editor_end_ms_reference_only": "12000",
            "gold_start_clip_ms": "",
        }
        candidate, source, machine = app._candidate("outer", row, "start")
        self.assertEqual(candidate, 1000)
        self.assertEqual(source, "editor_prior_machine_disagreement")
        self.assertIsNotNone(machine)

    def test_ui_internal_nonsemantic_machine_only_falls_back_to_editor_midpoint(self):
        app = object.__new__(AnchorAuditApp)
        case_id = "d" * 64
        app.consensus = {
            f"{case_id}:internal": {
                "candidate_ms": 11600,
                "consensus_status": "acoustic_only",
                "direct_prediction_count": 0,
            }
        }
        row = {
            "case_id": case_id,
            "clip_start_ms": "9000",
            "clip_end_ms": "13000",
            "editor_start_ms_reference_only": "10000",
            "editor_end_ms_reference_only": "12000",
            "gold_internal_clip_ms": "",
        }
        candidate, source, machine = app._candidate("internal", row, "internal")
        self.assertEqual(candidate, 2000)
        self.assertEqual(source, "editor_midpoint_no_semantic_machine_boundary")
        self.assertIsNotNone(machine)

    def test_ui_confirmed_human_mark_still_precedes_machine_candidate(self):
        app = object.__new__(AnchorAuditApp)
        case_id = "e" * 64
        app.consensus = {
            f"{case_id}:start": {
                "candidate_ms": 11600,
                "consensus_status": "high_consensus",
                "direct_prediction_count": 2,
            }
        }
        row = {
            "case_id": case_id,
            "clip_start_ms": "9000",
            "clip_end_ms": "13000",
            "editor_start_ms_reference_only": "10000",
            "editor_end_ms_reference_only": "12000",
            "gold_start_clip_ms": "1250",
        }
        candidate, source, machine = app._candidate(
            "outer", row, "start", prefer_stored_human=True
        )
        self.assertEqual(candidate, 1250)
        self.assertEqual(source, "confirmed_human_mark")
        self.assertIsNone(machine)

    def test_internal_anchor_gold_may_leave_editor_reference_but_must_stay_inside_locked_clip(self):
        locked = project_locked_anchor_selection(full_lock_fixture())["populations"]["internal"]
        audits = []
        for index, record in enumerate(locked):
            boundary_index = int(record["internal_boundary_index"])
            if index == 0:
                local = int(record["end_ms"]) - int(record["clip_start_ms"]) + 100
            else:
                local = (int(record["start_ms"]) + int(record["end_ms"])) // 2 - int(record["clip_start_ms"])
            audits.append(
                {
                    "case_id": str(record["case_id"]),
                    "partition": str(record["partition"]),
                    "source_benchmark_partition": str(record["source_benchmark_partition"]),
                    "track": str(record["track"]),
                    "cue_number": str(record["cue_number"]),
                    "clip_relpath": str(record["clip_relpath"]),
                    "clip_start_ms": str(record["clip_start_ms"]),
                    "clip_end_ms": str(record["clip_end_ms"]),
                    "editor_start_ms_reference_only": str(record["start_ms"]),
                    "editor_end_ms_reference_only": str(record["end_ms"]),
                    "canonical_text": str(record["canonical_text"]),
                    "segment_count": str(record["segment_count"]),
                    "internal_boundary_index": str(boundary_index),
                    "internal_left_text": str(record["segments"][boundary_index - 1]["text"]),
                    "internal_right_text": str(record["segments"][boundary_index]["text"]),
                    "gold_internal_clip_ms": str(local),
                    "gold_internal_clarity": "clear",
                }
            )
        records = _internal_records(locked, audits)
        self.assertEqual(len(records), 12)
        self.assertGreater(records[0]["gold_ms"], int(locked[0]["end_ms"]))
        audits[0]["gold_internal_clip_ms"] = str(int(locked[0]["clip_end_ms"]) - int(locked[0]["clip_start_ms"]))
        with self.assertRaisesRegex(ValueError, "outside locked clip"):
            _internal_records(locked, audits)

    def test_ui_ux_32_exposes_coarse_fine_timeline_and_clearer_ab_controls(self):
        self.assertEqual(AUDIT_UX_REVISION, "3.2")
        for token in (
            "UI 3.2",
            "V4 Boundary Authority",
            "Anchor Pack",
            "Machine Consensus",
            "-2000",
            "+2000",
            "局部 ±2 秒放大时间轴",
            "10ms",
            "只听 A",
            "只听 B",
            "A/B ×2",
            "700ms",
            "1000ms",
            "jumpFromInput",
            "timelineCommit",
        ):
            self.assertIn(token, HTML)

    def test_audit_ui_refuses_silent_port_fallback(self):
        with patch(
            "scripts.v4_human_boundary_anchor_audit_ui.ThreadingHTTPServer",
            side_effect=OSError("occupied"),
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing silent fallback"):
                _bind_server(object(), 8765)


if __name__ == "__main__":
    unittest.main()
