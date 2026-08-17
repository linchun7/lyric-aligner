"""Fail-closed text decoding policy for multilingual task inputs."""

from __future__ import annotations

from pathlib import Path


class TextEncodingError(ValueError):
    """Raised when task text cannot be decoded under the declared policy."""


def read_task_text(path: Path, *, encoding: str | None = None) -> str:
    """Read UTF-8 by default; legacy encodings require explicit declaration.

    This intentionally does not guess GB18030/CP949/Shift-JIS. A wrong decoder
    can produce valid Unicode with the wrong script, which is more dangerous
    than a visible decode failure for canonical lyrics.
    """

    chosen = encoding or "utf-8-sig"
    try:
        return path.read_text(encoding=chosen)
    except UnicodeDecodeError as exc:
        if encoding is None:
            raise TextEncodingError(
                f"{path} is not valid UTF-8; declare the source encoding explicitly "
                "or convert the task input to UTF-8 before production use"
            ) from exc
        raise TextEncodingError(
            f"{path} cannot be decoded as explicitly declared encoding {encoding!r}"
        ) from exc
