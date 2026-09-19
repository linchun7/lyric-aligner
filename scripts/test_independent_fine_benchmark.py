import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lyric_aligner.audio.independent_fine import (
    INDEPENDENT_FINE_AUTHORITY,
    INDEPENDENT_FINE_CORRELATION_GROUP,
    INDEPENDENT_FINE_VERSION,
    IndependentFineCandidate,
    IndependentFineResult,
)
from scripts import v4_evaluate_independent_fine_benchmark as evaluator
from scripts import v4_run_independent_fine_benchmark as runner


class IndependentFineBenchmarkTests(unittest.TestCase):
    def test_contextual_variant_is_recorded_and_does_not_use_legacy_retriever(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);manifest,_=self._manifest(root)
            prediction=dict(version=runner.CONTEXTUAL_FINE_VERSION,top1={'source_start':11.},ambiguous=False)
            with (mock.patch.object(runner,'REPOSITORY_ROOT',root),
                  mock.patch.object(runner.librosa,'load',return_value=([0.]*4096,22050)),
                  mock.patch.object(runner,'extract_percussive_onset_features',return_value=object()),
                  mock.patch.object(runner,'retrieve_contextual_onset_window',return_value=prediction) as contextual,
                  mock.patch.object(runner,'retrieve_independent_onset_window') as legacy,
                  mock.patch.object(runner,'_implementation_identity',return_value={'implementation_revision':'a'*64})):
                result=runner.run_benchmark(manifest_path=manifest,expected_partition='calibration',observer='contextual')
            contextual.assert_called_once();legacy.assert_not_called()
            self.assertEqual(result['observer_variant'],'contextual')
            self.assertEqual(result['observer_version'],runner.CONTEXTUAL_FINE_VERSION)
            self.assertEqual(result['records'][0]['prediction'],prediction)
            self.assertFalse(result['automatic_mutation_allowed'])

    def test_contextual_observer_cannot_reuse_legacy_holdout_freeze(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);manifest,_=self._manifest(root,partition='holdout');protocol,_=self._holdout_protocol(root)
            with mock.patch.object(runner,'REPOSITORY_ROOT',root):
                with self.assertRaisesRegex(runner.IndependentFineBenchmarkRunError,'own frozen observer'):
                    runner.run_benchmark(manifest_path=manifest,expected_partition='holdout',observer='contextual',holdout_protocol_path=protocol)

    def test_contextual_identity_requires_revision_and_survives_evaluation(self):
        identity = {"observer_variant": "contextual", "observer_version": "1.1.1", "sample_rate": 22050,
                    "implementation": {"implementation_revision": "a" * 64}}
        result = evaluator.observer_identity(identity)
        self.assertEqual(result["observer_implementation_revision"], "a" * 64)
        self.assertEqual(evaluator.observer_identity(result), result)
        identity.pop("implementation")
        with self.assertRaisesRegex(ValueError, "identity is incomplete"):
            evaluator.observer_identity(identity)

    def test_contextual_lock_rejects_reverse_variant_and_changed_sample_rate_before_audio(self):
        for observer, sample_rate in (("independent", 22050), ("contextual", 8000)):
            with self.subTest(observer=observer, sample_rate=sample_rate), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest, _ = self._manifest(root, partition="holdout")
                protocol, payload = self._holdout_protocol(root)
                payload.pop("artifact_sha256")
                payload.update(observer_variant="contextual", observer_version=runner.CONTEXTUAL_FINE_VERSION,
                               observer_implementation_revision="a" * 64, observer_sample_rate=22050)
                payload["artifact_sha256"] = runner._sha_json(payload)
                protocol.write_text(json.dumps(payload), encoding="utf-8")
                with (mock.patch.object(runner, "REPOSITORY_ROOT", root),
                      mock.patch.object(runner, "_implementation_identity", return_value={"implementation_revision": "a" * 64}),
                      mock.patch.object(runner, "load_manifest", side_effect=AssertionError("AUDIO_READ_REACHED"))):
                    with self.assertRaisesRegex(runner.IndependentFineBenchmarkRunError, "observer"):
                        runner.run_benchmark(manifest_path=manifest, expected_partition="holdout",
                                             holdout_protocol_path=protocol, observer=observer, sample_rate=sample_rate)

    def _manifest(self, root: Path, *, partition: str = "calibration") -> tuple[Path, dict]:
        source = root / "private/source.wav"
        mix = root / "private/mix.wav"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        mix.write_bytes(b"mix")
        payload = {
            "schema_version": runner.MANIFEST_SCHEMA_VERSION,
            "partition": partition,
            "source_audit_sha256": "a" * 64,
            "record_count": 1,
            "records": [
                {
                    "case_id": "1" * 64,
                    "source_path": "private/source.wav",
                    "source_sha256": runner.sha256_file(source),
                    "mix_path": "private/mix.wav",
                    "mix_sha256": runner.sha256_file(mix),
                    "mix_start_s": 10.0,
                    "mix_end_s": 20.0,
                    "source_search_start_s": 8.0,
                    "source_search_end_s": 28.0,
                    "slopes": [1.0, 1.1],
                }
            ],
        }
        if partition == "holdout":
            payload.update({
                "source_audit_file_sha256": "d" * 64,
                "frozen_policy_sha256": "b" * 64,
                "pair_selection_sha256": "c" * 64,
            })
        payload["manifest_sha256"] = runner._sha_json(payload)
        path = root / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path, payload

    def _holdout_protocol(self, root: Path) -> tuple[Path, dict]:
        payload = {
            "schema_version": runner.HOLDOUT_PROTOCOL_SCHEMA_VERSION,
            "frozen_policy_sha256": "b" * 64,
            "pair_selection_sha256": "c" * 64,
            "holdout_affine_truth_audit_sha256": "a" * 64,
            "holdout_predictions_status": "not_run_at_protocol_freeze",
            "production_authoritative": False,
            "automatic_mutation_allowed": False,
        }
        payload["artifact_sha256"] = runner._sha_json(payload)
        path = root / "holdout_protocol.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path, payload

    def test_manifest_partition_and_audio_sha_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, payload = self._manifest(root)
            with mock.patch.object(runner, "REPOSITORY_ROOT", root):
                loaded = runner.load_manifest(path, expected_partition="calibration")
                self.assertEqual(loaded["manifest_sha256"], payload["manifest_sha256"])
                with self.assertRaisesRegex(runner.IndependentFineBenchmarkRunError, "partition mismatch"):
                    runner.load_manifest(path, expected_partition="holdout")
                (root / "private/source.wav").write_bytes(b"changed")
                with self.assertRaisesRegex(runner.IndependentFineBenchmarkRunError, "SHA mismatch"):
                    runner.load_manifest(path, expected_partition="calibration")

    def test_runner_is_observer_only_and_reuses_locked_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self._manifest(root)
            candidate = IndependentFineCandidate(
                source_start=11.0,
                source_end=22.0,
                source_center=16.5,
                estimated_slope=1.1,
                onset_score=0.9,
                multiband_score=0.8,
                fused_score=0.845,
                feature_agreement=2,
            )
            result = IndependentFineResult(
                version=INDEPENDENT_FINE_VERSION,
                authority=INDEPENDENT_FINE_AUTHORITY,
                correlation_group=INDEPENDENT_FINE_CORRELATION_GROUP,
                mix_start=10.0,
                mix_end=20.0,
                top1=candidate,
                top2=None,
                candidates=(candidate,),
                margin=0.845,
                ambiguous=False,
                min_score=0.48,
                min_margin=0.025,
            )
            fake_bundle = object()
            with (
                mock.patch.object(runner, "REPOSITORY_ROOT", root),
                mock.patch.object(runner.librosa, "load", return_value=([0.0] * 4096, 22050)),
                mock.patch.object(runner, "extract_percussive_onset_features", return_value=fake_bundle),
                mock.patch.object(runner, "retrieve_independent_onset_window", return_value=result),
                mock.patch.object(runner, "_implementation_identity", return_value={"files": [], "implementation_revision": "a" * 64}),
            ):
                artifact = runner.run_benchmark(
                    manifest_path=path,
                    expected_partition="calibration",
                )
            self.assertEqual(artifact["aligned_count"], 1)
            self.assertEqual(artifact["prediction_coverage"], 1.0)
            self.assertEqual(artifact["source_audit_sha256"], "a" * 64)
            self.assertFalse(artifact["automatic_mutation_allowed"])
            self.assertFalse(artifact["subtitle_mutation_performed"])
            self.assertEqual(artifact["records"][0]["prediction"]["top1"]["estimated_slope"], 1.1)

    def test_holdout_runner_requires_and_binds_frozen_protocol(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, _ = self._manifest(root, partition="holdout")
            protocol_path, protocol_payload = self._holdout_protocol(root)
            candidate = IndependentFineCandidate(
                source_start=11.0,
                source_end=21.0,
                source_center=16.0,
                estimated_slope=1.0,
                onset_score=0.9,
                multiband_score=0.9,
                fused_score=0.9,
                feature_agreement=2,
            )
            result = IndependentFineResult(
                version=INDEPENDENT_FINE_VERSION,
                authority=INDEPENDENT_FINE_AUTHORITY,
                correlation_group=INDEPENDENT_FINE_CORRELATION_GROUP,
                mix_start=10.0,
                mix_end=20.0,
                top1=candidate,
                top2=None,
                candidates=(candidate,),
                margin=0.9,
                ambiguous=False,
                min_score=0.48,
                min_margin=0.025,
            )
            fake_bundle = object()
            with (
                mock.patch.object(runner, "REPOSITORY_ROOT", root),
                mock.patch.object(runner.librosa, "load", return_value=([0.0] * 4096, 22050)),
                mock.patch.object(runner, "extract_percussive_onset_features", return_value=fake_bundle),
                mock.patch.object(runner, "retrieve_independent_onset_window", return_value=result),
                mock.patch.object(runner, "_implementation_identity", return_value={"files": [], "implementation_revision": "a" * 64}),
            ):
                with self.assertRaisesRegex(runner.IndependentFineBenchmarkRunError, "requires frozen holdout protocol"):
                    runner.run_benchmark(manifest_path=manifest_path, expected_partition="holdout")
                artifact = runner.run_benchmark(
                    manifest_path=manifest_path,
                    expected_partition="holdout",
                    holdout_protocol_path=protocol_path,
                )
            self.assertEqual(artifact["holdout_protocol_sha256"], protocol_payload["artifact_sha256"])
            self.assertEqual(artifact["frozen_policy_sha256"], "b" * 64)
            self.assertEqual(artifact["pair_selection_sha256"], "c" * 64)
            self.assertEqual(artifact["source_audit_file_sha256"], "d" * 64)
            self.assertFalse(artifact["automatic_mutation_allowed"])

    def test_evaluator_uses_separate_truth_and_reports_start_and_slope_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = {
                "schema_version": runner.RUN_SCHEMA_VERSION,
                "authority": runner.BENCHMARK_AUTHORITY,
                "partition": "calibration",
                "source_audit_sha256": "a" * 64,
                "automatic_mutation_allowed": False,
                "subtitle_mutation_performed": False,
                "records": [
                    {
                        "case_id": "1" * 64,
                        "source_sha256": "2" * 64,
                        "mix_sha256": "3" * 64,
                        "status": "aligned",
                        "prediction": {
                            "top1": {
                                "source_start": 11.05,
                                "estimated_slope": 1.11,
                                "fused_score": 0.8,
                            },
                            "margin": 0.1,
                            "ambiguous": False,
                        },
                    }
                ],
            }
            run["artifact_sha256"] = evaluator._sha_json(run)
            truth = {
                "schema_version": evaluator.TRUTH_SCHEMA_VERSION,
                "partition": "calibration",
                "source_audit_sha256": "a" * 64,
                "record_count": 1,
                "records": [
                    {
                        "case_id": "1" * 64,
                        "source_sha256": "2" * 64,
                        "mix_sha256": "3" * 64,
                        "source_audit_sha256": "a" * 64,
                        "pair_index": 1,
                        "window_index": 1,
                        "expected_source_start_s": 11.0,
                        "expected_slope": 1.1,
                        "truth_basis": "known_uniform_transform_fixture",
                    }
                ],
            }
            truth["artifact_sha256"] = evaluator._sha_json(truth)
            run_path = root / "run.json"
            truth_path = root / "truth.json"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            truth_path.write_text(json.dumps(truth), encoding="utf-8")
            artifact = evaluator.evaluate(run_path=run_path, truth_path=truth_path)
            self.assertAlmostEqual(artifact["source_start_abs_error_ms"]["median"], 50.0)
            self.assertAlmostEqual(artifact["slope_abs_error"]["median"], 0.01)
            self.assertEqual(artifact["prediction_coverage"], 1.0)
            self.assertEqual(artifact["source_audit_sha256"], "a" * 64)
            self.assertEqual(artifact["cases"][0]["pair_index"], 1)
            self.assertEqual(artifact["cases"][0]["window_index"], 1)
            self.assertFalse(artifact["automatic_mutation_allowed"])

            truth["source_audit_sha256"] = "b" * 64
            truth["records"][0]["source_audit_sha256"] = "b" * 64
            truth.pop("artifact_sha256")
            truth["artifact_sha256"] = evaluator._sha_json(truth)
            truth_path.write_text(json.dumps(truth), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluator.IndependentFineBenchmarkEvaluationError,
                "source audit SHA mismatch",
            ):
                evaluator.evaluate(run_path=run_path, truth_path=truth_path)

    def test_holdout_evaluator_requires_and_propagates_blind_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lineage = {
                "source_audit_file_sha256": "d" * 64,
                "frozen_policy_sha256": "b" * 64,
                "pair_selection_sha256": "c" * 64,
            }
            run = {
                "schema_version": runner.RUN_SCHEMA_VERSION,
                "authority": runner.BENCHMARK_AUTHORITY,
                "partition": "holdout",
                "source_audit_sha256": "a" * 64,
                **lineage,
                "holdout_protocol_sha256": "e" * 64,
                "automatic_mutation_allowed": False,
                "subtitle_mutation_performed": False,
                "records": [{
                    "case_id": "1" * 64,
                    "source_sha256": "2" * 64,
                    "mix_sha256": "3" * 64,
                    "status": "aligned",
                    "prediction": {"top1": {"source_start": 11.01, "estimated_slope": 1.0, "fused_score": 0.9}, "margin": 0.1, "ambiguous": False},
                }],
            }
            run["artifact_sha256"] = evaluator._sha_json(run)
            truth = {
                "schema_version": evaluator.TRUTH_SCHEMA_VERSION,
                "partition": "holdout",
                "source_audit_sha256": "a" * 64,
                **lineage,
                "record_count": 1,
                "records": [{
                    "case_id": "1" * 64,
                    "source_sha256": "2" * 64,
                    "mix_sha256": "3" * 64,
                    "source_audit_sha256": "a" * 64,
                    "pair_index": 1,
                    "window_index": 1,
                    "expected_source_start_s": 11.0,
                    "expected_slope": 1.0,
                    "truth_basis": "fixture",
                }],
            }
            truth["artifact_sha256"] = evaluator._sha_json(truth)
            run_path = root / "run.json"
            truth_path = root / "truth.json"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            truth_path.write_text(json.dumps(truth), encoding="utf-8")
            artifact = evaluator.evaluate(run_path=run_path, truth_path=truth_path)
            self.assertEqual(artifact["holdout_protocol_sha256"], "e" * 64)
            self.assertEqual(artifact["frozen_policy_sha256"], "b" * 64)
            self.assertEqual(artifact["pair_selection_sha256"], "c" * 64)
            truth["frozen_policy_sha256"] = "f" * 64
            truth.pop("artifact_sha256")
            truth["artifact_sha256"] = evaluator._sha_json(truth)
            truth_path.write_text(json.dumps(truth), encoding="utf-8")
            with self.assertRaisesRegex(evaluator.IndependentFineBenchmarkEvaluationError, "frozen_policy_sha256 mismatch"):
                evaluator.evaluate(run_path=run_path, truth_path=truth_path)


if __name__ == "__main__":
    unittest.main()
