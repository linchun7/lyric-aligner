from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.qa.structural_evidence import (
    EDITOR_SOURCE_MAP_AUTHORITY,
    EDITOR_SOURCE_MAP_SCHEMA,
    StructuralEvidenceAuditError,
    audit_structural_evidence,
    file_sha256,
    load_editor_source_map,
)


def _clock(ms: int) -> str:
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_srt(path: Path, rows: list[tuple[int, int]]) -> None:
    blocks = []
    for index, (start_ms, end_ms) in enumerate(rows, start=1):
        blocks.append(f"{index}\n{_clock(start_ms)} --> {_clock(end_ms)}\ncue {index}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def _write_audio(path: Path) -> None:
    samples = np.zeros(10_000, dtype=np.float32)
    samples[:5_000] = 0.25
    samples[8_000:9_000] = 0.4
    sf.write(path, samples, 1000, subtype="FLOAT")


def _write_map(
    path: Path,
    *,
    repository_root: Path,
    srt: Path,
    task_fingerprint: str,
    positions: list[int],
    authority: str = EDITOR_SOURCE_MAP_AUTHORITY,
    source_path: Path | None = None,
    source_sha: str | None = None,
) -> Path:
    artifact = source_path or (repository_root / "source-map-authority.json")
    if source_path is None:
        artifact.write_text('{"authority":"fixture"}\n', encoding="utf-8")
    try:
        artifact_rel = artifact.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        artifact_rel = str(artifact)
    payload = {
        "schema_version": EDITOR_SOURCE_MAP_SCHEMA,
        "task_fingerprint_sha256": task_fingerprint,
        "mapping_authority": authority,
        "editor_srt_sha256": file_sha256(srt),
        "source_mapping_artifact_path": artifact_rel,
        "source_mapping_artifact_sha256": source_sha or file_sha256(artifact),
        "cue_count": len(srt.read_text(encoding="utf-8").strip().split("\n\n")),
        "mapped_cue_positions": positions,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact


def _load_map_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_map_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class StructuralEvidenceAuditTests(unittest.TestCase):
    def test_missing_mapping_authority_skips_reorder_but_runs_audio_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            audio = root / "input.wav"
            _write_srt(srt, [(0, 500), (5000, 5500), (1000, 1500)])
            _write_audio(audio)
            result = audit_structural_evidence(
                editor_srt=srt,
                audio_path=audio,
                expected_task_fingerprint="f" * 64,
                repository_root=root,
            )
            self.assertEqual(result["reorder"]["status"], "not_run_missing_source_mapping_authority")
            self.assertEqual(result["reorder"]["event_count"], 0)
            self.assertEqual(result["detached_tail"]["event_count"], 1)
            self.assertEqual(result["event_count"], 1)

    def test_valid_mapping_authority_detects_reorder_and_stays_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            audio = root / "input.wav"
            editor_map = root / "map.json"
            fingerprint = "f" * 64
            _write_srt(srt, [(0, 500), (5000, 5500), (1000, 1500), (6000, 6500)])
            _write_audio(audio)
            _write_map(
                editor_map,
                repository_root=root,
                srt=srt,
                task_fingerprint=fingerprint,
                positions=[0, 1, 2, 3],
            )
            result = audit_structural_evidence(
                editor_srt=srt,
                audio_path=audio,
                expected_task_fingerprint=fingerprint,
                repository_root=root,
                editor_source_map=editor_map,
            )
            self.assertEqual(result["reorder"]["status"], "evaluated")
            self.assertEqual(result["reorder"]["event_count"], 1)
            self.assertEqual(result["detached_tail"]["event_count"], 1)
            self.assertEqual(result["authority"], "diagnostic_only")
            self.assertFalse(result["automatic_timing_change_allowed"])
            self.assertFalse(result["automatic_content_end_change_allowed"])
            self.assertFalse(result["automatic_review_resolution_allowed"])
            self.assertFalse(result["release_gate_eligible"])
            self.assertFalse(result["publish_ready"])

    def test_unmapped_prefix_inversion_does_not_gain_reorder_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            audio = root / "input.wav"
            editor_map = root / "map.json"
            fingerprint = "f" * 64
            _write_srt(srt, [(54_000, 55_000), (60_000, 61_000), (1_500, 2_000), (10_000, 11_000), (12_000, 13_000)])
            _write_audio(audio)
            _write_map(
                editor_map,
                repository_root=root,
                srt=srt,
                task_fingerprint=fingerprint,
                positions=[3, 4],
            )
            result = audit_structural_evidence(
                editor_srt=srt,
                audio_path=audio,
                expected_task_fingerprint=fingerprint,
                repository_root=root,
                editor_source_map=editor_map,
            )
            self.assertEqual(result["reorder"]["event_count"], 0)

    def test_editor_map_wrong_task_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="1" * 64, positions=[0, 1])
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "another task"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="2" * 64, repository_root=root)

    def test_editor_map_requires_source_occurrence_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(
                editor_map,
                repository_root=root,
                srt=srt,
                task_fingerprint="f" * 64,
                positions=[0, 1],
                authority="heuristic_only",
            )
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "authority"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)

    def test_editor_map_positions_must_be_unique_sorted_and_in_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="f" * 64, positions=[1, 0])
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "unique and sorted"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)
            _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="f" * 64, positions=[0, 2])
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "out-of-range"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)

    def test_editor_map_requires_valid_source_artifact_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(
                editor_map,
                repository_root=root,
                srt=srt,
                task_fingerprint="f" * 64,
                positions=[0, 1],
                source_sha="not-a-sha",
            )
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "source_mapping_artifact_sha256"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)

    def test_editor_map_requires_source_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="f" * 64, positions=[0, 1])
            payload = _load_map_payload(editor_map)
            payload.pop("source_mapping_artifact_path")
            _save_map_payload(editor_map, payload)
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "missing source_mapping_artifact_path"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)

    def test_editor_map_rejects_source_artifact_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            outside = root.parent / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="f" * 64, positions=[0, 1])
            payload = _load_map_payload(editor_map)
            payload["source_mapping_artifact_path"] = "../outside.json"
            payload["source_mapping_artifact_sha256"] = file_sha256(outside)
            _save_map_payload(editor_map, payload)
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "inside the repository"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)

    def test_editor_map_rejects_missing_source_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="f" * 64, positions=[0, 1])
            payload = _load_map_payload(editor_map)
            payload["source_mapping_artifact_path"] = "missing.json"
            _save_map_payload(editor_map, payload)
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "does not exist"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)

    def test_editor_map_rejects_source_artifact_sha_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "input.srt"
            editor_map = root / "map.json"
            _write_srt(srt, [(0, 500), (1000, 1500)])
            artifact = _write_map(editor_map, repository_root=root, srt=srt, task_fingerprint="f" * 64, positions=[0, 1])
            artifact.write_text('{"changed":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(StructuralEvidenceAuditError, "SHA mismatch"):
                load_editor_source_map(editor_map, editor_srt=srt, expected_task_fingerprint="f" * 64, repository_root=root)


if __name__ == "__main__":
    unittest.main()
