# V4 execution optimization contract

This document describes execution-cost optimizations around the authoritative V4 production chain. They do **not** change canonical-text authority, Source-to-Mix timing authority, calibration thresholds, cut/overlap decisions, review policy, timeline semantics, or release gates.

## 0. Semantic run configuration is not an execution optimization

`4.0.0a14` adds task-local `qa/v4_run_config.json` for `profile / language_map / middle_cut_map / lyric_role_map`. Public Max wrappers validate and expand that semantic configuration **before** resume/worker optimization and before output-tree mutation. It is not a cache hint: a bound semantic file that changed content, a task fingerprint mismatch, or CLI/config drift is a hard configuration error rather than a resume miss.

`workers / no-resume / out-dir` remain execution-only controls and are deliberately excluded from the semantic run config. Formal TrackAsset artifacts continue to record the actual semantic-file SHA identities consumed by asset resolution.

## 1. Safe artifact resume

`python scripts/v4_run.py ...` may reuse existing **coarse**, **fine**, and **transition** stage artifacts only when `--git-commit` is supplied and all reuse checks pass.

`--git-commit` predates resume support and remains backward-compatible producer metadata. It never becomes a new execution precondition: arbitrary historical/test values still run normally and are recorded as before. Cross-run resume is enabled only when that value also exactly equals the currently checked-out `git rev-parse HEAD` and the Git worktree is clean. A mismatched, dirty, unavailable, or empty Git identity simply disables cross-run resume for that invocation; it does not reject the production run.

A reusable stage must match all of the following:

- current task fingerprint;
- current lyric-aligner algorithm version;
- exact stage name;
- exact producer `git_commit` backed by the current clean HEAD;
- exact upstream artifact-id set;
- formal output role, file size and SHA-256;
- stage-specific identity such as occurrence id;
- for coarse mappings, the requested mix interval;
- internally recomputed artifact id;
- a self-validating disposable resume sidecar bound to the same artifact id, git commit and current runtime identity.

Runtime resume identity currently hashes Python implementation/version, OS/release/machine, NumPy, SciPy, librosa, soundfile, soxr, numba and libsndfile versions. If the runtime changes, the old formal artifact remains valid lineage/evidence, but it is **not** automatically reused for execution.

Any mismatch is a cache miss, not a warning override: the stage executes again. Existing artifacts produced before the runtime sidecar existed are therefore recomputed once before becoming resume-eligible. Asset resolution is intentionally **not** reused across runs because optional profile/map inputs can live outside the task fingerprint.

`--no-resume` forces a fresh run even when a verified Git identity and reusable artifacts exist; a fresh runtime-bound sidecar may still be produced for a future invocation.

## 2. Same-invocation verified-input session

The parent `v4_run.py` always clears inherited verification-session variables and performs the normal full `task_manifest` input verification first.

A cheap file-stat/directory-membership snapshot is taken before the parent SHA-256 pass, checked again immediately after verification, and checked once more after session creation. If size, nanosecond mtime, or directory membership changes across that window, execution fails closed rather than converting the old digest into a fresh attestation.

Only after full verification succeeds does the parent create a fresh random-token session under `output/<task>/v4/cache/`. The token itself is not stored in plaintext in the session file. Child stages receive the token through their inherited process environment, and the parent clears the environment variables on exit.

The internal `v4_child_exec.py` bootstrap can skip a second content read only when the fresh session attests the same:

- manifest path identity and manifest SHA;
- task fingerprint and input-role SHA;
- exact file path identity;
- file size and nanosecond mtime;
- directory file set for directory roles.

Raw absolute task-input paths are not persisted in the session payload. Exact path identity is represented by SHA-256, so the disposable session does not add a plaintext username/drive/private-directory path leak.

If the token is absent/wrong, the manifest changes, a file stat changes, or a directory file set changes, the child falls back to the original SHA-256 verification path. Standalone V4 CLIs therefore keep their previous default verification behavior.

The same fresh attestation is also used by asset resolution when it needs the already-verified source-audio/LRC digest for TrackAsset identity. Optional external profile/map files are still hashed normally.

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

## 4. Bounded mix decode

Coarse and Fine acoustic stages decode only the current requested mix interval plus a fixed 2-second context margin instead of treating the full mix as their working waveform. This reduces repeated I/O and RAM pressure on long projects without changing retrieval windows, source features, candidate pools, TimeWarp thresholds, review policy, or artifact authority.

Compressed containers can report a physical duration a few samples beyond what the decoder returns at the exact tail. A bounded decode may clamp to the real decodable end only when the requested interval itself reaches the physical tail and the shortfall is at most 5 ms, while preserving the historical one-sample rounding tolerance. Mid-file short reads, larger terminal shortfalls, and decodes that do not cover the requested interval remain hard failures. Missing tail audio is never zero-padded into timing authority.

## 5. Authoritative production semantics remain shared

The serial production semantics are retained in `scripts/v4_run_legacy.py`. The public `scripts/v4_run.py` entrypoint routes through `scripts/v4_run_optimized.py`, which prestages reusable/parallel execution work and then follows the same authoritative production semantics. Optimized and legacy entrypoints share production-plan rules such as conservative `content_end`; execution optimization must not create a lower-accuracy or different-authority result.

The core still performs the original deterministic processing order for:

- effective TimeWarp selection;
- discontinuity issue materialization;
- canonical timeline projection;
- transition overlap/ambiguity issue materialization;
- readiness status;
- final `v4_run.json` and `v4_run.artifact.json` lineage.

Timeline output is intentionally rebuilt on every run rather than cross-run cached. It is cheap compared with acoustic alignment and keeping it fresh makes final issue/lineage materialization easier to audit.

The optimizer does not create a lower-accuracy fast mode.

## 6. Disposable observability

Each successful optimized invocation writes:

`cache/execution_summary.json`

It contains worker count plus resume/memo/execution counters. Runtime resume sidecars and the verified-input session are disposable local execution state. None of them is included in formal artifact lineage, so cache hits or worker scheduling do not alter semantic artifact identity.
