import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from check_environment import check_environment
from init_task import init_task, task_name
from migrate_task import migrate_file, prepare_migration
from task_contract import (
    QA_SCHEMA_VERSION,
    TASK_SCHEMA_VERSION,
    build_task_manifest,
    load_task_manifest,
    qa_metadata,
    report_fingerprint,
    sha256,
    validate_qa_artifact,
    verify_manifest_inputs,
    write_json_atomic,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def create_task_inputs(root: Path) -> dict[str, Path]:
    input_root = root / "private" / "sample-task" / "input"
    lyrics_dir = input_root / "lyrics"
    source_audio_dir = input_root / "source-audio"
    lyrics_dir.mkdir(parents=True)
    source_audio_dir.mkdir()
    paths = {
        "source_srt": input_root / "source.srt",
        "audio": input_root / "mix.wav",
        "song_list": input_root / "songs.txt",
        "lyrics_dir": lyrics_dir,
        "bpm_changes": input_root / "bpm.txt",
        "source_audio_dir": source_audio_dir,
    }
    paths["source_srt"].write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nsynthetic lyric\n",
        encoding="utf-8",
    )
    paths["audio"].write_bytes(b"synthetic-waveform")
    paths["song_list"].write_text("00:00 synthetic track\n", encoding="utf-8")
    paths["bpm_changes"].write_text("synthetic track 120 120\n", encoding="utf-8")
    (lyrics_dir / "track.lrc").write_text(
        "[00:01.00]synthetic lyric\n", encoding="utf-8"
    )
    (source_audio_dir / "track.wav").write_bytes(b"synthetic-source")
    return paths


def initialize(root: Path) -> tuple[dict[str, Path], dict[str, str]]:
    paths = create_task_inputs(root)
    result = init_task(root, "sample-task", **paths)
    return paths, result


class EnvironmentCheckTests(unittest.TestCase):
    def test_base_environment_reports_expected_contract(self):
        result = check_environment()

        self.assertEqual(set(result["modules"]), {"numpy", "scipy", "librosa"})
        self.assertEqual(set(result["tools"]), {"ffprobe"})
        self.assertFalse(result["asr_requested"])

    def test_asr_check_includes_optional_module(self):
        with mock.patch.object(importlib.util, "find_spec", return_value=None):
            result = check_environment(require_asr=True)

        self.assertIn("faster_whisper", result["modules"])
        self.assertIn("faster_whisper", result["missing_modules"])
        self.assertFalse(result["ok"])


