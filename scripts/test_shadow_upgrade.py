import csv
from contextlib import ExitStack
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner import __version__
from lyric_aligner.assets.bindings import bindings_from_payload
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.contracts.artifacts import build_artifact_manifest, sha256_file
from lyric_aligner.alignment.source_observer import json_sha
from lyric_aligner.srt import Cue, cue_id, parse_srt_strict
from scripts.task_contract import build_task_manifest
from scripts.v4_shadow_upgrade import run_shadow_job
from scripts.v4_upgrade_subtitles import run_job, validate_pair


class ShadowUpgradeTests(unittest.TestCase):
    """Real small lineage fixture for the experimental shadow-only runner."""

    CANONICAL = ("alpha", "bravo", "charlie", "delta", "echo")
    BASELINE = ((1000, 1100), (1200, 1300), (1400, 1500), (1600, 1700), (1800, 1900))

    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]
        private = self.repository / "private"
        private.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=private, prefix="test_shadow_upgrade_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = self.root / "input"
        self.lyrics = self.inputs / "lyrics"
        self.sources = self.inputs / "sources"
        for directory in (self.lyrics, self.sources, self.root / "qa"):
            directory.mkdir(parents=True, exist_ok=True)

        self.source_srt = self.inputs / "baseline.srt"
        self._write_srt(self.source_srt, self.BASELINE)
        self.baseline_srt_bytes = self.source_srt.read_bytes()
        self.report = self.root / "baseline.csv"
        self.mix_audio = self.inputs / "mix.wav"
        self.mix_audio.write_bytes(b"shadow-fixture-mix")
        self.song_list = self.inputs / "songs.txt"
        self.song_list.write_text("00:00 Artist - Signal\n", encoding="utf-8")
        self.lyric = self.lyrics / "Artist - Signal.lrc"
        self.lyric.write_text(
            "".join(f"[00:0{index}.00]{text}\n" for index, text in enumerate(self.CANONICAL)),
            encoding="utf-8",
        )
        self.source_audio = self.sources / "Artist - Signal.wav"
        self.source_audio.write_bytes(b"shadow-fixture-source")

        manifest = build_task_manifest(
            self.repository,
            "shadow-fixture",
            source_srt=self.source_srt,
            audio=self.mix_audio,
            song_list=self.song_list,
            lyrics_dir=self.lyrics,
            source_audio_dir=self.sources,
        )
        self.fingerprint = manifest["task_fingerprint_sha256"]
        self.manifest = self.root / "qa" / "task_manifest.json"
        self._write_json(self.manifest, manifest)

        assets = resolve_assets(
            song_list=self.song_list,
            lyrics_dir=self.lyrics,
            source_audio_dir=self.sources,
        )
        assets["task_fingerprint_sha256"] = self.fingerprint
        assets["algorithm_version"] = __version__
        self.assets = self.root / "assets.json"
        self._write_json(self.assets, assets)
        self.assets_artifact = self.root / "assets.artifact.json"
        self._write_json(
            self.assets_artifact,
            build_artifact_manifest(
                task_fingerprint_sha256=self.fingerprint,
                stage="asset_resolution",
                algorithm_version=__version__,
                outputs=(("track_assets", self.assets),),
            ),
        )
        self.binding = bindings_from_payload(assets)[0]
        self._write_report()

        self.fine = self.root / "fine.json"
        mapping = {
            "kind": "AFFINE",
            "intercept": 0.0,
            "base_slope": 1.0,
            "breakpoints": [],
            "slope_deltas": [],
        }
        self._write_json(
            self.fine,
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": self.fingerprint,
                "occurrence_id": self.binding.occurrence_id,
                "track_id": self.binding.track_id,
                "source_audio_sha256": self.binding.source_audio_sha256,
                "mix_audio_sha256": manifest["inputs"]["audio"]["sha256"],
                "canonical_selection_sha256": self.binding.canonical_selection_sha256,
                "upstream_asset_artifact_id": json.loads(
                    self.assets_artifact.read_text(encoding="utf-8")
                )["artifact_id"],
                "result": {"timewarp": {"blocked": False, "mapping": mapping}},
            },
        )
        self.fine_artifact = self.root / "fine.artifact.json"
        self._write_json(
            self.fine_artifact,
            build_artifact_manifest(
                task_fingerprint_sha256=self.fingerprint,
                stage="fine_audio_alignment",
                algorithm_version=__version__,
                outputs=(("fine_alignment", self.fine),),
                upstream_artifact_ids=(
                    json.loads(self.assets_artifact.read_text(encoding="utf-8"))["artifact_id"],
                ),
            ),
        )
        self.run = self.root / "run.json"
        self._write_json(
            self.run,
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": self.fingerprint,
                "occurrences": [
                    {
                        "occurrence_id": self.binding.occurrence_id,
                        "fine_path": str(self.fine),
                        "fine_artifact_path": str(self.fine_artifact),
                        "mapping_source": "fine",
                        "mapping_blocked": False,
                        "reference_retime": False,
                        "primary_interval": [0.0, 10.0],
                    }
                ],
                "review_resolution": {
                    "schema_version": "1.0",
                    "base_run_artifact_id": "shadow-fixture-base",
                    "remaining_issue_count": 0,
                },
            },
        )
        self.run_artifact = self.root / "run.artifact.json"
        self._write_json(
            self.run_artifact,
            build_artifact_manifest(
                task_fingerprint_sha256=self.fingerprint,
                stage="review_resolution",
                algorithm_version=__version__,
                outputs=(("v4_reviewed_run", self.run),),
                upstream_artifact_ids=(
                    json.loads(self.assets_artifact.read_text(encoding="utf-8"))["artifact_id"],
                    json.loads(self.fine_artifact.read_text(encoding="utf-8"))["artifact_id"],
                    "shadow-fixture-base",
                ),
            ),
        )
        self.model = self.root / "local-model"
        self.model.mkdir()
        (self.model / "model.bin").write_bytes(b"fixture-model")
        self.job = self.root / "shadow-job.json"
        self._write_shadow_job(self.job)

    @staticmethod
    def _write_json(path, payload):
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _clock(ms):
        seconds, millis = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"00:{minutes:02d}:{seconds:02d},{millis:03d}"

    def _write_srt(self, path, intervals, *, texts=None):
        texts = self.CANONICAL if texts is None else texts
        blocks = [
            f"{position}\n{self._clock(start)} --> {self._clock(end)}\n{text}\n"
            for position, ((start, end), text) in enumerate(zip(intervals, texts), 1)
        ]
        Path(path).write_text("\n".join(blocks), encoding="utf-8", newline="\n")

    def _write_report(self):
        fields = (
            "start_ms",
            "end_ms",
            "text",
            "occurrence_id",
            "canonical_line_index",
            "task_fingerprint_sha256",
        )
        with self.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for index, ((start, end), text) in enumerate(zip(self.BASELINE, self.CANONICAL)):
                writer.writerow(
                    {
                        "start_ms": start,
                        "end_ms": end,
                        "text": text,
                        "occurrence_id": (
                            self.binding.occurrence_id
                            if hasattr(self, "binding")
                            else "placeholder-until-assets-resolve"
                        ),
                        "canonical_line_index": index,
                        "task_fingerprint_sha256": self.fingerprint,
                    }
                )

    @staticmethod
    def _bound(path):
        path = Path(path)
        return {"path": str(path), "sha256": sha256_file(path)}

    def _write_shadow_job(self, path, *, mutate=None):
        job = {
            "schema_version": "subtitle-shadow-upgrade-job-1.0",
            "execution_mode": "shadow",
            "task_manifest": self._bound(self.manifest),
            "report": self._bound(self.report),
            "srt": self._bound(self.source_srt),
            "assets": self._bound(self.assets),
            "run": self._bound(self.run),
            "assets_artifact": self._bound(self.assets_artifact),
            "run_artifact": self._bound(self.run_artifact),
            "source_asr": {"model_path": str(self.model)},
            "source_cache_dir": str(self.root / "source-cache"),
            "sources": [
                {
                    "occurrence_id": self.binding.occurrence_id,
                    "fine": self._bound(self.fine),
                    "fine_artifact": self._bound(self.fine_artifact),
                }
            ],
        }
        if mutate is not None:
            mutate(job)
        self._write_json(path, job)

    def _fresh_source_observer(self, **kwargs):
        self.assertEqual(kwargs["audio_path"], self.source_audio)
        self.assertEqual(kwargs["audio_sha256"], self.binding.source_audio_sha256)
        # Cue 2 and cue 3 conflict after projection.  The complete optimizer
        # must keep cue 2, then can safely choose fresh source intervals for
        # cue 3 through cue 5.  This exercises actual packet->interval DP.
        source_intervals = ((2000, 2100), (2150, 2300), (2250, 2450), (2500, 2600), (2650, 2750))
        result = {
            "status": "observed",
            "authority": "observed_transcript_only",
            "audio_basis": "source",
            "source_audio_sha256": self.binding.source_audio_sha256,
            "cache_key_sha256": "fresh-whole-source-observer-fixture",
            "search_domain": {"start_ms": 0, "end_ms": 10_000, "domain_id": "whole-source"},
            "words": [
                {
                    "text": text,
                    "start_ms": start,
                    "end_ms": end,
                    "window_id": "whole_source",
                }
                for text, (start, end) in zip(self.CANONICAL, source_intervals)
            ],
        }
        result["artifact_sha256"] = json_sha(result)
        return result

    def _source_observation(self, words):
        result = {
            "status": "observed",
            "authority": "observed_transcript_only",
            "audio_basis": "source",
            "source_audio_sha256": self.binding.source_audio_sha256,
            "cache_key_sha256": "edge-provenance-observer-fixture",
            "search_domain": {"start_ms": 0, "end_ms": 10_000, "domain_id": "whole-source"},
            "words": words,
        }
        result["artifact_sha256"] = json_sha(result)
        return result

    def _rebind_fine_mapping(self, mapping):
        """Change a synthetic mapping while retaining valid artifact lineage."""

        fine = json.loads(self.fine.read_text(encoding="utf-8"))
        fine["result"]["timewarp"]["mapping"] = mapping
        self._write_json(self.fine, fine)
        assets_id = json.loads(self.assets_artifact.read_text(encoding="utf-8"))["artifact_id"]
        self._write_json(
            self.fine_artifact,
            build_artifact_manifest(
                task_fingerprint_sha256=self.fingerprint,
                stage="fine_audio_alignment",
                algorithm_version=__version__,
                outputs=(("fine_alignment", self.fine),),
                upstream_artifact_ids=(assets_id,),
            ),
        )
        fine_id = json.loads(self.fine_artifact.read_text(encoding="utf-8"))["artifact_id"]
        self._write_json(
            self.run_artifact,
            build_artifact_manifest(
                task_fingerprint_sha256=self.fingerprint,
                stage="review_resolution",
                algorithm_version=__version__,
                outputs=(("v4_reviewed_run", self.run),),
                upstream_artifact_ids=(assets_id, fine_id, "shadow-fixture-base"),
            ),
        )
        self._write_shadow_job(self.job)

    def _reset_multilingual_duplicate_fixture(self):
        """Rebuild the real lineage fixture with a Korean repeated lyric."""

        self.CANONICAL = ("시작", "반복", "반복", "반복", "반복", "반복", "반복", "끝")
        self.BASELINE = tuple((100 + index * 500, 400 + index * 500)
                              for index in range(len(self.CANONICAL)))
        # ``setUp`` intentionally recreates all bound files and artifacts, so
        # this remains a normal shadow-job test rather than a hand-built
        # packet fixture.  The original temporary directory remains cleanup
        # owned by unittest.
        self.setUp()
        return [
            {"text": text, "start_ms": 1500 + index * 350,
             "end_ms": 1750 + index * 350, "window_id": "whole_source"}
            for index, text in enumerate(self.CANONICAL)
        ]

    @staticmethod
    def _sequence_records(ledger):
        return [record for record in ledger["records"] if record.get("source_sequence_promotion")]

    def test_multilingual_duplicate_sequence_promotion_materializes_regular_shadow_srt(self):
        from lyric_aligner.alignment import source_packets

        words = self._reset_multilingual_duplicate_fixture()
        output = self.root / "multilingual-source-sequence"
        captured_n_best = []
        original = source_packets.build_source_packet_candidates

        def capture_packet(**kwargs):
            captured_n_best.append(kwargs["n_best"])
            return original(**kwargs)

        with patch("lyric_aligner.alignment.source_packets.build_source_packet_candidates",
                   side_effect=capture_packet):
            result = run_shadow_job(self.job, output,
                                    source_observer=lambda **_kwargs: self._source_observation(words))
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        sequence = ledger["source_sequences"][0]
        promoted = self._sequence_records(ledger)
        _rows, cues = validate_pair(output / "shadow.csv", output / "shadow.srt")

        self.assertEqual(sequence["status"], "complete")
        self.assertEqual(sequence["owned_target_cue_count"], len(self.CANONICAL))
        self.assertEqual(sequence["source_packet_count"], len(self.CANONICAL))
        self.assertEqual(captured_n_best, [1024] * len(self.CANONICAL))
        self.assertEqual(sequence["canonical_ownership_unresolved_count"], 0)
        self.assertEqual(sequence["source_packet_truncated_count"], 0)
        self.assertGreater(sequence["promotion_count"], 0)
        self.assertTrue(promoted)
        for record in promoted:
            position = record["position"]
            self.assertEqual(record["source_sequence_promotion"]["reason"],
                             "canonical_duplicate_resolved_by_all_optimal_source_order")
            self.assertTrue(record["selected_candidate_id"].startswith(str(position) + ":"))
            self.assertFalse(record["selected_candidate_id"].endswith(":KEEP"))
            self.assertNotEqual((cues[position - 1].start_ms, cues[position - 1].end_ms),
                                self.BASELINE[position - 1])
        self.assertEqual(result["source_sequences"][0]["promotion_count"], len(promoted))

    def test_multilingual_duplicate_without_complete_order_stays_unpromoted(self):
        words = self._reset_multilingual_duplicate_fixture()
        del words[4]  # one repeated source occurrence is absent
        output = self.root / "multilingual-incomplete-order"
        run_shadow_job(self.job, output, source_observer=lambda **_kwargs: self._source_observation(words))
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["source_sequences"][0]["status"], "complete")
        self.assertEqual(ledger["source_sequences"][0]["promotion_count"], 0)
        ambiguous = [record for record in ledger["records"]
                     if record.get("source_packet", {}).get("selection_reason")
                     == "ambiguous_canonical_packet_identity"]
        self.assertTrue(ambiguous)
        self.assertTrue(all(record["selected_candidate_id"].endswith(":KEEP") for record in ambiguous))

    def test_truncated_or_resource_limited_sequence_cannot_promote_duplicate_packet(self):
        from lyric_aligner.alignment import source_packets

        for label, limiter in (("truncated", "packet"), ("resource", "sequence")):
            with self.subTest(label=label):
                words = self._reset_multilingual_duplicate_fixture()
                output = self.root / ("multilingual-" + label)
                if limiter == "packet":
                    original = source_packets.build_source_packet_candidates

                    def truncated_packet(**kwargs):
                        packet = original(**kwargs)
                        packet["candidates_truncated"] = True
                        packet["coverage"]["candidate_count_before_truncation"] = len(packet["candidates"]) + 1
                        return packet

                    context = patch("lyric_aligner.alignment.source_packets.build_source_packet_candidates",
                                    side_effect=truncated_packet)
                else:
                    context = patch("lyric_aligner.alignment.source_sequence.MAX_VERTICES", 1)
                with context:
                    run_shadow_job(self.job, output,
                                   source_observer=lambda **_kwargs: self._source_observation(words))
                ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
                sequence = ledger["source_sequences"][0]
                self.assertEqual(sequence["promotion_count"], 0)
                self.assertEqual(sequence["status"],
                                 "incomplete_lattice" if limiter == "packet" else "source_sequence_resource_limit")
                ambiguous = [record for record in ledger["records"]
                             if record.get("source_packet", {}).get("selection_reason")
                             == "ambiguous_canonical_packet_identity"]
                self.assertTrue(ambiguous)
                self.assertTrue(all(record["selected_candidate_id"].endswith(":KEEP") for record in ambiguous))

    def test_observer_self_hash_conflict_fails_before_duplicate_sequence_admission(self):
        words = self._reset_multilingual_duplicate_fixture()
        output = self.root / "observer-self-hash-conflict"

        def stale_observer(**_kwargs):
            observation = self._source_observation(words)
            observation["artifact_sha256"] = "0" * 64
            return observation

        with self.assertRaisesRegex(ValueError, "verified self hash"):
            run_shadow_job(self.job, output, source_observer=stale_observer)
        self.assertFalse(output.exists())

    def test_fresh_source_packet_joint_selection_writes_only_shadow_and_readback_matches_selection(self):
        output = self.root / "shadow-result"
        result = run_shadow_job(self.job, output, source_observer=self._fresh_source_observer)

        self.assertEqual(result["execution_mode"], "shadow")
        self.assertFalse(result["publish_ready"])
        self.assertEqual(result["text_changed_count"], 0)
        self.assertEqual(self.source_srt.read_bytes(), self.baseline_srt_bytes)
        self.assertFalse((output / "final.srt").exists())
        self.assertTrue((output / "shadow.srt").is_file())
        self.assertTrue((output / "shadow.csv").is_file())
        self.assertFalse((output / "source_context_hubertfa_ledger.json").exists())
        self.assertFalse((output / "hfa_overlay.srt").exists())

        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        records = {item["position"]: item for item in ledger["records"]}
        self.assertTrue(records[2]["interval_candidates"])
        self.assertTrue(any(not record["selected_candidate_id"].endswith(":KEEP") for record in records.values()))

        selection = json.loads((output / "selection.json").read_text(encoding="utf-8"))
        _, readback = validate_pair(output / "shadow.csv", output / "shadow.srt")
        self.assertEqual(
            [(cue.start_ms, cue.end_ms) for cue in readback],
            [tuple(interval) for interval in selection["selected_intervals_ms"]],
        )
        self.assertEqual(
            [(cue.start_ms, cue.end_ms) for cue in parse_srt_strict(output / "shadow.srt")],
            [tuple(interval) for interval in selection["selected_intervals_ms"]],
        )
        selected = [tuple(interval) for interval in selection["selected_intervals_ms"]]
        self.assertTrue(all(start < end for start, end in selected))
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(selected, selected[1:])))
        self.assertGreaterEqual(
            sum(current != baseline for current, baseline in zip(selected, self.BASELINE)),
            2,
        )
        # The two deliberately conflicting complete source intervals cannot
        # simultaneously survive joint selection, independent of whether the
        # optimiser now uses a one-edge candidate for either cue.
        self.assertFalse(
            selected[1] == ((2150, 2300)) and selected[2] == ((2250, 2450))
        )

    def _run_hfa_overlay_fixture(self, *, output_name, mapping_check=None):
        import lyric_aligner.alignment.source_context_hubertfa as hfa

        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["experimental_source_context_hubertfa"] = {"test_only": True}
        if mapping_check is not None:
            job["sources"][0]["mix_centres_seconds"] = [1.0]
        self._write_json(self.job, job)

        dictionary = self.root / "hermetic-hfa-en.dict"
        dictionary.write_text("\n".join(f"{word}\tAH" for word in self.CANONICAL) + "\n", encoding="utf-8")

        class FakeConfig:
            mode = "hfa-only-overlay"
            policy_id = hfa.SOURCE_CONTEXT_HUBERTFA_POLICY_ID
            def __init__(self, dictionary_path):
                self.dictionary_path = dictionary_path
            def identity(self):
                return {"protocol_version": "test", "policy_id": self.policy_id, "mode": self.mode}
            def acoustic_request_fields(self):
                return {}
            def vendor_files(self):
                return {}

        def fake_execute(_config, records, *, work_dir):
            work_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {}
            for name in ("request", "response", "stdout", "stderr"):
                path = work_dir / (name + ".fixture")
                path.write_text(name + "\n", encoding="utf-8")
                artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
            response = []
            for record in records:
                words = []
                for index, token in enumerate(token for segment in record["segment_lexical_units"] for token in segment):
                    words.append({"text": token, "start": 0.1 + index * 0.2, "end": 0.2 + index * 0.2})
                response.append({"record_id": record["record_id"], "status": "aligned", "reason": "", "words": words})
            return {"response": {"records": response}, "artifacts": artifacts}

        def observer(**kwargs):
            result = self._fresh_source_observer(**kwargs)
            result["artifact_sha256"] = json_sha({key: value for key, value in result.items()
                                                   if key not in {"artifact_sha256", "cache_hit"}})
            return result

        output = self.root / output_name
        with ExitStack() as stack:
            stack.enter_context(patch.object(hfa.SourceContextHuBERTFAConfig, "from_job", return_value=FakeConfig(dictionary)))
            stack.enter_context(patch.object(hfa, "execute_batch", side_effect=fake_execute))
            if mapping_check is not None:
                stack.enter_context(patch("lyric_aligner.audio.contextual_mapping.generate_contextual_mapping_candidates",
                    return_value=mapping_check))
            result = run_shadow_job(self.job, output, source_observer=observer)
        return result, output

    def test_explicit_hfa_overlay_is_separate_from_the_regular_shadow(self):
        result, output = self._run_hfa_overlay_fixture(output_name="hfa-overlay-shadow")
        self.assertTrue((output / "shadow.srt").is_file())
        self.assertTrue((output / "hfa_overlay.srt").is_file())
        self.assertTrue((output / "hfa_overlay.csv").is_file())
        self.assertTrue((output / "hfa_overlay.artifact.json").is_file())
        overlay = json.loads((output / "hfa_overlay.artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay["authority"], "experimental")
        self.assertFalse(overlay["publish_ready"])
        self.assertGreater(overlay["candidate_count"], 0)
        hfa_ledger = json.loads((output / "source_context_hubertfa_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(hfa_ledger["schema_version"], "source-context-hubertfa-ledger-1.2")
        observed = [entry for occurrence in hfa_ledger["occurrences"] for entry in occurrence["prepared"]["ledger"]
                    if entry.get("result", {}).get("status") == "observed_complete_interval"]
        self.assertTrue(observed)
        self.assertTrue(all(entry["overlay_contextual_mapping_status"] == "not_checked_at_this_interval" for entry in observed))
        compact = hfa_ledger["occurrences"][0]["prepared"]
        self.assertNotIn("packets", compact)
        self.assertFalse(compact["packet_materialization"]["full_packets_materialized_in_this_artifact"])
        evidence = hfa_ledger["occurrences"][0]["source_evidence"]
        source_path = output / evidence["relative_path"]
        self.assertEqual(sha256_file(source_path), evidence["sha256"])
        source = json.loads(source_path.read_text(encoding="utf-8"))
        self.assertEqual(source["schema_version"], "source-observation-hubertfa-evidence-1.1")
        embedded = source["source_observation"]
        self.assertEqual(embedded["artifact_sha256"], source["source_observation_self_sha256"])
        self.assertEqual(json_sha({key: value for key, value in embedded.items() if key != "artifact_sha256"}),
                         source["source_observation_self_sha256"])
        for name, artifact in hfa_ledger["batch"].items():
            self.assertEqual(sha256_file(output / artifact["relative_path"]), artifact["sha256"], name)
        self.assertEqual(result["strategy_id"], "source-context-interval-shadow-2026-09-08-v5-source-sequence")

    def test_anchored_block_runs_one_inference_and_fans_out_adjacent_interior_targets_only(self):
        import lyric_aligner.alignment.source_context_hubertfa as hfa

        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["experimental_source_context_hubertfa"] = {"test_only": True}
        self._write_json(self.job, job)
        dictionary = self.root / "anchored-block-en.dict"
        dictionary.write_text("\n".join(f"{word}\tAH" for word in self.CANONICAL) + "\n", encoding="utf-8")

        class FakeConfig:
            mode = "hfa-only-overlay"
            policy_id = hfa.SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID
            context_policy = hfa.SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK
            def __init__(self, dictionary_path): self.dictionary_path = dictionary_path
            def identity(self):
                return {"protocol_version": "test", "policy_id": self.policy_id, "mode": self.mode,
                        "context_policy": self.context_policy}
            def acoustic_request_fields(self):
                return {"acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3}
            def context_request_fields(self):
                return {"context_policy": self.context_policy, "block_max_interior_lines": 12,
                        "block_max_window_ms": 45_000, "block_max_lexical_units": 256}
            def vendor_files(self): return {}

        calls = []
        def fake_prepare(**kwargs):
            oid = kwargs["occurrence_id"]
            return {"schema_version": "fixture", "policy_id": FakeConfig.policy_id,
                    "context_policy": hfa.SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, "authority": hfa.SOURCE_CONTEXT_AUTHORITY,
                    "occurrence_id": oid, "source_observation_sha256": "a" * 64,
                    "canonical_lines": [{"canonical_line_index": offset, "text": text}
                                        for offset, text in enumerate(self.CANONICAL, 1)],
                    "packet_count": 1, "packets": [{"canonical_line_index": 1, "packet": {
                        "cache_key_sha256": "1" * 64, "candidates": [], "coverage": {}, "policy_id": "fixture"}}],
                    "source_sequence": {"status": "complete", "promotions": []}, "records": [],
                    "ledger": [{"position": 2, "target_index": 2, "status": "unavailable", "reasons": []},
                               {"position": 3, "target_index": 3, "status": "unavailable", "reasons": []}]}

        def fake_blocks(prepared, *, dictionary_path):
            record = {"record_id": "anchored-block-record", "occurrence_id": prepared["occurrence_id"],
                "context_policy": hfa.SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK, "language": "en",
                "source_window_ms": [0, 2000], "segment_text": list(self.CANONICAL),
                "segment_lexical_units": [[word] for word in self.CANONICAL],
                "lexical_units_sha256": hfa.json_sha([[word] for word in self.CANONICAL]),
                "target_outputs": [{"position": 2, "canonical_line_index": 2, "target_segment_index": 1},
                                   {"position": 3, "canonical_line_index": 3, "target_segment_index": 2}],
                "context": {"context_policy": hfa.SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK,
                    "outer_anchor_candidates": [
                        {"canonical_line_index": 1, "packet_cache_key_sha256": "1" * 64,
                         "candidate_id": "left", "source_interval_ms": [900, 1000],
                         "qualification": "selected_full_context"},
                        {"canonical_line_index": 5, "packet_cache_key_sha256": "5" * 64,
                         "candidate_id": "right", "source_interval_ms": [1800, 1900],
                         "qualification": "selected_full_context"}],
                    "canonical_segment_indices": [1, 2, 3, 4, 5], "canonical_interior_indices": [2, 3, 4]}}
            return {"schema_version": "fixture", "records": [record], "anchor_count": 2,
                    "missing_three_line_target_count": 2}

        def fake_execute(_config, records, *, work_dir):
            calls.append([record["record_id"] for record in records])
            work_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {}
            for name in ("request", "response", "stdout", "stderr"):
                path = work_dir / (name + ".fixture")
                path.write_text(name + "\n", encoding="utf-8")
                artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
            return {"response": {"records": [{"record_id": "anchored-block-record", "status": "aligned", "reason": "",
                "words": [
                    {"text": "alpha", "start": 0.9, "end": 1.0},
                    {"text": "bravo", "start": 1.15, "end": 1.35},
                    {"text": "charlie", "start": 1.35, "end": 1.55},
                    {"text": "delta", "start": 1.6, "end": 1.7},
                    {"text": "echo", "start": 1.8, "end": 1.9},
                ]}]}, "artifacts": artifacts}

        def observer(**kwargs):
            result = self._fresh_source_observer(**kwargs)
            result["artifact_sha256"] = json_sha({key: value for key, value in result.items()
                                                   if key not in {"artifact_sha256", "cache_hit"}})
            return result

        output = self.root / "anchored-block-overlay"
        with ExitStack() as stack:
            stack.enter_context(patch.object(hfa.SourceContextHuBERTFAConfig, "from_job", return_value=FakeConfig(dictionary)))
            stack.enter_context(patch.object(hfa, "prepare_occurrence", side_effect=fake_prepare))
            stack.enter_context(patch.object(hfa, "prepare_anchored_blocks", side_effect=fake_blocks))
            stack.enter_context(patch.object(hfa, "execute_batch", side_effect=fake_execute))
            result = run_shadow_job(self.job, output, source_observer=observer)
        self.assertEqual(calls, [["anchored-block-record"]])
        overlay = json.loads((output / "hfa_overlay.artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay["candidate_count"], 2)
        self.assertEqual(overlay["selected_hubertfa_interval_count"], 2)
        hfa_ledger = json.loads((output / "source_context_hubertfa_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(hfa_ledger["anchored_block_record_count"], 1)
        self.assertEqual(hfa_ledger["anchored_block_target_count"], 2)
        self.assertEqual(hfa_ledger["inference_record_count"], 1)
        self.assertEqual(hfa_ledger["eligible_target_count"], 2)
        self.assertEqual(hfa_ledger["complete_target_interval_count"], 2)
        self.assertEqual(hfa_ledger["overlay_candidate_target_count"], 2)
        self.assertEqual(hfa_ledger["overlay_selected_target_count"], 2)
        prepared = hfa_ledger["occurrences"][0]["prepared"]
        self.assertEqual(len(prepared["anchored_block_records"]), 1)
        self.assertEqual([item["position"] for item in prepared["anchored_block_records"][0]["target_outputs"]], [2, 3])
        self.assertEqual(result["hfa_overlay"]["candidate_count"], 2)

    def _run_anchored_path_fixture(self, *, joint=False, exact=False):
        import lyric_aligner.alignment.source_context_hubertfa as hfa

        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["experimental_source_context_hubertfa"] = {"test_only": True}
        if joint:
            job["experimental_joint_source_context"] = {"policy_id": "atomic-adjacent-pair-v1"}
        if exact:
            job["experimental_exact_source_anchors"] = {"policy_id": "source-exact-word-run-legacy-bracket-v1"}
        self._write_json(self.job, job)
        dictionary = self.root / "anchored-path-en.dict"
        dictionary.write_text("\n".join(f"{word}\tAH" for word in self.CANONICAL) + "\n", encoding="utf-8")

        class FakeConfig:
            mode = "hfa-only-overlay"
            policy_id = hfa.SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID
            context_policy = hfa.SOURCE_CONTEXT_POLICY_ANCHORED_PATH
            def __init__(self, dictionary_path): self.dictionary_path = dictionary_path
            def identity(self): return {"protocol_version": "test", "policy_id": self.policy_id, "mode": self.mode,
                                         "context_policy": self.context_policy, "time_band_decoder": {"path": "fixture", "sha256": "x"}}
            def acoustic_request_fields(self):
                return {"acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3}
            def context_request_fields(self):
                return {"context_policy": self.context_policy, "block_max_interior_lines": 12,
                        "block_max_window_ms": 45_000, "block_max_lexical_units": 256,
                        "time_banded_decoder_id": "hubertfa-time-banded-destination-mask-v1",
                        "time_banded_postcheck": "frame_length_x_1.5_plus_0.0001-v1",
                        "time_banded_source_pad_ms": 1500}
            def vendor_files(self): return {}

        def fake_prepare(**kwargs):
            return {"schema_version": "fixture", "policy_id": FakeConfig.policy_id,
                    "context_policy": hfa.SOURCE_CONTEXT_POLICY_ANCHORED_PATH, "authority": hfa.SOURCE_CONTEXT_AUTHORITY,
                    "occurrence_id": kwargs["occurrence_id"], "source_observation_sha256": "a" * 64,
                    "canonical_lines": [{"canonical_line_index": offset, "text": text}
                                        for offset, text in enumerate(self.CANONICAL, 1)],
                    "packet_count": 1, "packets": [{"canonical_line_index": 1, "packet": {
                        "cache_key_sha256": "1" * 64, "candidates": [], "coverage": {}, "policy_id": "fixture"}}],
                    "source_sequence": {"status": "complete", "promotions": []}, "records": [],
                    "ledger": [{"position": 2, "target_index": 2, "status": "unavailable", "reasons": []},
                               {"position": 3, "target_index": 3, "status": "unavailable", "reasons": []}]}

        def fake_blocks(prepared, *, dictionary_path):
            record = {"record_id": "anchored-path-record", "occurrence_id": prepared["occurrence_id"],
                "context_policy": hfa.SOURCE_CONTEXT_POLICY_ANCHORED_PATH, "language": "en",
                "source_window_ms": [0, 2000], "segment_text": list(self.CANONICAL),
                "segment_lexical_units": [[word] for word in self.CANONICAL],
                "lexical_units_sha256": hfa.json_sha([[word] for word in self.CANONICAL]),
                "target_outputs": [{"position": 2, "canonical_line_index": 2, "target_segment_index": 1},
                                   {"position": 3, "canonical_line_index": 3, "target_segment_index": 2}],
                "context": {"context_policy": hfa.SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
                    "outer_anchor_candidates": [], "canonical_segment_indices": [1, 2, 3, 4, 5],
                    "canonical_interior_indices": [2, 3, 4],
                    "qualified_source_anchors": [{"canonical_line_index": 1, "packet_cache_key_sha256": "1" * 64,
                        "candidate_id": "left", "source_interval_ms": [900, 1000], "qualification": "selected_full_context"},
                        {"canonical_line_index": 2, "packet_cache_key_sha256": "2" * 64,
                        "candidate_id": "own", "source_interval_ms": [1100, 1200], "qualification": "selected_full_context"},
                        {"canonical_line_index": 5, "packet_cache_key_sha256": "5" * 64,
                        "candidate_id": "right", "source_interval_ms": [1800, 1900], "qualification": "selected_full_context"}],
                    "time_banded_decoder_id": "hubertfa-time-banded-destination-mask-v1",
                    "time_banded_postcheck": "frame_length_x_1.5_plus_0.0001-v1",
                    "time_banded_source_pad_ms": 1500}}
            return {"schema_version": "fixture", "records": [record], "anchor_count": 3,
                    "missing_three_line_target_count": 2,
                    "anchored_path_target_with_own_band_count": 1,
                    "anchored_path_target_outer_only_band_count": 1}

        def fake_execute(_config, records, *, work_dir):
            self.assertEqual([record["record_id"] for record in records], ["anchored-path-record"])
            work_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {}
            for name in ("request", "response", "stdout", "stderr"):
                path = work_dir / (name + ".fixture")
                path.write_text(name + "\n", encoding="utf-8")
                artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
            return {"response": {"records": [{"record_id": "anchored-path-record", "status": "aligned", "reason": "",
                "words": [{"text": text, "start": 0.9 + offset * .15, "end": 1.0 + offset * .15}
                          for offset, text in enumerate(self.CANONICAL)]}]}, "artifacts": artifacts}

        def observer(**kwargs):
            result = self._fresh_source_observer(**kwargs)
            result["artifact_sha256"] = json_sha({key: value for key, value in result.items()
                                                   if key not in {"artifact_sha256", "cache_hit"}})
            return result

        output = self.root / "anchored-path-overlay"
        with ExitStack() as stack:
            stack.enter_context(patch.object(hfa.SourceContextHuBERTFAConfig, "from_job", return_value=FakeConfig(dictionary)))
            stack.enter_context(patch.object(hfa, "prepare_occurrence", side_effect=fake_prepare))
            stack.enter_context(patch.object(hfa, "prepare_anchored_blocks", side_effect=fake_blocks))
            stack.enter_context(patch.object(hfa, "execute_batch", side_effect=fake_execute))
            run_shadow_job(self.job, output, source_observer=observer)
        state = json.loads((output / "source_context_hubertfa_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(state["eligible_target_count"], 2)
        self.assertEqual(state["complete_target_interval_count"], 2)
        self.assertEqual(state["anchored_path_target_with_own_band_count"], 1)
        self.assertEqual(state["anchored_path_target_outer_only_band_count"], 1)
        return json.loads((output / "shadow.artifact.json").read_text(encoding="utf-8"))

    def test_anchored_path_runner_fans_out_and_reports_own_vs_outer_only_without_filtering(self):
        result = self._run_anchored_path_fixture()
        self.assertNotIn("joint_source_context", result)

    def test_joint_overlay_is_explicit_and_receives_full_occurrence_context(self):
        with patch("scripts.source_joint_overlay.run_joint_overlay", return_value={"probe": True}) as joint:
            result = self._run_anchored_path_fixture(joint=True)
        self.assertEqual(result["joint_source_context"], {"probe": True})
        joint.assert_called_once()
        context = next(iter(joint.call_args.kwargs["contexts"].values()))
        self.assertIn("packets", context["prepared"])
        self.assertIn("mapping", context)
        self.assertEqual(len(joint.call_args.kwargs["nodes"]), len(self.CANONICAL))

    def test_joint_overlay_cannot_silently_run_without_hfa(self):
        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["experimental_joint_source_context"] = {"policy_id": "atomic-adjacent-pair-v1"}
        self._write_json(self.job, job)
        with self.assertRaisesRegex(ValueError, "requires anchored-path"):
            run_shadow_job(self.job, self.root / "invalid-joint")

    def test_exact_anchor_flag_requires_joint_and_passes_original_words(self):
        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["experimental_exact_source_anchors"] = {"policy_id": "source-exact-word-run-legacy-bracket-v1"}
        self._write_json(self.job, job)
        with self.assertRaisesRegex(ValueError, "require explicit joint"):
            run_shadow_job(self.job, self.root / "invalid-exact")
        with patch("scripts.source_joint_overlay.run_joint_overlay", return_value={"probe": True}) as joint:
            self._run_anchored_path_fixture(joint=True, exact=True)
        context = next(iter(joint.call_args.kwargs["contexts"].values()))
        self.assertIsInstance(context["exact_observed_words"], list)
        self.assertTrue(context["exact_observed_words"])

    def test_hfa_overlay_rejects_ambiguous_contextual_mapping_when_covered(self):
        result, output = self._run_hfa_overlay_fixture(output_name="hfa-overlay-ambiguous", mapping_check={
            "status": "available", "ambiguous": True, "top1": {"mix_start": 0.0, "mix_end": 10.0},
        })
        overlay = json.loads((output / "hfa_overlay.artifact.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay["candidate_count"], 0)
        self.assertEqual(overlay["selected_hubertfa_interval_count"], 0)
        hfa_ledger = json.loads((output / "source_context_hubertfa_ledger.json").read_text(encoding="utf-8"))
        observed = [entry for occurrence in hfa_ledger["occurrences"] for entry in occurrence["prepared"]["ledger"]
                    if entry.get("result", {}).get("status") == "observed_complete_interval"]
        self.assertTrue(observed)
        self.assertTrue(all(entry["overlay_contextual_mapping_status"] == "covered" for entry in observed))
        self.assertTrue(all(entry["overlay_rejection"] == "contextual_mapping_ambiguous" for entry in observed))
        self.assertEqual(result["hfa_overlay"]["candidate_count"], 0)

    def test_internal_transcript_error_keeps_observed_outer_edges_through_actual_srt(self):
        words = [{"text": text if i != 2 else "charxie",
                  "start_ms": start + 10, "end_ms": end - 10, "window_id": "whole_source"}
                 for i, (text, (start, end)) in enumerate(zip(self.CANONICAL, self.BASELINE))]
        output = self.root / "internal-edit-shadow"
        run_shadow_job(self.job, output, source_observer=lambda **_: self._source_observation(words))
        _, cues = validate_pair(output / "shadow.csv", output / "shadow.srt")
        self.assertEqual((cues[2].start_ms, cues[2].end_ms), (1410, 1490))
        self.assertEqual(cues[2].text, "charlie")
        self.assertEqual(self.source_srt.read_bytes(), self.baseline_srt_bytes)
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        row = next(row for row in ledger["records"] if row["position"] == 3)
        self.assertEqual(row["source_packet"]["selection_reason"], "unique_outer_anchored_bounded_target_edit")
        self.assertFalse(row["selected_candidate_id"].endswith(":KEEP"))

    def test_orphan_exact_target_cannot_hide_supported_rescue_from_srt(self):
        words = [{"text": "charlie", "start_ms": 0, "end_ms": 100, "window_id": "whole_source"}]
        words += [{"text": text if i != 2 else "charxie", "start_ms": start + 10,
                   "end_ms": end - 10, "window_id": "whole_source"}
                  for i, (text, (start, end)) in enumerate(zip(self.CANONICAL, self.BASELINE))]
        output = self.root / "orphan-exact-shadow"
        run_shadow_job(self.job, output, source_observer=lambda **_: self._source_observation(words))
        _, cues = validate_pair(output / "shadow.csv", output / "shadow.srt")
        self.assertEqual((cues[2].start_ms, cues[2].end_ms), (1410, 1490))
        self.assertEqual(cues[2].text, "charlie")

    def test_changed_shadow_row_rebinds_active_provenance_and_preserves_baseline_human_record(self):
        # Give the source report a real cue identity plus human provenance.
        # Only alpha is retimed; bravo stays at its original timing and must
        # retain its active reviewer annotation unchanged.
        original_cues = parse_srt_strict(self.source_srt)
        with self.report.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        fields = list(rows[0]) + [
            "cue_id", "status", "confidence", "evidence", "boundary_authority",
            "human_confirmed_start_record", "human_confirmed_end_record",
            "human_confirmation_artifact_sha256",
        ]
        for position, (row, cue) in enumerate(zip(rows, original_cues), 1):
            row.update(
                cue_id=cue_id(position, cue),
                status="human_confirmed",
                confidence="high",
                evidence="reviewer_hearing",
                boundary_authority="human_review",
                human_confirmed_start_record=f"start-record-{position}",
                human_confirmed_end_record=f"end-record-{position}",
                human_confirmation_artifact_sha256=f"human-artifact-{position}",
            )
        old_alpha = dict(rows[0])
        old_bravo = dict(rows[1])
        with self.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        self._write_shadow_job(self.job)

        words = [
            {"text": "alpha", "start_ms": 1050, "end_ms": 1150},
            {"text": "bravo", "start_ms": 1200, "end_ms": 1300},
            {"text": "charlie", "start_ms": 1400, "end_ms": 1500},
            {"text": "delta", "start_ms": 1600, "end_ms": 1700},
            {"text": "echo", "start_ms": 1800, "end_ms": 1900},
        ]
        output = self.root / "provenance-rebound-shadow"
        run_shadow_job(self.job, output, source_observer=lambda **_kwargs: self._source_observation(words))
        with (output / "shadow.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            shadow_rows = list(csv.DictReader(handle))
        shadow_cues = parse_srt_strict(output / "shadow.srt")

        changed = shadow_rows[0]
        self.assertEqual((shadow_cues[0].start_ms, shadow_cues[0].end_ms), (1050, 1150))
        self.assertEqual(changed["cue_id"], cue_id(1, shadow_cues[0]))
        self.assertEqual(changed["shadow_baseline_cue_id"], old_alpha["cue_id"])
        self.assertEqual(changed["status"], "experimental_shadow_timing")
        self.assertEqual(changed["confidence"], "uncalibrated")
        self.assertEqual(changed["evidence"], "shadow_candidate_ledger")
        self.assertEqual(changed["boundary_authority"], "experimental_only")
        for field in (
            "human_confirmed_start_record", "human_confirmed_end_record",
            "human_confirmation_artifact_sha256",
        ):
            self.assertEqual(changed[field], "")
            self.assertEqual(changed["shadow_baseline_" + field], old_alpha[field])

        unchanged = shadow_rows[1]
        self.assertEqual((shadow_cues[1].start_ms, shadow_cues[1].end_ms), self.BASELINE[1])
        for field in (
            "cue_id", "status", "confidence", "evidence", "boundary_authority",
            "human_confirmed_start_record", "human_confirmed_end_record",
            "human_confirmation_artifact_sha256",
        ):
            self.assertEqual(unchanged[field], old_bravo[field])

    def test_unavailable_source_backend_keeps_baseline_intervals(self):
        output = self.root / "unavailable-result"

        def unavailable(**_kwargs):
            return {
                "status": "unavailable",
                "reason": "local_model_unavailable",
                "audio_basis": "source",
                "source_audio_sha256": self.binding.source_audio_sha256,
                "words": [],
            }

        result = run_shadow_job(self.job, output, source_observer=unavailable)
        self.assertEqual(result["selected_source_interval_count"], 0)
        self.assertEqual(
            [(cue.start_ms, cue.end_ms) for cue in parse_srt_strict(output / "shadow.srt")],
            list(self.BASELINE),
        )
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["reason"] == "local_model_unavailable" for item in ledger["records"]))
        self.assertTrue(all(item["selected_candidate_id"].endswith(":KEEP") for item in ledger["records"]))

    def test_one_observed_source_edge_replaces_only_that_edge_without_reading_gold(self):
        cases = [
            (
                "known-start",
                [
                    {"text": "al", "start_ms": 500, "end_ms": 600},
                    {"text": "pha", "start_ms": 600, "end_ms": 600},
                    {"text": "bravo", "start_ms": 1200, "end_ms": 1300},
                    {"text": "charlie", "start_ms": 1400, "end_ms": 1500},
                    {"text": "delta", "start_ms": 1600, "end_ms": 1700},
                    {"text": "echo", "start_ms": 1800, "end_ms": 1900},
                ],
                0,
                (500, self.BASELINE[0][1]),
                "observed_positive_duration_word",
                "unknown_zero_duration_word",
            ),
            (
                "known-end",
                [
                    {"text": "alpha", "start_ms": 1000, "end_ms": 1100},
                    {"text": "bravo", "start_ms": 1200, "end_ms": 1300},
                    {"text": "char", "start_ms": 1600, "end_ms": 1600},
                    {"text": "lie", "start_ms": 1600, "end_ms": 1700},
                    {"text": "delta", "start_ms": 1800, "end_ms": 1900},
                    {"text": "echo", "start_ms": 2000, "end_ms": 2100},
                ],
                2,
                (self.BASELINE[2][0], 1700),
                "unknown_zero_duration_word",
                "observed_positive_duration_word",
            ),
        ]
        with patch(
            "scripts.v4_evaluate_product_boundaries.build_report",
            side_effect=AssertionError("gold/evaluation must not be read during prediction"),
        ):
            for label, words, index, expected, start_reason, end_reason in cases:
                with self.subTest(label=label):
                    output = self.root / ("one-edge-" + label)
                    result = run_shadow_job(
                        self.job,
                        output,
                        source_observer=lambda **_kwargs: self._source_observation(words),
                    )
                    actual = [(cue.start_ms, cue.end_ms) for cue in parse_srt_strict(output / "shadow.srt")]
                    self.assertEqual(actual[index], expected)
                    self.assertEqual(result["text_changed_count"], 0)
                    self.assertFalse((output / "historical_quality.json").exists())
                    ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
                    record = next(item for item in ledger["records"] if item["position"] == index + 1)
                    candidate = record["source_packet"]["candidates"][0]
                    self.assertEqual(candidate["source_interval_start_reason"], start_reason)
                    self.assertEqual(candidate["source_interval_end_reason"], end_reason)
                    self.assertFalse(record["selected_candidate_id"].endswith(":KEEP"))

    def test_both_unknown_source_edges_leave_the_complete_baseline_interval(self):
        words = [
            {"text": "alpha", "start_ms": 1000, "end_ms": 1100},
            {"text": "bravo", "start_ms": 1200, "end_ms": 1300},
            {"text": "char", "start_ms": 1600, "end_ms": 1600},
            {"text": "lie", "start_ms": 1700, "end_ms": 1700},
            {"text": "delta", "start_ms": 1800, "end_ms": 1900},
            {"text": "echo", "start_ms": 2000, "end_ms": 2100},
        ]
        output = self.root / "both-edges-unknown"
        run_shadow_job(self.job, output, source_observer=lambda **_kwargs: self._source_observation(words))
        actual = [(cue.start_ms, cue.end_ms) for cue in parse_srt_strict(output / "shadow.srt")]
        self.assertEqual(actual[2], self.BASELINE[2])
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        record = next(item for item in ledger["records"] if item["position"] == 3)
        self.assertEqual(record["selected_candidate_id"], "3:KEEP")
        self.assertTrue(any(
            item["reason"] == "source_endpoints_unavailable"
            for item in record["rejected_candidates"]
        ))

    def test_one_edge_candidate_is_rejected_when_kept_baseline_edge_crosses_cut_segment(self):
        cut_mapping = {
            "kind": "CUT_AWARE",
            "segments": [
                {
                    "index": 0,
                    "mix_start": 0.0,
                    "mix_end": 1.45,
                    "source_start": 0.0,
                    "source_end": 1.45,
                    "mapping": {"kind": "AFFINE", "intercept": 0.0, "base_slope": 1.0,
                                "breakpoints": [], "slope_deltas": []},
                },
                {
                    "index": 1,
                    "mix_start": 1.45,
                    "mix_end": 3.0,
                    "source_start": 2.0,
                    "source_end": 3.55,
                    "mapping": {"kind": "AFFINE", "intercept": 0.55, "base_slope": 1.0,
                                "breakpoints": [], "slope_deltas": []},
                },
            ],
        }
        self._rebind_fine_mapping(cut_mapping)
        words = [
            {"text": "alpha", "start_ms": 1000, "end_ms": 1100},
            {"text": "bravo", "start_ms": 1200, "end_ms": 1300},
            {"text": "char", "start_ms": 1400, "end_ms": 1450},
            {"text": "lie", "start_ms": 1450, "end_ms": 1450},
            {"text": "delta", "start_ms": 2400, "end_ms": 2500},
            {"text": "echo", "start_ms": 2600, "end_ms": 2700},
        ]
        output = self.root / "cross-cut-one-edge"
        run_shadow_job(self.job, output, source_observer=lambda **_kwargs: self._source_observation(words))
        actual = [(cue.start_ms, cue.end_ms) for cue in parse_srt_strict(output / "shadow.srt")]
        self.assertEqual(actual[2], self.BASELINE[2])
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        record = next(item for item in ledger["records"] if item["position"] == 3)
        self.assertTrue(record["source_packet"]["selected_candidate_id"])
        self.assertEqual(record["selected_candidate_id"], "3:KEEP")
        self.assertFalse(any(item["candidate_shape"] == "start_only" for item in record["interval_candidates"]))
        self.assertTrue(any(
            item["reason"] == "composed_interval_crosses_retained_cut_segment"
            for item in record["rejected_candidates"]
        ))

    def test_stale_bound_sha_and_wrong_source_time_basis_fail_before_shadow_delivery(self):
        stale = self.root / "stale-job.json"

        def stale_fine(job):
            job["sources"][0]["fine"]["sha256"] = "0" * 64

        self._write_shadow_job(stale, mutate=stale_fine)
        with self.assertRaisesRegex(ValueError, "fine:.*SHA mismatch"):
            run_shadow_job(stale, self.root / "stale-result", source_observer=self._fresh_source_observer)
        self.assertFalse((self.root / "stale-result").exists())

        def wrong_time_basis(**_kwargs):
            observation = self._fresh_source_observer(audio_path=self.source_audio, audio_sha256=self.binding.source_audio_sha256)
            observation["audio_basis"] = "mix"
            return observation

        with self.assertRaisesRegex(ValueError, "another audio/time basis"):
            run_shadow_job(self.job, self.root / "wrong-basis-result", source_observer=wrong_time_basis)
        self.assertFalse((self.root / "wrong-basis-result").exists())

    def test_changed_bound_source_and_production_srt_destination_are_rejected(self):
        self.source_audio.write_bytes(b"tampered-after-asset-resolution")
        with self.assertRaisesRegex(ValueError, "source changed after asset resolution"):
            run_shadow_job(self.job, self.root / "changed-source-result", source_observer=self._fresh_source_observer)
        self.assertFalse((self.root / "changed-source-result").exists())
        self.assertEqual(self.source_srt.read_bytes(), self.baseline_srt_bytes)

        # Rebuild a fresh fixture member for the output-tree collision case so
        # the preceding intentional source mutation is not its failure cause.
        self.setUp()
        with self.assertRaises(ValueError):
            run_shadow_job(self.job, self.source_srt, source_observer=self._fresh_source_observer)
        self.assertEqual(self.source_srt.read_bytes(), self.baseline_srt_bytes)

    def test_failed_shadow_attempt_preserves_diagnostics_but_same_output_can_rerun(self):
        output = self.root / "rerunnable-shadow-result"

        def fail_after_staging(**_kwargs):
            raise RuntimeError("synthetic observer interruption")

        with self.assertRaisesRegex(RuntimeError, "synthetic observer interruption"):
            run_shadow_job(self.job, output, source_observer=fail_after_staging)
        self.assertFalse(output.exists())
        preserved = list(self.root.glob(output.name + ".staging.*"))
        self.assertTrue(preserved)
        self.assertTrue(all((path / "execution_receipt.json").is_file() for path in preserved))

        result = run_shadow_job(self.job, output, source_observer=self._fresh_source_observer)
        self.assertTrue((output / "shadow.srt").is_file())
        self.assertFalse(result["publish_ready"])

    def test_legacy_schema_cannot_request_shadow_mode(self):
        old_schema_shadow = self.root / "old-schema-shadow-job.json"

        def rewrite_as_legacy(job):
            job["schema_version"] = "subtitle-upgrade-job-1.0"

        self._write_shadow_job(old_schema_shadow, mutate=rewrite_as_legacy)
        with self.assertRaisesRegex(ValueError, "execution_mode requires the explicit shadow job schema"):
            run_job(old_schema_shadow, self.root / "old-schema-shadow-result")

    def test_unknown_top_level_source_config_is_rejected_instead_of_ignored(self):
        # ``source_config`` was once documented as if it were the source ASR
        # namespace.  The runner only consumed ``source_asr`` and silently
        # ignored this sibling, so a multilingual request could run with the
        # legacy defaults while looking configured in the job file.
        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["source_config"] = {"multilingual": True}
        path = self.root / "unknown-source-config.json"
        self._write_json(path, job)
        output = self.root / "unknown-source-config-result"
        with self.assertRaisesRegex(ValueError, "unknown shadow job fields: source_config"):
            run_shadow_job(path, output, source_observer=self._fresh_source_observer)
        self.assertFalse(output.exists())

    def test_unknown_source_asr_field_is_rejected_with_config_name(self):
        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["source_asr"]["multi_lingual"] = True
        path = self.root / "unknown-source-asr-field.json"
        self._write_json(path, job)
        with self.assertRaisesRegex(ValueError, "source_asr has unknown fields: multi_lingual"):
            run_shadow_job(path, self.root / "unknown-source-asr-result",
                           source_observer=self._fresh_source_observer)

    def test_source_cache_cannot_claim_reserved_shadow_staging_path(self):
        output = self.root / "reserved-cache-shadow-result"
        reserved_cache_job = self.root / "reserved-cache-shadow-job.json"

        def reserve_staging_as_cache(job):
            job["source_cache_dir"] = str(output.with_name(output.name + ".staging"))

        self._write_shadow_job(reserved_cache_job, mutate=reserve_staging_as_cache)
        with self.assertRaisesRegex(ValueError, "source cache and shadow output/staging must be disjoint"):
            run_shadow_job(reserved_cache_job, output, source_observer=self._fresh_source_observer)
        self.assertFalse(output.exists())

    def test_v4_upgrade_dispatches_shadow_early_and_legacy_schema_still_materializes_final(self):
        with patch("scripts.v4_shadow_upgrade.run_shadow_job", return_value={"shadow": True}) as dispatched:
            self.assertEqual(run_job(self.job, self.root / "dispatch-result"), {"shadow": True})
            dispatched.assert_called_once_with(self.job.resolve(), self.root / "dispatch-result")

        legacy_job = self.root / "legacy-job.json"
        self._write_json(
            legacy_job,
            {
                "schema_version": "subtitle-upgrade-job-1.0",
                "task_manifest": str(self.manifest),
                "report": str(self.report),
                "srt": str(self.source_srt),
            },
        )
        legacy_output = self.root / "legacy-output"
        result = run_job(legacy_job, legacy_output)
        self.assertTrue((legacy_output / "final.srt").is_file())
        self.assertTrue(result["srt_byte_identical"])

    def test_editor_preserved_partial_canonical_content_offsets_are_used_without_lrc_line_assumption(self):
        # A preserved editor cue can own only the tail of a canonical LRC line.
        # It must reach packet matching with exact raw canonical ownership, not
        # a first text search or a whole-line fallback.  The source word keeps
        # the new candidate non-materializable because its start is sub-word.
        self._write_srt(self.source_srt, self.BASELINE, texts=("pha", *self.CANONICAL[1:]))
        with self.report.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0].update(text="pha", canonical_content_start="2", canonical_content_end="5")
        with self.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        self._write_shadow_job(self.job)

        output = self.root / "partial-canonical-content-result"
        result = run_shadow_job(self.job, output, source_observer=self._fresh_source_observer)
        self.assertTrue((output / "shadow.srt").is_file())
        self.assertEqual(result["text_changed_count"], 0)
        ledger = json.loads((output / "candidate_ledger.json").read_text(encoding="utf-8"))
        record = next(item for item in ledger["records"] if item["position"] == 1)
        packet = record["source_packet"]
        self.assertEqual(packet["target_cue"]["canonical_character_ranges"], [
            {"canonical_line_index": 0, "start_char": 2, "end_char": 5}
        ])
        self.assertEqual(packet["candidates"][0]["source_interval_ms"][0], None)
        self.assertEqual(record["selected_candidate_id"], "1:KEEP")


if __name__ == "__main__":
    unittest.main()
