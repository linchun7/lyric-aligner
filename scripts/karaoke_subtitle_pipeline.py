#!/usr/bin/env python3
"""Retired pre-v4 diagnostic helper.

This filename is retained only so historical invocations fail with an explicit
migration message instead of silently running obsolete alignment logic.
"""
from __future__ import annotations

import sys

MIGRATION = """\
`scripts/karaoke_subtitle_pipeline.py` has been retired.

It was a pre-v4 diagnostic/draft helper and is not a production authority.
Use the current workflow in `references/workflow.md` instead:

  inspect / task setup        -> scripts/init_task.py + current task contracts
  correct / source-timed      -> scripts/v4_text_repair.py (trusted timing)
                                 or scripts/v4_smart_repair.py
  bounded ASR evidence        -> current Pro ASR planning/execution path
  full acoustic realignment   -> scripts/v4_run.py (Max)

The historical implementation remains recoverable from Git history. It is not
forwarded automatically because its old SRT/LRC/ASR authority rules are not
semantically equivalent to the current v4 contracts.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stream = sys.stdout if any(arg in {"-h", "--help"} for arg in args) else sys.stderr
    print(MIGRATION, file=stream)
    return 0 if stream is sys.stdout else 2


if __name__ == "__main__":
    raise SystemExit(main())
