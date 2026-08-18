# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-forced-alignment-mix-projection`  
当前 main：`9ad6df4f04b396871f757422bcb35f1fa7676678`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

已合入增量：

```text
P1    strict calibration/blind framework
      1c6babe37067c217d14a7404aa0ed6a1c4779a00
P1.1  private dataset scaffold/readiness
      ad6c403a56209e945a9a61a1eeab1a4bc3c204b4
P2    editor/Jianying multilingual shadow evidence
      2e96569189ac6eb16d987fb2f304403696bc809b
P3    local acoustic planner/backend/faster-whisper executor
      cd3420750c06a55fa1af7d6314ec56971e728928
P4    shadow multi-family evidence fusion
      bc4e10760ffee2e5990ca580d5edbadd7d561eaf
P5    bounded ASR second-pass routing
      1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d
P6    ASR second-pass execution + composite evidence
      6eacacc50e885684b0265e3abea729b19b1b7725
P7    external source forced alignment
      9ad6df4f04b396871f757422bcb35f1fa7676678
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均在 ASR environment + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

## 2. P7 baseline：External Source Forced Alignment

P7 建立 backend-neutral external subprocess JSON 协议，不把 ASR 冒充 forced alignment，也不假设 WhisperX/SOFA/MFA 已安装。正式 evidence：

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

Source/canonical identity、live source SHA、backend/model revision、original source window、line/spans bounds 全部 fail closed；canonical raw text、local source path、full command、stdout/stderr 不进入正式 evidence/artifact。

Forced alignment 的 authority 仍固定为 auxiliary source acoustic evidence；canonical lyric 和 Source-to-Mix authority 不变。

## 3. 当前 P8：Forced Alignment Source-to-Mix Projection

P8 解决 P7 source-ms 不能直接与 editor/ASR mix-ms 比较的问题。

新增：

```text
lyric_aligner/alignment/forced_projection.py
scripts/v4_project_forced_alignment.py
scripts/test_v4_forced_mix_projection.py
scripts/test_v4_forced_mix_projection_end_to_end.py
```

并更新 `lyric_aligner/alignment/__init__.py`。

输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

## 4. P8 projection semantics

- `AFFINE` / `PIECEWISE_RATE`：复用 existing `mix_time_for_source()` analytical inverse；
- `CUT_AWARE`：line 两端只有同在一个 retained source segment 才投影；
- confirmed gap boundary 或 cross-cut line -> `unprojectable`，绝不 bridge；
- character spans 独立投影，可保留 cut 两侧合法局部证据；
- 只解析 forced evidence 实际引用的 occurrences，`mapping_scope=forced_evidence_occurrences_only`；
- unrelated blocked occurrence 不阻塞局部 projection；
- relevant mapping missing/blocked/unbound/provenance tamper 仍 fail closed。

## 5. P8 lineage / privacy

P8 artifact upstream 绑定：

```text
source run artifact
P7 forced evidence artifact
exact coarse/fine/cut mapping artifacts actually used
```

正式 projected evidence 不复制 canonical raw lyric；保留 job/occurrence/track/line identity、source boundaries、projection status/reason、可用 mix boundaries、backend/model lineage。

## 6. P8 tests

已覆盖：

- AFFINE；
- PIECEWISE_RATE；
- CUT_AWARE same-segment；
- confirmed gap/cross-cut unprojectable；
- independent span projection；
- relevant mapping failure；
- unrelated blocked occurrence isolation；
- mapping artifact provenance tamper；
- CLI artifact E2E/privacy。

前一版 latest-head `94aa6df29f8505f703c37c5ce59c292f149806e3` 的 validate #577 失败原因仅为 documentation contract：生产/CLI/architecture 变化尚未同步 owning docs；compile 在失败前已通过。当前分支正在补齐这些文档后重新跑 latest-head CI。

## 7. Actions 能 / 不能证明

Actions 能验证 P8 deterministic projection、cut safety、artifact lineage、privacy 与 package/CLI E2E。

Actions 仍不能证明：

- 任一真实 WhisperX/SOFA/MFA production backend 已安装/已跑通；
- 某 checkpoint/G2P 对真实歌声准确；
- forced-alignment family 与 editor/ASR 的权重/阈值已通过真实 blind calibration；
- forced evidence 可以直接改 final timing。

## 8. P8 之后的收口路线

1. P8 latest-head CI 全绿并合入；
2. 将 `forced_alignment_mix_evidence` 作为独立 acoustic family 接入 fusion，保持 shadow/fail-closed；
3. 增加 family disagreement / cut-unprojectable / missing-family diagnostics 与 release-gate 输入；
4. 为本地真实生产补齐可执行的 runtime/preflight 与 production recipe；
5. 真正使用 WhisperX/SOFA/MFA 或其他 adapter 时，锁定 backend/model/language/runtime identity；
6. 使用 private real-song calibration/blind 数据完成误差评估和阈值选择；
7. 在真实数据证明收益前，不把 auxiliary evidence 自动升级为 final timing authority。

> **当前正确表述：P0/P1/P1.1/P2/P3/P4/P5/P6/P7 已进入 main；P8 的 source-to-mix forced projection 已实现，正在补齐 documentation contract 并等待 latest-head CI。真实 production forced-aligner 准确率仍必须由本地真实数据验证。**
