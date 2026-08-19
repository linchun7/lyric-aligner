# V4 hardening review fixes — 2026-08-19

This hardening pass follows the post-merge review of PR #25. It is deliberately narrow: no Source-to-Mix algorithm, calibration threshold, cut/overlap decision, readiness policy, or authoritative V4 timeline semantics are changed.

## Text-only repair hardening

The text-only path now fails closed when the monotonic alignment skips canonical lyric occurrences. A canonical line that has no subtitle cue is reported under `unmatched_canonical`, contributes to `review_count`, and makes the report `review_required`; the path still never invents a new cue or timing.

Timed LRC/QRC occurrences are stable-sorted by their actual timestamp within each canonical file when all parsed lyric rows in that file are timed. This fixes interleaved multi-timestamp repeat rows while preserving the caller-supplied multi-song file order. Mixed timed/untimed files retain source order rather than guessing missing timing.

Automatic text replacement now preserves the source SRT punctuation, whitespace, and line breaks. Only lexical/content characters are replaced from canonical text. If canonical lexical content cannot map one-for-one onto the existing cue layout, the cue remains unchanged and becomes `review_required` with `format_preserving_replacement_unsafe`.

## Production output locking

The public `scripts/v4_run.py` entrypoint now acquires an exclusive `.v4-run.lock` inside the selected `--out-dir`. A second orchestrator targeting the same output tree fails closed instead of racing on assets, primary mappings, transitions, resume state, or final materializations.

The lock contains an ownership token and is removed only by the owner that created it. A stale lock after abnormal process termination is intentionally not guessed away automatically; the operator must first verify that no V4 run is active before removing it.

## Regression coverage

New focused tests cover:

- missing canonical lyric occurrence => `review_required`;
- chronological ordering of interleaved multi-timestamp LRC repeats;
- caller-supplied song order across multiple canonical files;
- punctuation/spacing/line-break preservation during word correction;
- punctuation-only differences not causing source reformatting;
- length-changing replacement failing closed;
- second lock on the same output directory failing closed;
- lock release after exceptions;
- lock ownership preventing deletion of a replacement lock.
