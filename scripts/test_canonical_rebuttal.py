from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text.canonical_rebuttal import (
    POLICY_ID,
    POLICY_SCHEMA,
    build_truth_overlay,
    classify_variant_observation,
    load_rebuttal_policy,
    materialize_rebuttal_shadow,
    sha256_file,
)


CANONICAL_FIELDS = [
    "occurrence_id",
    "track_id",
    "canonical_line_index",
    "text",
]
FINAL_FIELDS = [
    "position",
    "cue_number",
    "text",
    "occurrence_id",
    "track_id",
    "canonical_line_index",
    "canonical_line_indices",
    "task_fingerprint_sha256",
]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def evidence(evidence_id: str, family: str) -> dict[str, str]:
    return {
        "evidence_id": evidence_id,
        "family": family,
        "sha256": ("a" if family == "editor_observation" else "b") * 64,
        "role": "supports_corrected_text",
    }


class CanonicalRebuttalTests(unittest.TestCase):
    def test_variant_classifier_does_not_confuse_but_with_no(self):
        self.assertEqual(
            classify_variant_observation(
                "I could do this forever, but I don't wanna stop.",
                canonical_variant="know I don't",
                corrected_variant="no I don't",
            ),
            "ambiguous",
        )
        self.assertEqual(
            classify_variant_observation(
                "I could do this forever, no, I don't wanna stop.",
                canonical_variant="know I don't",
                corrected_variant="no I don't",
            ),
            "supports_corrected_text",
        )
        self.assertEqual(
            classify_variant_observation(
                "I could do this forever know I don't wanna stop.",
                canonical_variant="know I don't",
                corrected_variant="no I don't",
            ),
            "supports_canonical_text",
        )

    def make_fixture(self, *, corrected: str = "I could do this forever, no I don't wanna stop"):
        root_obj = tempfile.TemporaryDirectory()
        root = Path(root_obj.name)
        canonical = root / "canonical.csv"
        write_csv(
            canonical,
            CANONICAL_FIELDS,
            [
                {
                    "occurrence_id": "occ1",
                    "track_id": "track1",
                    "canonical_line_index": 2,
                    "text": "I could do this forever know I don't wanna stop",
                }
            ],
        )
        policy = root / "policy.json"
        payload = {
            "schema_version": POLICY_SCHEMA,
            "policy_id": POLICY_ID,
            "task_fingerprint_sha256": "f" * 64,
            "canonical_evaluation_audit_sha256": sha256_file(canonical),
            "min_independent_evidence_families": 2,
            "model_identity": {
                "provider": "openai",
                "model_id": "test-semantic-model",
                "prompt_policy_id": "canonical-rebuttal-test-prompt-1",
            },
            "overrides": [
                {
                    "occurrence_id": "occ1",
                    "track_id": "track1",
                    "canonical_line_index": 2,
                    "expected_text": "I could do this forever know I don't wanna stop",
                    "corrected_text": corrected,
                    "kind": "lexical_rebuttal",
                    "confidence": "high",
                    "status": "authorized",
                    "reason": "bounded semantic/acoustic evidence favors corrected wording",
                    "reviewer": "test-semantic-model",
                    "evidence": [
                        evidence("editor-1", "editor_observation"),
                        evidence("acoustic-1", "independent_acoustic_model"),
                    ],
                }
            ],
        }
        policy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return root_obj, root, canonical, policy

    def test_raw_observation_role_is_recomputed_before_it_can_count_as_support(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["evidence"][0].update(
            {
                "observation_text": "I could do this forever, but I don't wanna stop.",
                "canonical_variant": "know I don't",
                "corrected_variant": "no I don't",
            }
        )
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "claimed role.*disagrees"):
            load_rebuttal_policy(
                policy_path,
                canonical_evaluation_audit=canonical,
                expected_task_fingerprint="f" * 64,
            )

    def test_authorized_lexical_rebuttal_requires_two_evidence_families(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["evidence"] = [evidence("editor-1", "editor_observation")]
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "independent supporting evidence families"):
            load_rebuttal_policy(
                policy_path,
                canonical_evaluation_audit=canonical,
                expected_task_fingerprint="f" * 64,
            )

    def test_conflicting_or_provenance_evidence_does_not_count_as_support(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["evidence"][1]["role"] = "supports_canonical_text"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "independent supporting evidence families"):
            load_rebuttal_policy(
                policy_path,
                canonical_evaluation_audit=canonical,
                expected_task_fingerprint="f" * 64,
            )

    def test_authorized_rebuttal_fails_when_direct_canonical_counterevidence_exists(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["evidence"].append(
            {
                "evidence_id": "counter-1",
                "family": "independent_counterevidence",
                "sha256": "c" * 64,
                "role": "supports_canonical_text",
            }
        )
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "direct evidence supporting canonical text"):
            load_rebuttal_policy(
                policy_path,
                canonical_evaluation_audit=canonical,
                expected_task_fingerprint="f" * 64,
            )

    def test_candidate_can_exist_without_automatic_authority(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["status"] = "candidate"
        payload["overrides"][0]["confidence"] = "medium"
        payload["overrides"][0]["evidence"] = []
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        policy = load_rebuttal_policy(
            policy_path,
            canonical_evaluation_audit=canonical,
            expected_task_fingerprint="f" * 64,
        )
        self.assertFalse(policy["overrides"][0]["authorized"])
        overlay = build_truth_overlay(canonical_evaluation_audit=canonical, policy=policy)
        self.assertEqual(overlay["authorized_override_count"], 0)
        self.assertEqual(
            overlay["rows"][0]["truth_text"],
            "I could do this forever know I don't wanna stop",
        )

    def test_presentation_only_change_cannot_be_mislabeled_lexical(self):
        root_obj, _, canonical, policy_path = self.make_fixture(
            corrected="I could do this forever know I don't wanna stop"
        )
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["corrected_text"] = "I could do this forever know I don't wanna stop!"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "kind does not match"):
            load_rebuttal_policy(
                policy_path,
                canonical_evaluation_audit=canonical,
                expected_task_fingerprint="f" * 64,
            )

    def test_expected_text_must_bind_exact_canonical_row(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["overrides"][0]["expected_text"] = "wrong baseline"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "expected_text differs"):
            load_rebuttal_policy(
                policy_path,
                canonical_evaluation_audit=canonical,
                expected_task_fingerprint="f" * 64,
            )

    def test_shadow_materializes_only_text_and_stays_non_publishable(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        policy = load_rebuttal_policy(
            policy_path,
            canonical_evaluation_audit=canonical,
            expected_task_fingerprint="f" * 64,
        )
        source = (
            "1\n00:00:01,000 --> 00:00:03,000\n"
            "I could do this forever know I don't wanna stop\n"
        )
        final_rows = [
            {
                "position": "1",
                "cue_number": "1",
                "text": "I could do this forever know I don't wanna stop",
                "occurrence_id": "occ1",
                "track_id": "track1",
                "canonical_line_index": "2",
                "canonical_line_indices": "",
                "task_fingerprint_sha256": "f" * 64,
            }
        ]
        shadow, report = materialize_rebuttal_shadow(
            source_srt_text=source,
            final_audit_rows=final_rows,
            policy=policy,
        )
        self.assertIn("forever, no I don't wanna stop", shadow)
        self.assertIn("00:00:01,000 --> 00:00:03,000", shadow)
        self.assertFalse(report["publish_ready"])
        self.assertFalse(report["timing_authority_used"])
        self.assertTrue(report["timeline_unchanged"])
        self.assertEqual(report["materialized_count"], 1)

    def test_multi_line_final_cue_is_deferred_not_repartitioned(self):
        root_obj, _, canonical, policy_path = self.make_fixture()
        self.addCleanup(root_obj.cleanup)
        policy = load_rebuttal_policy(
            policy_path,
            canonical_evaluation_audit=canonical,
            expected_task_fingerprint="f" * 64,
        )
        source = (
            "1\n00:00:01,000 --> 00:00:03,000\n"
            "I could do this forever know I don't wanna stop\n"
        )
        final_rows = [
            {
                "position": "1",
                "cue_number": "1",
                "text": "I could do this forever know I don't wanna stop",
                "occurrence_id": "occ1",
                "track_id": "track1",
                "canonical_line_index": "2",
                "canonical_line_indices": "[2, 3]",
                "task_fingerprint_sha256": "f" * 64,
            }
        ]
        shadow, report = materialize_rebuttal_shadow(
            source_srt_text=source,
            final_audit_rows=final_rows,
            policy=policy,
        )
        self.assertEqual(shadow, source)
        self.assertEqual(report["materialized_count"], 0)
        self.assertEqual(report["deferred_count"], 1)


if __name__ == "__main__":
    unittest.main()
