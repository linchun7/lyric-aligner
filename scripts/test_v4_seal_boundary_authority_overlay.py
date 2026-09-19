import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.v4_seal_boundary_authority_overlay import seal, sha256_json, verify_self_sha


class BoundaryAuthorityOverlaySealTests(unittest.TestCase):
    def test_self_sha_accepts_exact_payload(self):
        payload = {"schema_version": "fixture", "count": 0}
        payload["artifact_sha256"] = sha256_json(payload)
        self.assertEqual(
            verify_self_sha(payload, "artifact_sha256", label="fixture"),
            payload["artifact_sha256"],
        )

    def test_self_sha_rejects_tampering(self):
        payload = {"schema_version": "fixture", "count": 0}
        payload["artifact_sha256"] = sha256_json(payload)
        payload["count"] = 1
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_self_sha(payload, "artifact_sha256", label="fixture")

    def test_zero_is_preserved_by_semantic_hash(self):
        zero = {"timing_changed_count": 0}
        absent = {}
        self.assertNotEqual(sha256_json(zero), sha256_json(absent))

    @patch("scripts.v4_seal_boundary_authority_overlay.active_invalidation_markers")
    def test_seal_rejects_active_invalidation_before_any_release_validation(self, markers):
        markers.return_value = [Path("INVALIDATED.json")]
        args = SimpleNamespace(out=Path("never-written-seal.json"), final_srt=Path("final.srt"))
        with self.assertRaisesRegex(ValueError, "release is invalidated"):
            seal(args)

    @patch("scripts.v4_seal_boundary_authority_overlay.active_invalidation_references")
    @patch("scripts.v4_seal_boundary_authority_overlay.sha256_file")
    @patch("scripts.v4_seal_boundary_authority_overlay.verify_manifest_inputs")
    @patch("scripts.v4_seal_boundary_authority_overlay.load_task_manifest")
    @patch("scripts.v4_seal_boundary_authority_overlay.active_invalidation_markers")
    def test_seal_rejects_tracked_invalidation_reference(
        self, markers, load_manifest, verify_inputs, sha_file, invalidation_refs
    ):
        markers.return_value = []
        load_manifest.return_value = {"task_fingerprint_sha256": "a" * 64}
        verify_inputs.return_value = []
        sha_file.return_value = "b" * 64
        invalidation_refs.return_value = [Path("references/release-invalidation.json")]
        args = SimpleNamespace(
            out=Path("never-written-seal.json"),
            final_srt=Path("final.srt"),
            task_manifest=Path("task_manifest.json"),
        )
        with self.assertRaisesRegex(ValueError, "tracked invalidation reference"):
            seal(args)


if __name__ == "__main__":
    unittest.main()
