"""Stage runner variant that uses the same-invocation verification bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lyric_aligner.contracts.verification_session import (
    SESSION_PATH_ENV,
    SESSION_TOKEN_ENV,
)
from lyric_aligner.pipeline.stage_runner import SafeStageRunner


class VerifiedStageRunner(SafeStageRunner):
    """SafeStageRunner that accelerates child verification when session-bound."""

    def _execution_command(self, command: list[str]) -> list[str]:
        if len(command) < 2:
            return command
        if not os.environ.get(SESSION_PATH_ENV) or not os.environ.get(SESSION_TOKEN_ENV):
            return command
        target = Path(command[1]).resolve()
        scripts_root = self.repository_root / "scripts"
        try:
            target.relative_to(scripts_root)
        except ValueError:
            return command
        bootstrap = scripts_root / "v4_child_exec.py"
        if target == bootstrap:
            return command
        return [command[0], str(bootstrap), str(target), *command[2:]]

    def run(self, command: list[str], *, allow_resume: bool = True) -> str:
        key = tuple(command)
        with self._lock:
            memo = self._memo.get(key)
            if memo is not None:
                self._memo_hits += 1
                return memo

        if allow_resume:
            reusable, reason = self._check_reusable(command)
            if reusable:
                with self._lock:
                    self._resume_hits += 1
                    self._memo[key] = ""
                return ""
            if self.resume_enabled and reason not in {"unsupported_stage", "resume_disabled"}:
                self._record_miss(reason)

        execution_command = self._execution_command(command)
        completed = subprocess.run(
            execution_command,
            cwd=self.repository_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"stage failed ({' '.join(command[:2])}): {detail}")
        output = completed.stdout.strip()
        if output:
            print(output, file=sys.stderr)
        with self._lock:
            self._executed += 1
            self._memo[key] = output
        return output
