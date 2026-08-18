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
- P7 external source forced alignment：`9ad6df4f04b396871f757422bcb35f1fa7676678`。

P7 head `2ee9e1d2ced75c3d24b5a00353e9f275fc9dc9f9` 的 validate #560 全绿后合入。

---

## 2026-08-18 — P8 Forced Alignment Source-to-Mix Projection

### 1. 目标

P7 输出的是 absolute source-ms；editor、ASR 与 P4 fusion 使用 mix-ms。P8 只负责把 P7 forced-alignment line/spans 通过当前 occurrence 的 exact Source-to-Mix 映射投影到 edited-mix time，避免跨时基直接比较。

新增：

```text
lyric_aligner/alignment/forced_projection.py
scripts/v4_project_forced_alignment.py
scripts/test_v4_forced_mix_projection.py
scripts/test_v4_forced_mix_projection_end_to_end.py
```

并更新：

```text
lyric_aligner/alignment/__init__.py
```

### 2. Authority 不变

```text
canonical_text_authority = canonical_lyrics_only
primary_timing_authority = source_to_mix
forced_alignment_timing = auxiliary_evidence_only
```

P8 不让 forced aligner 直接拥有 final timing，也不改 canonical text/order。

### 3. Continuous mapping

`AFFINE` 与 `PIECEWISE_RATE` 复用既有 `mix_time_for_source()` analytical inverse，不新增第二套映射实现。line 与 character spans 都从 source boundary 投影到 mix boundary。

### 4. CUT_AWARE fail-closed

对 confirmed cut：

- line 两端必须落在同一个 retained source segment；
- boundary 落在 confirmed source gap -> `unprojectable`；
- line 跨 confirmed cut -> `unprojectable`；
- 绝不把 cut 两侧强行桥接成一个假的连续 mix interval；
- spans 独立投影，cut 两侧仍可各自保留可用局部证据。

### 5. Mapping scope / lineage

只解析 forced-evidence 实际引用的 occurrences：

```text
mapping_scope = forced_evidence_occurrences_only
```

因此 unrelated blocked occurrence 不阻塞局部 forced projection；但 relevant occurrence 的 mapping 缺失、blocked、artifact 未绑定或 provenance 不一致仍 fail closed。

输出同时绑定：

- source run artifact；
- P7 forced evidence artifact；
- exact coarse/fine/cut mapping artifacts actually used。

### 6. Artifact

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

每个 job 保留原 forced-alignment identity、source boundaries、projection status/reason，并在可投影时增加 mix boundaries。正式输出继续不复制 canonical raw lyric text。

### 7. Tests

覆盖：

- AFFINE projection；
- PIECEWISE_RATE projection；
- CUT_AWARE same-segment projection；
- confirmed gap boundary -> unprojectable；
- cross-cut line -> unprojectable；
- spans 独立投影；
- relevant mapping 缺失/blocked fail closed；
- unrelated blocked occurrence isolation；
- artifact lineage/provenance tamper；
- CLI E2E 与 privacy。

### 8. CI / accuracy 边界

公共 Actions 只能证明 deterministic timebase projection、cut semantics、lineage/privacy；不能证明真实 WhisperX/SOFA/MFA checkpoint 对歌声准确，也不能证明 fusion/release threshold 已校准。

P8 合入后，forced mix evidence 才有资格作为独立 acoustic family 进入多证据融合；在 private calibration/blind 完成前仍不得自动提升为 final boundary authority。
