import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from check_environment import check_environment
from init_task import init_task, sha256, task_name, validate_existing_scope


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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
    def test_init_task_creates_scoped_qa_skeletons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nexample\n",
                encoding="utf-8",
            )

            result = init_task(root, "sample-task", source)

            overrides_path = Path(result["manual_overrides"])
            regression_path = Path(result["regression_cases"])
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            regression = json.loads(regression_path.read_text(encoding="utf-8"))
            self.assertTrue((root / "private/sample-task/input").is_dir())
            self.assertTrue((root / "output/sample-task").is_dir())
            self.assertEqual(overrides["source_srt_sha256"], sha256(source))
            self.assertEqual(regression["source_srt_sha256"], sha256(source))
            self.assertEqual(regression["cases"], [])

    def test_init_task_does_not_overwrite_existing_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text("source", encoding="utf-8")
            result = init_task(root, "sample-task", source)
            overrides_path = Path(result["manual_overrides"])
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            overrides["preserve"] = True
            overrides_path.write_text(
                json.dumps(overrides, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            init_task(root, "sample-task", source)

            preserved = json.loads(overrides_path.read_text(encoding="utf-8"))
            self.assertTrue(preserved["preserve"])

    def test_init_task_rejects_existing_qa_for_another_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_source = root / "first.srt"
            second_source = root / "second.srt"
            first_source.write_text("first", encoding="utf-8")
            second_source.write_text("second", encoding="utf-8")
            init_task(root, "sample-task", first_source)

            with self.assertRaisesRegex(ValueError, "different source SRT"):
                init_task(root, "sample-task", second_source)

    def test_existing_qa_requires_valid_json_object_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qa.json"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON object"):
                validate_existing_scope(path, "0" * 64)

    def test_task_name_rejects_paths_and_reserved_names(self):
        for value in ("nested/task", r"nested\task", "..", "CON", "bad:name"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    task_name(value)

    def test_init_task_requires_srt_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")

            with self.assertRaises(ValueError):
                init_task(root, "sample-task", source)


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
