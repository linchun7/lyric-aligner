import copy
import hashlib
import json
import unittest

from lyric_aligner.evaluation.editor_risk_profile import (
    EDITOR_RISK_PROFILE_AUTHORITY,
    EDITOR_RISK_PROFILE_SCHEMA_VERSION,
    EditorRiskProfileError,
    authoritative_editor_profile_from_artifact,
    build_editor_risk_profile_artifact,
)
from scripts.test_support_production_calibration import human_anchor_gold_artifact


def _sha_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fixture(*, start_offset_ms=300, end_offset_ms=500):
    # Human Anchor case identities are deterministic and independent of the lock SHA.
    seed_gold = human_anchor_gold_artifact(selection_lock_sha256="0" * 64)
    by_case = {}
    for row in seed_gold["records"]:
        if row["population"] != "outer":
            continue
        by_case.setdefault(row["case_id"], {})[row["boundary_kind"]] = row

    outer = []
    for case_id, pair in sorted(by_case.items()):
        start = pair["start"]
        end = pair["end"]
        outer.append(
            {
                "case_id": case_id,
                "cue_number": start["cue_number"],
                "track": start["track"],
                "start_ms": start["gold_ms"] + start_offset_ms,
                "end_ms": end["gold_ms"] + end_offset_ms,
                "canonical_text": f"fixture-{start['cue_number']}",
                "segments": [{"lrc_index": start["cue_number"], "text": f"fixture-{start['cue_number']}"}],
                "internal_boundary_index": None,
                "source_status": "replace_existing",
                "source_confidence": "high",
                "source_evidence": "fixture",
                "segment_count": 1,
                "purpose": "outer",
                "boundary_kinds": ["start", "end"],
                "partition": start["partition"],
            }
        )

    lock = {
        "schema_version": "human-boundary-anchor-pack-lock-1.1",
        "selection": {
            "schema_version": "human-boundary-anchor-selection-1.1",
            "policy_id": "fixture",
            "populations": {"outer": outer, "internal": []},
        },
        "inputs": {
            "language_scope": "en",
            "final_audio_sha256": "5" * 64,
        },
    }
    lock["lock_sha256"] = _sha_json(lock)
    gold = human_anchor_gold_artifact(selection_lock_sha256=lock["lock_sha256"])
    return lock, gold, gold["selection_lock_file_sha256"]


