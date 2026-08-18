# V4 execution optimization contract

This document describes execution-cost optimizations around the authoritative V4 production chain. They do **not** change canonical-text authority, Source-to-Mix timing authority, calibration thresholds, cut/overlap decisions, review policy, timeline semantics, or release gates.

## 1. Safe artifact resume

`python scripts/v4_run.py ...` may reuse existing **coarse**, **fine**, and **transition** stage artifacts only when `--git-commit` is supplied and all reuse checks pass.

A reusable stage must match all of the following:

- current task fingerprint;
- current lyric-aligner algorithm version;
- exact stage name;
- exact producer `git_commit`;
- exact upstream artifact-id set;
- formal output role, file size and SHA-256;
- stage-specific identity such as occurrence id;
- for coarse mappings, the requested mix interval;
- internally recomputed artifact id.

Any mismatch is a cache miss, not a warning override: the stage executes again. Asset resolution is intentionally **not** reused across runs because optional profile/map inputs can live outside the task fingerprint.

Resume is disabled when `--git-commit` is absent. `--no-resume` forces a fresh run even when reusable artifacts exist.

## 2. Same-invocation verified-input session

The parent `v4_run.py` always clears inherited verification-session variables and performs the normal full `task_manifest` input verification first.

Only after that full verification succeeds does it create a fresh random-token session under `output/<task>/v4/cache/`. The token itself is not stored in plaintext in the session file. Child stages receive the token through their inherited process environment.

The internal `v4_child_exec.py` bootstrap can skip a second content read only when the fresh session attests the same:

- manifest path and manifest SHA;
- task fingerprint and input-role SHA;
- exact file path;
- file size and nanosecond mtime;
- directory file set for directory roles.

If the token is absent/wrong, the manifest changes, a file stat changes, or a directory file set changes, the child falls back to the original SHA-256 verification path. Standalone V4 CLIs therefore keep their previous default verification behavior.

This session is disposable execution state. It is never timing evidence and never an upstream artifact.

## 3. Bounded workers

The production entrypoint pre-executes independent subprocess stages with a fixed worker bound:

```text
--workers 1..4
```

Default: `--workers 2`.

Dependency order is unchanged:

1. asset resolution;
2. all independent primary coarse mappings;
3. fine mappings only for coarse results that require refinement;
4. independent left/right transition-window coarse mappings;
5. transition probes after both boundary mappings exist;
6. original authoritative orchestration/materialization pass.

`--workers 1` is the serial fallback. More than four workers is rejected to avoid unbounded CPU/RAM amplification from concurrent librosa/NumPy processes.

Each stage writes unique formal output paths. The existing source-feature cache uses temporary files plus atomic `os.replace`, so concurrent same-source cache misses cannot expose a partially written cache file.

## 4. Authoritative core remains unchanged

The historical production orchestration implementation is retained as `scripts/v4_run_legacy.py`. The public `scripts/v4_run.py` entrypoint routes through `scripts/v4_run_optimized.py`, which prestages execution work and then invokes that unchanged core.

The core still performs the original deterministic processing order for:

- effective TimeWarp selection;
- discontinuity issue materialization;
- canonical timeline projection;
- transition overlap/ambiguity issue materialization;
- readiness status;
- final `v4_run.json` and `v4_run.artifact.json` lineage.

The optimizer does not create a lower-accuracy fast mode.

## 5. Disposable observability

Each successful optimized invocation writes:

`cache/execution_summary.json`

It contains worker count plus resume/memo/execution counters. This file is explicitly disposable and is **not** included in formal artifact lineage, so cache hits or worker scheduling do not alter semantic artifact identity.
