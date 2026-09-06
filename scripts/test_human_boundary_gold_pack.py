import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from lyric_aligner.evaluation.production_calibration import validate_human_boundary_gold_artifact
from scripts import v4_build_human_boundary_gold_pack as build
from scripts import v4_ingest_human_boundary_gold as ingest


class HumanBoundaryGoldPackTests(unittest.TestCase):
    def make_sources(self, root: Path) -> tuple[Path, Path, Path]:
        lyric_dir = root / "lyrics"
        lyric_dir.mkdir()
        report = root / "report.csv"
        audio = root / "final.wav"
        sf.write(str(audio), np.zeros((90000, 1), dtype=np.float32), 1000, subtype="PCM_16")

        report_rows = []
        cue_number = 0
        for track_number in range(20):
            track = f"track-{track_number:02d}"
            lrc = lyric_dir / f"Artist - {track}.lrc"
            lrc.write_text(
                "[00:10.00]single alpha\n"
                "[00:11.00]single beta\n"
                "[00:12.00]multi left\n"
                "[00:13.00]multi right\n"
                "[00:14.00]other left\n"
                "[00:15.00]other right\n",
                encoding="utf-8",
            )
            definitions = [
                ("single alpha", "0"),
                ("single beta", "1"),
                ("multi left multi right", "2;3"),
                ("other left other right", "4;5"),
            ]
            for text, indices in definitions:
                cue_number += 1
                start_ms = 1000 + cue_number * 1000
                report_rows.append(
                    {
                        "kind": "existing",
                        "original_cue": str(cue_number),
                        "start_ms": str(start_ms),
                        "end_ms": str(start_ms + 500),
                        "text": text,
                        "track": track,
                        "lrc_indices": indices,
                        "status": "fixture",
                        "confidence": "high",
                        "evidence": "fixture",
                    }
                )
        with report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(report_rows[0]))
            writer.writeheader()
            writer.writerows(report_rows)
        return report, lyric_dir, audio

    def build_pack(self, root: Path) -> Path:
        report, lyric_dir, audio = self.make_sources(root)
        pack = root / "pack"
        with patch.object(
            sys,
            "argv",
            [
                "v4_build_human_boundary_gold_pack.py",
                "--report",
                str(report),
                "--lyrics-dir",
                str(lyric_dir),
                "--audio",
                str(audio),
                "--language",
                "fixture",
                "--out-dir",
                str(pack),
            ],
        ):
            self.assertEqual(build.main(), 0)
        lock = json.loads((pack / "selection.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["inputs"]["language_scope"], "fixture")
        self.assertEqual(lock["summary"], {
            "outer_cue_count": 30,
            "internal_cue_count": 30,
            "human_boundary_point_count": 90,
        })
        outer_ids = {row["case_id"] for row in lock["populations"]["outer"]["records"]}
        internal_ids = {row["case_id"] for row in lock["populations"]["internal"]["records"]}
        self.assertFalse(outer_ids & internal_ids)
        return pack

    def update_csv(self, path: Path, updater) -> None:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        for row in rows:
            updater(row)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def fill_audits(self, pack: Path) -> None:
        def fill_outer(row):
            editor_start = int(row["editor_start_ms_reference_only"])
            editor_end = int(row["editor_end_ms_reference_only"])
            clip_start = int(row["clip_start_ms"])
            row["gold_start_clip_ms"] = str(editor_start - clip_start)
            row["gold_start_uncertainty_ms"] = "20"
            row["gold_end_clip_ms"] = str(editor_end - clip_start)
            row["gold_end_uncertainty_ms"] = "20"

        def fill_internal(row):
            editor_start = int(row["editor_start_ms_reference_only"])
            clip_start = int(row["clip_start_ms"])
            row["gold_internal_clip_ms"] = str(editor_start - clip_start + 250)
            row["gold_internal_uncertainty_ms"] = "20"

        self.update_csv(pack / "outer" / "human_audit.csv", fill_outer)
        self.update_csv(pack / "internal" / "human_audit.csv", fill_internal)

    def rewrite_lock_hash(self, lock: dict) -> None:
        lock.pop("lock_sha256", None)
        lock["lock_sha256"] = ingest.sha256_json(lock)

    def test_build_verify_and_ingest_produce_exact_90_scope_separated_points(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self.build_pack(root)
            self.fill_audits(pack)
            out = root / "gold.json"
            with patch.object(
                sys,
                "argv",
                [
                    "v4_ingest_human_boundary_gold.py",
                    "--lock",
                    str(pack / "selection.lock.json"),
                    "--out",
                    str(out),
                ],
            ):
                self.assertEqual(ingest.main(), 0)
            gold = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(gold["record_count"], 90)
            self.assertEqual(gold["boundary_kind_counts"], {"start": 30, "end": 30, "internal": 30})
            self.assertEqual(gold["population_counts"], {"outer": 60, "internal": 30})
            verified = validate_human_boundary_gold_artifact(gold)
            self.assertEqual(verified["artifact_sha256"], gold["artifact_sha256"])
            self.assertEqual(len(verified["records"]), 90)

    def test_forged_rehashed_selection_fails_fresh_deterministic_reconstruction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self.build_pack(root)
            lock_path = pack / "selection.lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            population = lock["populations"]["outer"]
            selection = population["selection"]
            selection["records"][0]["track"] = "forged-track"
            selection.pop("selection_sha256", None)
            selection["selection_sha256"] = ingest.sha256_json(selection)
            population["selection_sha256"] = selection["selection_sha256"]
            population["records"][0]["track"] = "forged-track"
            self.rewrite_lock_hash(lock)
            lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(ValueError):
                ingest.verify_lock(lock, lock_path)

    def test_rehashed_replacement_clip_fails_final_mix_regeneration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self.build_pack(root)
            lock_path = pack / "selection.lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            record = lock["populations"]["outer"]["records"][0]
            clip = pack / record["clip_relpath"]
            info = sf.info(str(clip))
            sf.write(
                str(clip),
                np.full((info.frames, info.channels), 0.25, dtype=np.float32),
                info.samplerate,
                subtype="PCM_16",
            )
            record["clip_sha256"] = ingest.sha256_file(clip)
            self.rewrite_lock_hash(lock)
            lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(ValueError):
                ingest.verify_lock(lock, lock_path)

    def test_explicit_pack_invalidation_blocks_ingest_before_audit_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self.build_pack(root)
            self.fill_audits(pack)
            (pack / ingest.PACK_INVALIDATION_SENTINEL).write_text(
                json.dumps(
                    {"status": "invalidated", "reason": "source report ownership regression"}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "human-gold pack is invalidated"):
                ingest.build_human_boundary_gold_artifact(pack / "selection.lock.json")

    def test_audit_identity_and_uncertainty_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self.build_pack(root)
            self.fill_audits(pack)
            outer_path = pack / "outer" / "human_audit.csv"
            self.update_csv(outer_path, lambda row: row.update({"gold_start_uncertainty_ms": "101"}))
            lock = json.loads((pack / "selection.lock.json").read_text(encoding="utf-8"))
            outer, internal = ingest.verify_lock(lock, pack / "selection.lock.json")
            outer_rows = ingest._load_audit(outer["audit_path"])
            with self.assertRaises(ValueError):
                ingest._outer_gold(outer["records"], outer_rows)

            self.fill_audits(pack)
            self.update_csv(outer_path, lambda row: row.update({"track": "changed-track"}))
            outer_rows = ingest._load_audit(outer["audit_path"])
            with self.assertRaises(ValueError):
                ingest._outer_gold(outer["records"], outer_rows)


if __name__ == "__main__":
    unittest.main()