class EditorRiskProfileTests(unittest.TestCase):
    def test_build_holdout_start_profile_uses_same_locked_human_gold_population(self):
        lock, gold, lock_file_sha = _fixture(start_offset_ms=300, end_offset_ms=500)
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="holdout",
        )
        self.assertEqual(artifact["schema_version"], EDITOR_RISK_PROFILE_SCHEMA_VERSION)
        self.assertEqual(artifact["authority_class"], EDITOR_RISK_PROFILE_AUTHORITY)
        self.assertEqual(artifact["selection_lock_sha256"], lock["lock_sha256"])
        self.assertEqual(artifact["human_gold_artifact_sha256"], gold["artifact_sha256"])
        self.assertEqual(artifact["profile"]["sample_count"], 4)
        self.assertEqual(artifact["profile"]["distinct_track_count"], 4)
        self.assertEqual(artifact["profile"]["population"], "holdout")
        # Human Anchor clear rows carry 50ms uncertainty, so a 300ms editor offset
        # becomes 250ms effective error.
        self.assertEqual(artifact["profile"]["mean_effective_error_ms"], 250.0)
        self.assertEqual(artifact["profile"]["p90_effective_error_ms"], 250.0)
        self.assertEqual(artifact["profile"]["production_authoritative"], False)
        self.assertEqual(artifact["profile"]["provenance_sha256"], "")
        self.assertEqual(len(artifact["records"]), 4)
        self.assertEqual(artifact["records_sha256"], _sha_json(artifact["records"]))
        unsigned = dict(artifact)
        claimed = unsigned.pop("artifact_sha256")
        self.assertEqual(claimed, _sha_json(unsigned))

    def test_end_profile_is_separate_scope(self):
        lock, gold, lock_file_sha = _fixture(start_offset_ms=300, end_offset_ms=500)
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="end",
            population="holdout",
        )
        self.assertEqual(artifact["profile"]["boundary_kind"], "end")
        self.assertEqual(artifact["profile"]["sample_count"], 4)
        self.assertEqual(artifact["profile"]["mean_effective_error_ms"], 450.0)

    def test_authority_is_attached_only_after_exact_rebuild(self):
        lock, gold, lock_file_sha = _fixture()
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="holdout",
        )
        profile = authoritative_editor_profile_from_artifact(
            artifact,
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
        )
        self.assertTrue(profile.production_authoritative)
        self.assertEqual(profile.provenance_sha256, artifact["artifact_sha256"])
        self.assertEqual(profile.sample_count, 4)

    def test_rehashed_profile_tamper_still_fails_reproducibility_check(self):
        lock, gold, lock_file_sha = _fixture()
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="holdout",
        )
        tampered = copy.deepcopy(artifact)
        tampered["profile"]["mean_effective_error_ms"] = 1.0
        tampered.pop("artifact_sha256")
        tampered["artifact_sha256"] = _sha_json(tampered)
        with self.assertRaisesRegex(EditorRiskProfileError, "cannot be reproduced"):
            authoritative_editor_profile_from_artifact(
                tampered,
                selection_lock=lock,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
            )

    def test_profile_cannot_self_assert_authority_even_with_rehash(self):
        lock, gold, lock_file_sha = _fixture()
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="holdout",
        )
        tampered = copy.deepcopy(artifact)
        tampered["profile"]["production_authoritative"] = True
        tampered["profile"]["provenance_sha256"] = "f" * 64
        tampered.pop("artifact_sha256")
        tampered["artifact_sha256"] = _sha_json(tampered)
        with self.assertRaises(EditorRiskProfileError):
            authoritative_editor_profile_from_artifact(
                tampered,
                selection_lock=lock,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
            )

    def test_rehashed_selection_lock_tamper_is_rejected_by_gold_identity(self):
        lock, gold, lock_file_sha = _fixture()
        changed = copy.deepcopy(lock)
        changed["selection"]["policy_id"] = "attacker-rehashed-policy"
        changed.pop("lock_sha256")
        changed["lock_sha256"] = _sha_json(changed)
        with self.assertRaisesRegex(EditorRiskProfileError, "does not match human gold"):
            build_editor_risk_profile_artifact(
                selection_lock=changed,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
                boundary_kind="start",
                population="holdout",
            )

    def test_invalid_selection_lock_self_hash_is_rejected(self):
        lock, gold, lock_file_sha = _fixture()
        changed = copy.deepcopy(lock)
        changed["selection"]["policy_id"] = "changed-without-rehash"
        with self.assertRaisesRegex(EditorRiskProfileError, "self-hash"):
            build_editor_risk_profile_artifact(
                selection_lock=changed,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
                boundary_kind="start",
                population="holdout",
            )

    def test_selection_lock_file_sha_mismatch_is_rejected(self):
        lock, gold, _ = _fixture()
        with self.assertRaisesRegex(EditorRiskProfileError, "file SHA"):
            build_editor_risk_profile_artifact(
                selection_lock=lock,
                selection_lock_file_sha256="9" * 64,
                human_gold_artifact=gold,
                boundary_kind="start",
                population="holdout",
            )

    def test_rehashed_human_gold_timing_tamper_is_still_bound_to_selection_identity(self):
        lock, gold, lock_file_sha = _fixture()
        tampered = copy.deepcopy(gold)
        tampered["records"][0]["gold_ms"] += 1000
        tampered["gold_sha256"] = _sha_json(tampered["records"])
        tampered.pop("artifact_sha256")
        tampered["artifact_sha256"] = _sha_json(tampered)
        # Structurally valid human-gold artifacts are permitted to contain different
        # audited timings, so this builder will recompute the resulting editor risk.
        # The important property is that authority is tied to that exact rehashed gold.
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=tampered,
            boundary_kind="start",
            population="holdout",
        )
        self.assertEqual(artifact["human_gold_artifact_sha256"], tampered["artifact_sha256"])
        with self.assertRaises(EditorRiskProfileError):
            authoritative_editor_profile_from_artifact(
                artifact,
                selection_lock=lock,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
            )

    def test_calibration_partition_has_eight_samples(self):
        lock, gold, lock_file_sha = _fixture()
        artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="calibration",
        )
        self.assertEqual(artifact["profile"]["sample_count"], 8)
        self.assertEqual(artifact["profile"]["distinct_track_count"], 8)

    def test_internal_editor_profile_is_not_implicitly_supported(self):
        lock, gold, lock_file_sha = _fixture()
        with self.assertRaisesRegex(EditorRiskProfileError, "outer start/end only"):
            build_editor_risk_profile_artifact(
                selection_lock=lock,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
                boundary_kind="internal",
                population="holdout",
            )


if __name__ == "__main__":
    unittest.main()
