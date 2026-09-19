from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.review.model_text_adjudication import generate_proposals, verify_and_materialize

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=Path.cwd())
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--proposal-out", type=Path, required=True)
    args = ap.parse_args()
    payload = generate_proposals(args.repo_root, args.run_dir, args.proposal_out)
    report = verify_and_materialize(args.repo_root, args.run_dir, args.output_dir, args.proposal_out)
    print(json.dumps({"proposal_count": payload["proposal_count"], "verified_new_resolved": report["verified_new_resolved"], "remaining_manual": report["remaining_manual"]}))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
