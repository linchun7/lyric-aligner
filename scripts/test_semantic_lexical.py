from __future__ import annotations

import unittest

from lyric_aligner.text.semantic_lexical import (
    POLICY_ID,
    RESPONSE_SCHEMA,
    build_semantic_request_bundle,
    materialize_semantic_shadow_candidate,
    semantic_partition_targets,
    validate_semantic_response,
)
from lyric_aligner.text_repair import (
    CanonicalLine,
    MatchDecision,
    SubtitleCue,
    _normalize_for_match,
)


def canonical(*lines: str) -> list[CanonicalLine]:
    return [
        CanonicalLine(i, "song.lrc", text, _normalize_for_match(text), source_ordinal=0)
        for i, text in enumerate(lines)
    ]


def cue(ordinal: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"00:00:0{ordinal + 1},000 --> 00:00:0{ordinal + 2},000",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=ordinal * 2,
    )


class SemanticLexicalTests(unittest.TestCase):
    def _bundle(self):
        cues = [cue(0, "so nyo shi dae"), cue(1, "ma le bwa")]
        lines = canonical("소녀시대", "말해봐")
        decisions = [
            MatchDecision(
                cue_ordinal=index,
                canonical_ordinal=0,
                score=0.2,
                action="review",
                reason="low_or_structurally_unsafe_similarity",
                cue_span=(0, 2),
                canonical_span=(0, 2),
                source_text=cues[index].text,
                canonical_text="소녀시대말해봐",
                output_text=cues[index].text,
            )
            for index in range(2)
        ]
        return build_semantic_request_bundle(cues, lines, decisions), lines

    def test_request_is_bounded_and_contains_no_timing_mutation_fields(self):
        bundle, _ = self._bundle()
        self.assertEqual(len(bundle["requests"]), 1)
        request = bundle["requests"][0]
        self.assertEqual(request["cue_ordinals"], [0, 1])
        self.assertEqual(request["proposed_canonical_span"], [0, 2])
        self.assertNotIn("start_ms", request)
        self.assertNotIn("end_ms", request)
        self.assertIn("do_not_propose_timestamps", request["instructions"]["hard_constraints"])

    def test_valid_response_can_only_partition_exact_canonical_stream(self):
        bundle, _ = self._bundle()
        request = bundle["requests"][0]
        split = len("소녀시대")
        response = {
            "schema_version": RESPONSE_SCHEMA,
            "policy_id": POLICY_ID,
            "request_bundle_sha256": bundle["bundle_sha256"],
            "model_identity": {
                "provider": "test-model-provider",
                "model_id": "semantic-model-test",
                "prompt_policy_id": "lexical-floor-test-prompt-1",
            },
            "decisions": [
                {
                    "request_id": request["request_id"],
                    "request_sha256": request["request_sha256"],
                    "verdict": "accept_partition",
                    "cue_content_spans": [[0, split], [split, len(request["canonical_lexical_stream"])]],
                }
            ],
        }
        validated = validate_semantic_response(bundle, response)
        targets = semantic_partition_targets(request, validated[request["request_id"]])
        self.assertEqual(targets, ["소녀시대", "말해봐"])

    def test_shadow_materialization_changes_only_text_and_never_publish_ready(self):
        cues = [cue(0, "so nyo shi dae"), cue(1, "ma le bwa")]
        lines = canonical("소녀시대", "말해봐")
        decisions = [
            MatchDecision(
                cue_ordinal=index,
                canonical_ordinal=0,
                score=0.2,
                action="review",
                reason="low_or_structurally_unsafe_similarity",
                cue_span=(0, 2),
                canonical_span=(0, 2),
                source_text=cues[index].text,
                canonical_text="소녀시대말해봐",
                output_text=cues[index].text,
            )
            for index in range(2)
        ]
        bundle = build_semantic_request_bundle(cues, lines, decisions)
        request = bundle["requests"][0]
        split = len("소녀시대")
        response = {
            "schema_version": RESPONSE_SCHEMA,
            "policy_id": POLICY_ID,
            "request_bundle_sha256": bundle["bundle_sha256"],
            "model_identity": {
                "provider": "test-model-provider",
                "model_id": "semantic-model-test",
                "prompt_policy_id": "lexical-floor-test-prompt-1",
            },
            "decisions": [
                {
                    "request_id": request["request_id"],
                    "request_sha256": request["request_sha256"],
                    "verdict": "accept_partition",
                    "cue_content_spans": [[0, split], [split, len(request["canonical_lexical_stream"])]],
                }
            ],
        }
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\nso nyo shi dae\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nma le bwa\n"
        )
        candidate, report = materialize_semantic_shadow_candidate(
            source,
            cues,
            {},
            bundle,
            response,
        )
        self.assertIn("소녀시대", candidate)
        self.assertIn("말해봐", candidate)
        self.assertIn("00:00:01,000 --> 00:00:02,000", candidate)
        self.assertIn("00:00:02,000 --> 00:00:03,000", candidate)
        self.assertFalse(report["publish_ready"])
        self.assertFalse(report["timing_authority_used"])
        self.assertTrue(report["timeline_unchanged"])

    def test_noncontiguous_partition_fails_closed(self):
        bundle, _ = self._bundle()
        request = bundle["requests"][0]
        response = {
            "schema_version": RESPONSE_SCHEMA,
            "policy_id": POLICY_ID,
            "request_bundle_sha256": bundle["bundle_sha256"],
            "model_identity": {
                "provider": "test-model-provider",
                "model_id": "semantic-model-test",
                "prompt_policy_id": "lexical-floor-test-prompt-1",
            },
            "decisions": [
                {
                    "request_id": request["request_id"],
                    "request_sha256": request["request_sha256"],
                    "verdict": "accept_partition",
                    "cue_content_spans": [[0, 2], [3, len(request["canonical_lexical_stream"])]],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "contiguous"):
            validate_semantic_response(bundle, response)

    def test_request_hash_is_recomputed_even_if_bundle_hash_is_recomputed(self):
        bundle, _ = self._bundle()
        request = bundle["requests"][0]
        tampered = dict(bundle)
        tampered_requests = [dict(item) for item in bundle["requests"]]
        tampered_requests[0]["cue_texts"] = ["tampered", "ma le bwa"]
        tampered["requests"] = tampered_requests
        from lyric_aligner.text.semantic_lexical import _stable_sha
        bare = dict(tampered)
        bare.pop("bundle_sha256", None)
        tampered["bundle_sha256"] = _stable_sha(bare)
        response = {
            "schema_version": RESPONSE_SCHEMA,
            "policy_id": POLICY_ID,
            "request_bundle_sha256": tampered["bundle_sha256"],
            "model_identity": {
                "provider": "test-model-provider",
                "model_id": "semantic-model-test",
                "prompt_policy_id": "lexical-floor-test-prompt-1",
            },
            "decisions": [
                {
                    "request_id": request["request_id"],
                    "request_sha256": request["request_sha256"],
                    "verdict": "abstain",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "request hash mismatch"):
            validate_semantic_response(tampered, response)

    def test_response_bound_to_different_request_bundle_fails(self):
        bundle, _ = self._bundle()
        response = {
            "schema_version": RESPONSE_SCHEMA,
            "policy_id": POLICY_ID,
            "request_bundle_sha256": "0" * 64,
            "model_identity": {
                "provider": "test-model-provider",
                "model_id": "semantic-model-test",
                "prompt_policy_id": "lexical-floor-test-prompt-1",
            },
            "decisions": [],
        }
        with self.assertRaisesRegex(ValueError, "different request bundle"):
            validate_semantic_response(bundle, response)


if __name__ == "__main__":
    unittest.main()
