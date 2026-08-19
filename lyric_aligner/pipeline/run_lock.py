"""Exclusive lock for one V4 production output directory."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


class OutputRunLock:
    """Fail closed when two production orchestrators target the same out-dir."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir).resolve()
        self.path = self.out_dir / ".v4-run.lock"
        self._acquired = False
        self._token = secrets.token_hex(16)

    def __enter__(self) -> "OutputRunLock":
        self.out_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(
                "another V4 production run is already using this out-dir; "
                f"verify no run is active before removing {self.path}"
            ) from exc
        try:
            payload = json.dumps(
                {
                    "schema_version": "1.0",
                    "pid": os.getpid(),
                    "token": self._token,
                },
                ensure_ascii=True,
                sort_keys=True,
            ) + "\n"
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        self._acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._acquired:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                payload = {}
            if secrets.compare_digest(str(payload.get("token") or ""), self._token):
                self.path.unlink(missing_ok=True)
            self._acquired = False
        return False
