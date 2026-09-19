from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.timing_decision_pack import (
    GOLD_SCHEMA_VERSION,
    build_timing_decision_pack,
    merge_timing_decision_gold,
)


FIELDS = [
    "position",
    "cue_number",
    "start_ms",
    "end_ms",
    "text",
    "occurrence_id",
    "track_id",
    "canonical_line_index",
    "canonical_line_indices",
    "task_fingerprint_sha256",
]


def write_audit(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class TimingDecisionPackTests(unittest.TestCase):
    def make_rows(self):
        base = []
        hybrid = []
        for index in range(4):
            old_start = 1000 + index * 3000
            old_end = old_start + 1500
            new_start = old_start
            new_end = old_end
            if index == 1:
                new_start += 220
            if index == 2:
                new_end -= 350
            row = {
                "position": index + 1,
                "cue_number": index + 1,
                "start_ms": old_start,
                "end_ms": old_end,
                "text": f"line {index}",
                "occurrence_id": "occ1",
                "track_id": "track1",
                "canonical_line_index": index,
                "canonical_line_indices": "",
                "task_fingerprint_sha256": "f" * 64,
            }
            base.append(dict(row))
            row["start_ms"] = new_start
            row["end_ms"] = new_end
            hybrid.append(dict(row))
        return base, hybrid

    def test_changed_boundaries_and_controls_are_frozen_without_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            write_audit(old, base)
            write_audit(new, hybrid)
            pack = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                min_changed_delta_ms=100,
                unchanged_control_count=2,
                clip_margin_ms=1000,
            )
        self.assertFalse(pack["gold_read"])
        self.assertEqual(pack["changed_case_count"], 2)
        self.assertEqual(pack["control_case_count"], 2)
        self.assertEqual(pack["selected_case_count"], 4)
        self.assertTrue(pack["selection_lock_sha256"])
        changed = [case for case in pack["cases"] if case["selection_reason"] == "decision_changed_above_threshold"]
        self.assertEqual({case["absolute_change_ms"] for case in changed}, {220, 350})
        self.assertTrue(all("gold" not in key for case in pack["cases"] for key in case))

    def test_selection_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            write_audit(old, base)
            write_audit(new, hybrid)
            first = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                unchanged_control_count=3,
            )
            second = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                unchanged_control_count=3,
            )
        self.assertEqual(first["selection_lock_sha256"], second["selection_lock_sha256"])
        self.assertEqual(first["cases"], second["cases"])

    def test_multi_line_or_duplicate_identity_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            base[0]["canonical_line_indices"] = "[0, 1]"
            hybrid[0]["canonical_line_indices"] = "[0, 1]"
            base.append(dict(base[1]))
            base[-1]["position"] = 9
            hybrid.append(dict(hybrid[1]))
            hybrid[-1]["position"] = 9
            write_audit(old, base)
            write_audit(new, hybrid)
            pack = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                unchanged_control_count=10,
            )
        identities = {
            (case["private_identity"]["occurrence_id"], case["private_identity"]["canonical_line_index"])
            for case in pack["cases"]
        }
        self.assertNotIn(("occ1", 0), identities)
        self.assertNotIn(("occ1", 1), identities)

    def test_gold_must_bind_complete_pre_gold_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            write_audit(old, base)
            write_audit(new, hybrid)
            pack = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                unchanged_control_count=1,
            )
        gold = {
            "schema_version": GOLD_SCHEMA_VERSION,
            "selection_lock_sha256": pack["selection_lock_sha256"],
            "partition": "blind",
            "records": [
                {
                    "id": case["id"],
                    "gold_ms": case["hybrid_ms"],
                    "uncertainty_ms": 20,
                }
                for case in pack["cases"]
            ],
        }
        records = merge_timing_decision_gold(pack, gold)
        self.assertEqual(len(records), pack["selected_case_count"])
        self.assertTrue(all(record["partition"] == "blind" for record in records))
        self.assertTrue(all("editor_ms" not in record for record in records))

    def test_invalid_gold_cases_count_as_accounted_but_are_not_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            write_audit(old, base)
            write_audit(new, hybrid)
            pack = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                unchanged_control_count=0,
            )
        cases = list(pack["cases"])
        self.assertGreaterEqual(len(cases), 2)
        invalid_id = cases[0]["id"]
        gold = {
            "schema_version": GOLD_SCHEMA_VERSION,
            "selection_lock_sha256": pack["selection_lock_sha256"],
            "partition": "blind",
            "records": [
                {"id": case["id"], "gold_ms": case["hybrid_ms"], "uncertainty_ms": 20}
                for case in cases[1:]
            ],
            "invalid_records": [{"id": invalid_id, "reason": "target boundary unscorable"}],
        }
        records = merge_timing_decision_gold(pack, gold)
        self.assertEqual(len(records), len(cases) - 1)
        self.assertNotIn(invalid_id, {record["id"] for record in records})

    def test_incomplete_gold_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            write_audit(old, base)
            write_audit(new, hybrid)
            pack = build_timing_decision_pack(
                old_audit=old,
                hybrid_audit=new,
                final_mix_sha256="a" * 64,
                unchanged_control_count=1,
            )
        gold = {
            "schema_version": GOLD_SCHEMA_VERSION,
            "selection_lock_sha256": pack["selection_lock_sha256"],
            "partition": "blind",
            "records": [],
        }
        with self.assertRaisesRegex(ValueError, "incomplete"):
            merge_timing_decision_gold(pack, gold)

    def test_task_fingerprint_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            base, hybrid = self.make_rows()
            base[0]["task_fingerprint_sha256"] = "e" * 64
            write_audit(old, base)
            write_audit(new, hybrid)
            with self.assertRaisesRegex(ValueError, "exactly one task fingerprint"):
                build_timing_decision_pack(
                    old_audit=old,
                    hybrid_audit=new,
                    final_mix_sha256="a" * 64,
                )


if __name__ == "__main__":
    unittest.main()
