from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lyric_aligner.runtime_snapshot import (
    build_runtime_snapshot,
    validate_runtime_snapshot,
)


class RuntimeSnapshotTests(unittest.TestCase):
    def _build(self, root: Path, **kwargs):
        with (
            mock.patch(
                "lyric_aligner.runtime_snapshot._git_identity",
                return_value={
                    "commit_sha": "a" * 40,
                    "branch": "main",
                    "dirty": False,
                },
            ),
            mock.patch(
                "lyric_aligner.runtime_snapshot._binary_version",
                side_effect=lambda name: {
                    "available": True,
                    "version_line": f"{name} version 1.0",
                },
            ),
            mock.patch(
                "lyric_aligner.runtime_snapshot._package_versions",
                return_value={"numpy": "2.0"},
            ),
        ):
            return build_runtime_snapshot(
                repo_root=root, packages=("numpy",), **kwargs
            )

    def test_snapshot_hash_is_stable_for_same_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._build(
                root, models={"asr": "Systran/faster-whisper-large-v3"}
            )
            second = self._build(
                root, models={"asr": "Systran/faster-whisper-large-v3"}
            )
        self.assertEqual(
            first["runtime_identity_sha256"], second["runtime_identity_sha256"]
        )
        self.assertEqual(first["models"]["asr"]["kind"], "logical_id")
        self.assertEqual(
            validate_runtime_snapshot(first), first["runtime_identity_sha256"]
        )

    def test_local_model_path_and_full_command_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_root = "/" + "Users" + "/" + "example"
            secret_model = f"{secret_root}/private/checkpoints/model.bin"
            secret_command = (
                f"{secret_root}/bin/aligner --token SECRET --input private.wav"
            )
            payload = self._build(
                root,
                models={"forced": secret_model},
                external_forced_aligner_command=secret_command,
            )
            rendered = json.dumps(payload)
        self.assertNotIn(secret_model, rendered)
        self.assertNotIn(secret_command, rendered)
        self.assertNotIn("SECRET", rendered)
        self.assertEqual(
            payload["models"]["forced"]["kind"], "local_path_redacted"
        )
        self.assertEqual(
            payload["models"]["forced"]["basename"], "model.bin"
        )
        self.assertEqual(
            payload["external_forced_aligner"]["executable_basename"],
            "aligner",
        )
        self.assertEqual(
            payload["external_forced_aligner"]["argument_count"], 4
        )

    def test_identity_changes_when_model_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._build(root, models={"asr": "model-a"})
            second = self._build(root, models={"asr": "model-b"})
        self.assertNotEqual(
            first["runtime_identity_sha256"], second["runtime_identity_sha256"]
        )

    def test_tampered_metadata_does_not_validate_against_old_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(Path(tmp), models={"asr": "model-a"})
        payload["packages"]["numpy"] = "9.9-tampered"
        with self.assertRaises(ValueError):
            validate_runtime_snapshot(payload)


if __name__ == "__main__":
    unittest.main()
