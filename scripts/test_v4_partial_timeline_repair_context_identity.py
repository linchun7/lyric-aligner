from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest
from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_context import derive_effective_run_mapping_context


FINGERPRINT = "f" * 64


def _mapping(mode: str) -> dict:
    return {
        "mode": mode,
        "intercept": 0.0,
        "base_slope": 1.0,
        "breakpoints": [] if mode == "AFFINE" else [10.0],
        "slope_deltas": [] if mode == "AFFINE" else [0.05],
        "diagnostics": {},
        "objective": 0.1,
    }


def _write_stage(
    root: Path,
    *,
    name: str,
    payload: dict,
    stage: str,
    role: str,
    upstreams: tuple[str, ...] = (),
) -> tuple[Path, Path, dict]:
    payload_path = root / f"{name}.json"
    artifact_path = root / f"{name}.artifact.json"
    atomic_write_json(payload_path, payload)
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=FINGERPRINT,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload_path),),
        upstream_artifact_ids=upstreams,
    )
    atomic_write_json(artifact_path, artifact)
    return payload_path, artifact_path, artifact


def _coarse(root: Path):
    payload = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "occurrence_id": "occ-1",
        "track_id": "track-1",
        "canonical_selection_sha256": "selection-1",
        "result": {
            "timewarp": {
                "mapping": _mapping("AFFINE"),
                "blocked": False,
            }
        },
    }
    return _write_stage(
        root,
        name="coarse",
        payload=payload,
        stage="coarse_audio_alignment",
        role="coarse_alignment",
    )


def _fine(
    root: Path,
    *,
    coarse_id: str,
    track_id: str = "track-1",
    canonical_selection_sha256: str = "selection-1",
):
    payload = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "occurrence_id": "occ-1",
        "track_id": track_id,
        "canonical_selection_sha256": canonical_selection_sha256,
        "result": {
            "applied": True,
            "timewarp": {
                "mapping": _mapping("PIECEWISE_RATE"),
                "blocked": False,
            },
        },
    }
    return _write_stage(
        root,
        name="fine",
        payload=payload,
        stage="fine_audio_alignment",
        role="fine_alignment",
        upstreams=(coarse_id,),
    )


def _run(
    root: Path,
    *,
    coarse_path: Path,
    coarse_artifact_path: Path,
    coarse_id: str,
    fine_path: Path,
    fine_artifact_path: Path,
    fine_id: str,
):
    occurrence = {
        "occurrence_id": "occ-1",
        "mapping_source": "fine",
        "mapping_blocked": False,
        "fine_applied": True,
        "coarse_path": str(coarse_path),
        "coarse_artifact_path": str(coarse_artifact_path),
        "fine_path": str(fine_path),
        "fine_artifact_path": str(fine_artifact_path),
    }
    payload = {
        "schema_version": "1.2",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "status": "ready_for_render",
        "legacy_fallback_used": False,
        "occurrences": [occurrence],
        "issues": [],
    }
    return _write_stage(
        root,
        name="run",
        payload=payload,
        stage="production_orchestration",
        role="v4_production_run",
        upstreams=(coarse_id, fine_id),
    )


class PartialTimelineRepairContextIdentityTests(unittest.TestCase):
    def _derive_with_fine_identity(
        self,
        *,
        track_id: str = "track-1",
        canonical_selection_sha256: str = "selection-1",
    ):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        coarse_path, coarse_artifact_path, coarse_artifact = _coarse(root)
        fine_path, fine_artifact_path, fine_artifact = _fine(
            root,
            coarse_id=coarse_artifact["artifact_id"],
            track_id=track_id,
            canonical_selection_sha256=canonical_selection_sha256,
        )
        run_path, run_artifact_path, _ = _run(
            root,
            coarse_path=coarse_path,
            coarse_artifact_path=coarse_artifact_path,
            coarse_id=coarse_artifact["artifact_id"],
            fine_path=fine_path,
            fine_artifact_path=fine_artifact_path,
            fine_id=fine_artifact["artifact_id"],
        )
        return derive_effective_run_mapping_context(
            run_path=run_path,
            run_artifact_path=run_artifact_path,
        )

    def test_fine_track_identity_must_match_effective_coarse(self):
        with self.assertRaisesRegex(
            PartialTimelineRepairError,
            "Fine/coarse track identity mismatch",
        ):
            self._derive_with_fine_identity(track_id="track-2")

    def test_fine_canonical_selection_must_match_effective_coarse(self):
        with self.assertRaisesRegex(
            PartialTimelineRepairError,
            "Fine/coarse canonical selection identity mismatch",
        ):
            self._derive_with_fine_identity(
                canonical_selection_sha256="selection-2"
            )

    def test_matching_fine_identity_still_derives_piecewise_rate(self):
        context = self._derive_with_fine_identity()
        self.assertEqual(
            context.mapping_kind_by_occurrence,
            {"occ-1": "PIECEWISE_RATE"},
        )


if __name__ == "__main__":
    unittest.main()
