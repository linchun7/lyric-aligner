import unittest

from scripts.v4_seal_boundary_authority_overlay import sha256_json, verify_self_sha


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


if __name__ == "__main__":
    unittest.main()
