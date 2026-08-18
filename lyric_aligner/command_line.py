"""Cross-platform parsing for configured external commands.

The project stores external aligner commands as strings but always executes
parsed argv with ``shell=False``. Windows ``shlex`` parsing preserves matching
outer quotes, so normalize those tokens before executable discovery or runtime
identity calculation. All callers share this helper to avoid readiness/runtime
identity disagreeing with actual execution.
"""

from __future__ import annotations

import os
import shlex


class CommandLineParseError(ValueError):
    """Raised when a configured command cannot be parsed safely."""


def _strip_matching_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def split_external_command(
    command: str | None,
    *,
    windows: bool | None = None,
) -> list[str]:
    """Split one configured command into argv without invoking a shell.

    ``windows`` is injectable for deterministic contract tests; production
    callers leave it as ``None`` so the current OS decides the parsing mode.
    Malformed quoting fails closed instead of falling back to shell parsing.
    """

    text = str(command or "").strip()
    if not text:
        return []
    is_windows = os.name == "nt" if windows is None else bool(windows)
    try:
        values = shlex.split(text, posix=not is_windows)
    except ValueError as exc:
        raise CommandLineParseError("external command cannot be parsed") from exc

    normalized: list[str] = []
    for raw in values:
        value = str(raw)
        if is_windows:
            value = _strip_matching_outer_quotes(value)
        if value:
            normalized.append(value)
    return normalized
