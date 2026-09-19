import hashlib
import unittest
from unittest.mock import patch

from lyric_aligner.evaluation.machine_boundary_consensus import (
    MachineBoundaryConsensusError,
    MachineConsensusPolicy,
    _validate_response_record,
    build_machine_boundary_consensus,
)


def _case_id(label, index):
    return hashlib.sha256(f"{label}-{index}".encode()).hexdigest()


def _record(label, index, *, internal=False):
    segments = (
        [
            {"lrc_index": 0, "text": f"{label} {index} left"},
            {"lrc_index": 1, "text": f"{label} {index} right"},
        ]
        if internal
        else [{"lrc_index": 0, "text": f"{label} {index}"}]
    )
    start = 10000 + index * 2000
    end = start + 1200
    return {
        "case_id": _case_id(label, index),
        "track": f"track-{index:02d}",
        "cue_number": index + 1,
        "start_ms": start,
        "end_ms": end,
        "clip_start_ms": start - 1000,
        "clip_end_ms": end + 1000,
        "segments": segments,
        "segment_count": len(segments),
        "internal_boundary_index": 1 if internal else None,
    }


def _lock(audio_path, audio_sha):
    return {
        "lock_sha256": "a" * 64,
        "inputs": {
            "language_scope": "zh",
            "final_audio_path": str(audio_path),
            "final_audio_sha256": audio_sha,
        },
        "populations": {
            "outer": {"records": [_record("outer", i) for i in range(30)]},
            "internal": {"records": [_record("internal", i, internal=True) for i in range(30)]},
        },
    }


class _Profile:
    def __init__(self, profile_id):
        self.profile_id = profile_id

    def resolve(self, repository_root, *, language):
        return {
            "profile_id": self.profile_id,
            "backend_id": self.profile_id + "-backend",
            "backend_version": "v1",
            "family": self.profile_id + "-family",
            "correlation_group": self.profile_id + "-group",
            "model_id": self.profile_id + "-model",
            "model_revision": self.profile_id + "-revision",
            "window_policy_id": "window-v1",
            "command": "unused",
        }


class MachineBoundaryConsensusTests(unittest.TestCase):
    def test_builds_all_90_machine_only_points_and_marks_consensus(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fixture-audio")
            audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            lock = _lock(audio, audio_sha)

            def get_profile(profile_id):
                return _Profile(profile_id)

            def predict(**kwargs):
                kind = kwargs["boundary_kind"]
                profile_id = kwargs["resolved_profile"]["profile_id"]
                delta = 20 if profile_id == "profile-b" else 0
                result = {}
                for case in kwargs["locked_cases"]:
                    if kind == "start":
                        value = int(case["start_ms"]) + 50 + delta
                    elif kind == "end":
                        value = int(case["end_ms"]) - 50 + delta
                    else:
                        value = int(round((int(case["start_ms"]) + int(case["end_ms"])) / 2)) + delta
                    result[f"{case['case_id']}:{kind}"] = value
                return result

            acoustic_calls = []

            def acoustic(_path, *, editor_ms, boundary_kind):
                acoustic_calls.append((int(editor_ms), boundary_kind))
                return {
                    "available": True,
                    "boundary_ms": int(editor_ms) + 5,
                    "confidence": 0.8,
                    "uncertainty_ms": 60,
                }

            with patch(
                "lyric_aligner.evaluation.machine_boundary_consensus.analyze_final_mix_boundary",
                side_effect=acoustic,
            ):
                artifact = build_machine_boundary_consensus(
                    full_lock=lock,
                    repository_root=temporary,
                    profile_ids=("profile-a", "profile-b"),
                    profile_getter=get_profile,
                    prediction_executor=predict,
                )

            self.assertEqual(artifact["record_count"], 90)
            self.assertEqual(len({row["id"] for row in artifact["records"]}), 90)
            self.assertEqual(
                artifact["status_counts"],
                {"aligner_consensus": 30, "high_consensus": 60},
            )
            self.assertEqual(artifact["authority"], "machine_candidate_only_never_human_gold")
            self.assertNotIn("gold", str(artifact["records"]).lower())

            first_start = next(row for row in artifact["records"] if row["boundary_kind"] == "start")
            self.assertEqual(first_start["acoustic_search_center_ms"], 10000)
            self.assertEqual(first_start["candidate_ms"], 10060)
            self.assertEqual(first_start["acoustic_role"], "independent_outer_corroborator")
            first_internal = next(row for row in artifact["records"] if row["boundary_kind"] == "internal")
            self.assertEqual(first_internal["acoustic_search_center_ms"], 10600)
            self.assertEqual(first_internal["candidate_ms"], 10610)
            self.assertEqual(first_internal["consensus_status"], "aligner_consensus")
            self.assertEqual(first_internal["acoustic_role"], "nonsemantic_internal_context_diagnostic")
            self.assertEqual(acoustic_calls[0], (10000, "start"))

    def test_large_direct_disagreement_is_never_high_consensus(self):
        policy = MachineConsensusPolicy()
        self.assertLess(policy.direct_high_agreement_ms, policy.hard_disagreement_ms)

    def test_internal_prediction_outside_editor_owner_is_retained_when_inside_locked_window(self):
        request = {
            "record_id": "a" * 64 + ":internal",
            "boundary_kind": "internal",
            "lexical_units_sha256": "b" * 64,
            "mix_window_ms": [9000, 12200],
            "row_interval_ms": [10000, 11200],
        }
        payload = {
            "record_id": request["record_id"],
            "boundary_kind": "internal",
            "lexical_units_sha256": request["lexical_units_sha256"],
            "mix_window_ms": request["mix_window_ms"],
            "status": "aligned",
            "predicted_ms": 11500,
        }
        self.assertEqual(_validate_response_record(payload, request), 11500)

        payload["predicted_ms"] = 13000
        with self.assertRaises(MachineBoundaryConsensusError):
            _validate_response_record(payload, request)

    def test_outer_prediction_collapsed_to_padded_window_edge_becomes_missing(self):
        request = {
            "record_id": "a" * 64 + ":end",
            "boundary_kind": "end",
            "lexical_units_sha256": "b" * 64,
            "mix_window_ms": [9000, 12200],
            "row_interval_ms": [10000, 10700],
        }
        payload = {
            "record_id": request["record_id"],
            "boundary_kind": "end",
            "lexical_units_sha256": request["lexical_units_sha256"],
            "mix_window_ms": request["mix_window_ms"],
            "status": "aligned",
            "predicted_ms": 12180,
        }
        self.assertIsNone(_validate_response_record(payload, request))
        payload["predicted_ms"] = 10740
        self.assertEqual(_validate_response_record(payload, request), 10740)


if __name__ == "__main__":
    unittest.main()
