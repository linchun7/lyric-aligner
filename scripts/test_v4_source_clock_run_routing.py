from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "v4_run.py"
SPEC = importlib.util.spec_from_file_location("_v4_run_source_clock_routing_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load v4_run.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class V4SourceClockRunRoutingTests(unittest.TestCase):
    def test_source_clock_flag_selects_legacy_core(self):
        self.assertTrue(MODULE._uses_source_clock(["--source-clock-map", "clock.json"]))
        self.assertTrue(MODULE._uses_source_clock(["--source-clock-map=clock.json"]))
        self.assertFalse(MODULE._uses_source_clock(["--profile", "profile.json"]))

    def test_source_clock_main_expands_config_once_and_enters_core_implementation(self):
        saved = {
            "argv": sys.argv,
            "expand": MODULE.expand_run_config_argv,
            "strip": MODULE.strip_run_config_control_argv,
            "validate": MODULE.validate_run_output_tree_from_argv,
            "lock": MODULE.OutputRunLock,
            "core": MODULE._CORE._IMPLEMENTATION_MAIN,
            "optimized": MODULE._OPTIMIZED.main,
        }
        calls = []

        class DummyLock:
            def __init__(self, _path):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        try:
            MODULE.expand_run_config_argv = lambda _argv, repository_root: [
                "--task-manifest", "manifest.json",
                "--run-config", "config.json",
                "--source-clock-map", "clock.json",
                "--out-dir", "out",
            ]
            MODULE.strip_run_config_control_argv = lambda argv: [
                value for index, value in enumerate(argv)
                if value != "--run-config" and (index == 0 or argv[index - 1] != "--run-config")
            ]
            MODULE.validate_run_output_tree_from_argv = lambda _argv: None
            MODULE.OutputRunLock = DummyLock
            MODULE._CORE._IMPLEMENTATION_MAIN = lambda: calls.append(("core", list(sys.argv[1:]))) or 0
            MODULE._OPTIMIZED.main = lambda: calls.append(("optimized", list(sys.argv[1:]))) or 0
            sys.argv = ["v4_run.py", "--task-manifest", "manifest.json"]
            self.assertEqual(MODULE.main(), 0)
        finally:
            sys.argv = saved["argv"]
            MODULE.expand_run_config_argv = saved["expand"]
            MODULE.strip_run_config_control_argv = saved["strip"]
            MODULE.validate_run_output_tree_from_argv = saved["validate"]
            MODULE.OutputRunLock = saved["lock"]
            MODULE._CORE._IMPLEMENTATION_MAIN = saved["core"]
            MODULE._OPTIMIZED.main = saved["optimized"]

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "core")
        self.assertIn("--source-clock-map", calls[0][1])
        self.assertNotIn("--run-config", calls[0][1])


if __name__ == "__main__":
    unittest.main()