class TaskInitializationTests(unittest.TestCase):
    def test_init_task_creates_fingerprinted_qa_skeletons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, result = initialize(root)

            manifest = load_task_manifest(Path(result["task_manifest"]))
            overrides = json.loads(
                Path(result["manual_overrides"]).read_text(encoding="utf-8")
            )
            regression = json.loads(
                Path(result["regression_cases"]).read_text(encoding="utf-8")
            )
            self.assertTrue((root / "private/sample-task/input").is_dir())
            self.assertTrue((root / "output/sample-task").is_dir())
            run_config = json.loads(
                Path(result["v4_run_config"]).read_text(encoding="utf-8")
            )
            self.assertEqual(run_config["schema_version"], "v4-run-config-1.0")
            self.assertEqual(
                run_config["task_fingerprint_sha256"],
                manifest["task_fingerprint_sha256"],
            )
            self.assertTrue(
                all(value is None for value in run_config["semantic_inputs"].values())
            )
            self.assertEqual(manifest["schema_version"], TASK_SCHEMA_VERSION)
            self.assertEqual(
                manifest["inputs"]["source_srt"]["sha256"],
                sha256(paths["source_srt"]),
            )
            self.assertEqual(overrides["schema_version"], QA_SCHEMA_VERSION)
            self.assertEqual(
                overrides["task_fingerprint_sha256"],
                manifest["task_fingerprint_sha256"],
            )
            self.assertEqual(regression["cases"], [])

    def test_init_task_does_not_overwrite_current_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, result = initialize(root)
            overrides_path = Path(result["manual_overrides"])
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            overrides["preserve"] = True
            write_json_atomic(overrides_path, overrides)

            init_task(root, "sample-task", **paths)

            preserved = json.loads(overrides_path.read_text(encoding="utf-8"))
            self.assertTrue(preserved["preserve"])

    def test_init_task_preserves_existing_semantic_run_config_when_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = create_task_inputs(root)
            language_map = root / "private/sample-task/qa/language_map.json"
            language_map.parent.mkdir(parents=True, exist_ok=True)
            language_map.write_text('{"synthetic track":"en"}\n', encoding="utf-8")
            first = init_task(
                root,
                "sample-task",
                **paths,
                language_map=language_map,
            )
            second = init_task(root, "sample-task", **paths)
            self.assertEqual(
                first["run_config_fingerprint_sha256"],
                second["run_config_fingerprint_sha256"],
            )
            config = json.loads(
                Path(second["v4_run_config"]).read_text(encoding="utf-8")
            )
            self.assertIsNotNone(config["semantic_inputs"]["language_map"])

    def test_any_input_change_rejects_task_reuse(self):
        roles = ("source_srt", "audio", "song_list", "bpm_changes")
        for role in roles:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths, _ = initialize(root)
                with paths[role].open("ab") as handle:
                    handle.write(b"changed")
                with self.assertRaisesRegex(ValueError, "different inputs"):
                    init_task(root, "sample-task", **paths)

        for role, filename in (
            ("lyrics_dir", "extra.lrc"),
            ("source_audio_dir", "extra.wav"),
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths, _ = initialize(root)
                (paths[role] / filename).write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "different inputs"):
                    init_task(root, "sample-task", **paths)

    def test_manifest_detects_on_disk_input_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, result = initialize(root)
            manifest_path = Path(result["task_manifest"])
            manifest = load_task_manifest(manifest_path)
            paths["audio"].write_bytes(b"different-audio")

            issues = verify_manifest_inputs(manifest_path, manifest)

            self.assertTrue(any(issue.startswith("audio:") for issue in issues))

    def test_qa_schema_rejects_every_missing_contract_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, result = initialize(root)
            manifest = load_task_manifest(Path(result["task_manifest"]))
            payload = {**qa_metadata(manifest, "manual_overrides"), "1": {}}
            for key in (
                "schema_version",
                "artifact_type",
                "project",
                "source_srt_sha256",
                "task_fingerprint_sha256",
                "scope",
            ):
                with self.subTest(key=key):
                    candidate = dict(payload)
                    candidate.pop(key)
                    issues = validate_qa_artifact(
                        candidate, manifest, "manual overrides", "manual_overrides"
                    )
                    self.assertTrue(any(key in issue for issue in issues))

    def test_report_requires_fingerprint_on_every_row(self):
        with self.assertRaisesRegex(ValueError, "every report row"):
            report_fingerprint(
                [
                    {"task_fingerprint_sha256": "1" * 64},
                    {"task_fingerprint_sha256": ""},
                ]
            )

    def test_migration_preserves_cases_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = create_task_inputs(root)
            manifest = build_task_manifest(root, "sample-task", **paths)
            qa_path = root / "private/sample-task/qa/legacy.json"
            qa_path.parent.mkdir(parents=True)
            legacy = {"_source_srt_sha256": "0" * 64, "42": {"text": "keep"}}
            write_json_atomic(qa_path, legacy)

            status = migrate_file(qa_path, manifest, "manual_overrides")

            migrated = json.loads(qa_path.read_text(encoding="utf-8"))
            backup = json.loads(
                qa_path.with_name("legacy.json.schema1.bak").read_text(encoding="utf-8")
            )
            self.assertEqual(status, "migrated")
            self.assertEqual(migrated["42"], {"text": "keep"})
            self.assertNotIn("_source_srt_sha256", migrated)
            self.assertEqual(backup, legacy)

    def test_migration_preflight_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = create_task_inputs(root)
            manifest = build_task_manifest(root, "sample-task", **paths)
            qa_path = root / "private/sample-task/qa/legacy.json"
            qa_path.parent.mkdir(parents=True)
            write_json_atomic(qa_path, {"cases": []})

            status, migrated = prepare_migration(
                qa_path, manifest, "regression_cases"
            )

            self.assertEqual(status, "migrated")
            self.assertIsNotNone(migrated)
            self.assertFalse(
                qa_path.with_name("legacy.json.schema1.bak").exists()
            )
            self.assertNotIn(
                "schema_version",
                json.loads(qa_path.read_text(encoding="utf-8")),
            )

    def test_task_name_rejects_paths_and_reserved_names(self):
        for value in ("nested/task", r"nested\task", "..", "CON", "bad:name"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    task_name(value)

    def test_init_task_requires_srt_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = create_task_inputs(root)
            source = paths["source_srt"].with_suffix(".txt")
            paths["source_srt"].replace(source)
            paths["source_srt"] = source

            with self.assertRaises(ValueError):
                init_task(root, "sample-task", **paths)


class RepositoryContractTests(unittest.TestCase):
    def test_required_skill_files_exist(self):
        required = (
            "SKILL.md",
            "agents/openai.yaml",
            "references/workflow.md",
            "scripts/redo_karaoke_pipeline.py",
            "requirements.txt",
        )
        for relative_path in required:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((REPOSITORY_ROOT / relative_path).is_file())

    def test_agent_prompt_uses_explicit_skill_name(self):
        metadata = (REPOSITORY_ROOT / "agents/openai.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("$lyric-aligner", metadata)

    def test_public_workflow_uses_one_output_root(self):
        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                REPOSITORY_ROOT / "SKILL.md",
                REPOSITORY_ROOT / "references/workflow.md",
                REPOSITORY_ROOT / "references/task-template.md",
                REPOSITORY_ROOT / "references/prompt-template.txt",
            )
        )

        self.assertNotIn("output/transcribe/", public_text)
        self.assertIn("output/任务名/", public_text)

    def test_ignore_contract_covers_local_task_artifacts(self):
        ignore_text = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

        for pattern in (
            "private/",
            "output/",
            "*.srt",
            "*.lrc",
            "*歌曲清单*.txt",
            "*bpm*.txt",
            "*_manual_overrides.json",
            "*_regression_cases.json",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore_text)


if __name__ == "__main__":
    unittest.main()
