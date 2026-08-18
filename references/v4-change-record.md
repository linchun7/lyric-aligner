# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 已合入 main

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed overlap：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed cut/CUT_AWARE：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut+overlap composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`；
- P1 strict calibration/blind：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`；
- P1.1 private dataset readiness：`ad6c403a56209e945a9a61a1eeab1a4bc3c204b4`；
- P2 editor shadow evidence：`2e96569189ac6eb16d987fb2f304403696bc809b`；
- P3 local acoustic evidence：`cd3420750c06a55fa1af7d6314ec56971e728928`；
- P4 evidence fusion shadow：`bc4e10760ffee2e5990ca580d5edbadd7d561eaf`；
- P5 ASR second-pass routing：`1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d`；
- P6 ASR second-pass execution/composite：`6eacacc50e885684b0265e3abea729b19b1b7725`；
- P7 external source forced alignment：`9ad6df4f04b396871f757422bcb35f1fa7676678`；
- P8 forced alignment source-to-mix projection：PR #17，merge `00585a07b658ffea93509c4ed1a4b129deafd0a3`；
- P9 forced alignment multi-family shadow fusion：PR #19，merge `efbdbb926b03efdf1d91622d5c23cabef1f9850c`。

P7 head `2ee9e1d2ced75c3d24b5a00353e9f275fc9dc9f9` 的 validate #560 全绿后合入。P8 latest result tree 在 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 在 fast-core #2 完成同级验证并跑完 **324 tests** 全绿后，与 P8 main 同步 ancestry，再合入 main。

---

## 2026-08-18 — P8 Forced Alignment Source-to-Mix Projection

P8 将 P7 absolute source-ms forced evidence 通过当前 occurrence 的 exact Source-to-Mix 映射投影到 edited-mix time，避免跨时基比较。

新增：

```text
lyric_aligner/alignment/forced_projection.py
scripts/v4_project_forced_alignment.py
scripts/test_v4_forced_mix_projection.py
scripts/test_v4_forced_mix_projection_end_to_end.py
```

Authority 不变：canonical lyrics 是 final text/order truth，Source-to-Mix 是 primary timing truth，forced alignment 只是 auxiliary evidence。

`AFFINE` / `PIECEWISE_RATE` 复用 `mix_time_for_source()`；`CUT_AWARE` 对 confirmed source gap 或 cross-cut line 标记 `unprojectable`，绝不 bridge；spans 独立投影。只解析 forced-evidence 实际引用 occurrences，relevant mapping/provenance 缺失仍 fail closed。

Artifact：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

公共 CI 只能证明 projection/cut/lineage/privacy，不能证明真实 forced-aligner checkpoint 对歌声准确。

---

## 2026-08-18 — P9 Forced Alignment Multi-Family Shadow Fusion

### 1. 目标

P8 让 forced evidence 与 editor/ASR 都进入 mix time 后，P9 才允许它作为独立 auxiliary family 进入 evidence fusion。仍然是 shadow-only：不修改 canonical text、Source-to-Mix、render timeline 或 release eligibility。

更新：

```text
lyric_aligner/evidence/fusion.py
scripts/v4_fuse_evidence.py
scripts/test_v4_evidence_fusion_end_to_end.py
```

新增：

```text
scripts/test_v4_forced_evidence_fusion.py
```

### 2. Forced family contract

只接受：

```text
mode = forced_alignment_mix_projection
source_evidence_backend = external_forced_aligner
primary_timing_authority = source_to_mix_only
forced_alignment_authority = auxiliary_acoustic_evidence_only
```

每个 forced job 必须绑定已知 occurrence/canonical line/track/canonical_text_sha256；job IDs 与 canonical line identity 必须唯一。`projected` job 才提供 mix boundary；`unprojectable` 会显式进入 diagnostics，但不计为可用 auxiliary boundary family。`unprojectable` payload 若携带 mix boundary 会 fail closed。

### 3. Conflict policy

P4 旧版只计算 editor↔ASR disagreement。P9 改为三 family pairwise fail-closed：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

只要任意可用 auxiliary pair 的 onset/offset 最大分歧超过 `conflict_boundary_ms`，整行 shadow state 为 `CONFLICT`。不会用多数票掩盖 outlier。

无冲突时仍按未校准 shadow 规则：0 family=`LOW`，1=`MEDIUM`，>=2=`HIGH`。这里的 `HIGH` 仍不是 release confidence。

新增 diagnostics：

```text
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
summary.forced_alignment_line_counts
```

### 4. CLI / lineage

`v4_fuse_evidence.py` 新增：

```text
--forced-mix-evidence
--forced-mix-evidence-artifact
```

P8 artifact 必须与当前 source run 同 task、同 algorithm version、同 source_run_artifact_id，并把 source run 放在 upstream。fusion artifact 保存所有实际输入 artifacts 的 lineage，并记录 forced artifact ID 与 `any_auxiliary_pair_over_threshold_blocks` conflict policy。

### 5. Safety boundary

P9 固定：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

这就是代码阶段的刻意收口点。下一阶段必须使用 private real-song calibration/blind 来决定 family 的实际独立性、不同语言/风险类型阈值，以及是否允许任何自动 timing refinement。公共 synthetic CI 不得用于宣称真实 accuracy。
