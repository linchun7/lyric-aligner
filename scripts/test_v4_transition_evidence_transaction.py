import json
import tempfile
import unittest
from pathlib import Path
from lyric_aligner.contracts.artifacts import sha256_file

from lyric_aligner.review.transition_evidence_transaction import (
    TransitionAuthorizationError,
    authorize_transition_review,
)


class TransitionEvidenceTransactionTests(unittest.TestCase):
    def test_missing_positional_artifact_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            def write(name, payload):
                path = out / name
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path
            source = out / "source.srt"
            source.write_text("synthetic source witness", encoding="utf-8")
            task = write("task.json", {"task_fingerprint_sha256": "t" * 64,
                "inputs": {"source_srt": {"path": "source.srt", "sha256": sha256_file(source)}}})
            run = write("run.json", {"transitions": [{"left_occurrence_id": "left",
                "right_occurrence_id": "right", "nominal_boundary": 1}]})
            artifact = write("artifact.json", {"artifact_id": "a" * 64})
            binding = {"task_fingerprint_sha256": "t" * 64, "run_sha256": sha256_file(run),
                "run_artifact_id": "a" * 64, "run_artifact_sha256": sha256_file(artifact)}
            template = write("template.json", {"task_fingerprint_sha256": "t" * 64,
                "review_items": [{"issue": {"kind": "transition_ambiguity",
                    "code": "ambiguous_source_occurrence", "candidate_id": "candidate",
                    "left_occurrence_id": "left", "right_occurrence_id": "right"}}]})
            lexical = write("lexical.json", {**binding, "source_srt_sha256": sha256_file(source),
                "transitions": [{"issue_candidate_id": "candidate", "recommendation": "clear_candidate"}]})
            positional = {**binding, "authority": {"shadow_only": True,
                "automatic_review_decision": False, "timing_mutation_performed": False},
                "timing_mutation_performed": False, "evidence": [{"transition_index": 1,
                    "nominal_boundary": 1, "recommendation": {"action": "clear_sequential_advisory"}}]}
            valid = write("positional.json", positional)
            kwargs = dict(task_manifest=task, run_path=run, run_artifact_path=artifact,
                          template_path=template, lexical_path=lexical, repository_root=out)
            # Positive control proves this fixture reaches real authorization.
            report = authorize_transition_review(**kwargs, positional_path=valid,
                decisions_out=out / "valid-decisions.json", report_out=out / "valid-report.json")
            self.assertEqual(report["counts"]["resolved_clear"], 1)
            positional.pop("run_artifact_sha256")
            malformed = write("positional-missing-run-artifact-sha.json", positional)
            with self.assertRaisesRegex(
                TransitionAuthorizationError, "run_artifact_sha256 mismatch"
            ):
                authorize_transition_review(
                    **kwargs,
                    positional_path=malformed,
                    decisions_out=out / "decisions.json",
                    report_out=out / "report.json",
                )


if __name__ == "__main__":
    unittest.main()
