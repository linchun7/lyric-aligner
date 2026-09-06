import tempfile
import unittest
from pathlib import Path

from lyric_aligner.alignment.production_backend_profiles import (
    PRODUCTION_BOUNDARY_BACKEND_PROFILES,
    ProductionBackendProfileError,
    ProductionBoundaryBackendProfile,
    get_production_boundary_backend_profile,
    validate_profile_registry,
)


class ProductionBackendProfileTests(unittest.TestCase):
    def test_registry_has_two_distinct_direct_alignment_identities(self):
        validate_profile_registry()
        self.assertEqual(set(PRODUCTION_BOUNDARY_BACKEND_PROFILES), {"sofa_mandarin_v1", "hubertfa_mandarin_v1"})
        profiles = list(PRODUCTION_BOUNDARY_BACKEND_PROFILES.values())
        self.assertEqual(len({p.backend_id for p in profiles}), 2)
        self.assertEqual(len({p.correlation_group for p in profiles}), 2)
        self.assertEqual(
            {p.family for p in profiles},
            {"final_mix_singing_alignment", "final_mix_forced_alignment"},
        )

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(ProductionBackendProfileError):
            get_production_boundary_backend_profile("not-a-profile")

    def test_adapter_contract_revision_changes_when_bound_core_contract_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sidecar = root / "private" / "sidecar"
            runtime = sidecar / "runtime" / ".venv" / "Scripts"
            runtime.mkdir(parents=True)
            (runtime / "python.exe").write_bytes(b"fake-python")
            (sidecar / "adapter.py").write_text("pass\n", encoding="utf-8")
            (sidecar / "model.onnx").write_bytes(b"fake-model")
            contract = root / "observer_contract.py"
            contract.write_text("REVISION = 1\n", encoding="utf-8")
            profile = ProductionBoundaryBackendProfile(
                profile_id="fixture",
                backend_id="fixture-backend",
                backend_version="1",
                family="final_mix_forced_alignment",
                correlation_group="fixture-group",
                model_id="fixture-model",
                supported_languages=("en",),
                sidecar_root_relpath="private/sidecar",
                adapter_relpath="private/sidecar/adapter.py",
                model_relpath="private/sidecar/model.onnx",
                contract_relpaths=("observer_contract.py",),
            )
            first = profile.resolve(root, language="en")["adapter_contract_revision"]
            contract.write_text("REVISION = 2\n", encoding="utf-8")
            second = profile.resolve(root, language="en")["adapter_contract_revision"]
            self.assertNotEqual(first, second)

    def test_resolve_derives_model_revision_without_loading_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sidecar = root / "private" / "sidecar"
            runtime = sidecar / "runtime" / ".venv" / "Scripts"
            runtime.mkdir(parents=True)
            interpreter = runtime / "python.exe"
            interpreter.write_bytes(b"fake-python")
            adapter = sidecar / "adapter.py"
            adapter.write_text("pass\n", encoding="utf-8")
            model = sidecar / "model.onnx"
            model.write_bytes(b"fake-model")
            profile = ProductionBoundaryBackendProfile(
                profile_id="fixture",
                backend_id="fixture-backend",
                backend_version="1",
                family="final_mix_forced_alignment",
                correlation_group="fixture-group",
                model_id="fixture-model",
                supported_languages=("en",),
                sidecar_root_relpath="private/sidecar",
                adapter_relpath="private/sidecar/adapter.py",
                model_relpath="private/sidecar/model.onnx",
            )
            resolved = profile.resolve(root, language="en")
            self.assertEqual(resolved["profile_id"], "fixture")
            self.assertEqual(len(resolved["model_revision"]), 64)
            self.assertEqual(len(resolved["implementation_revision"]), 64)
            self.assertIn(str(interpreter), resolved["command"])
            self.assertIn(str(adapter), resolved["command"])
            self.assertIn(str(adapter), resolved["boundary_command"])
            self.assertIn(str(adapter), resolved["internal_command"])
            self.assertIn(str(adapter), resolved["batch_command"])
            self.assertEqual(resolved["boundary_adapter_path"], str(adapter.resolve()))
            self.assertEqual(resolved["internal_adapter_path"], str(adapter.resolve()))
            self.assertEqual(resolved["batch_adapter_path"], str(adapter.resolve()))
            self.assertEqual(len(resolved["adapter_revision"]), 64)
            self.assertEqual(resolved["adapter_revision"], resolved["boundary_adapter_revision"])
            self.assertEqual(resolved["adapter_revision"], resolved["internal_adapter_revision"])
            self.assertEqual(resolved["adapter_revision"], resolved["batch_adapter_revision"])
            self.assertEqual(len(resolved["adapter_contract_revision"]), 64)


if __name__ == "__main__":
    unittest.main()
