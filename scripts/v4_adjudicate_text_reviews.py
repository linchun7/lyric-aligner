from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.review.text_review_adjudication import adjudicate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=Path.cwd())
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    report = adjudicate(repo_root=args.repo_root, run_dir=args.run_dir, output_dir=args.output_dir)
    print(f"before={report['before']} auto_resolved={report['auto_resolved']} remaining_manual={report['remaining_manual']} normalized_changes={report['actual_normalized_text_changes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
