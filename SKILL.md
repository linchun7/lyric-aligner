---
name: lyric-aligner
description: Reconstruct, review, materialize, diagnose and render multilingual canonical lyric subtitles for edited music mixes using Standard text repair, Smart no-audio anchor repair, Pro selective local audio evidence, and Max full Source-to-Mix reconstruction.
---

# Lyric Aligner

当前生产主路径为 **Standard -> Smart -> Pro -> Max**。当前 Smart / Pro 已收口到 v1.1.1；完整 Full V4 主线算法版本及最新合并状态以 `references/v4-status.md` 为准。

这个项目的生产原则不是“让 ASR 重写歌词”，而是：**canonical lyric 决定最终文字与顺序；Jianying timing 是强但可推翻的先验；Smart 先用 timed canonical + editor majority anchors 做 0-audio 验证；Pro/Max 才引入 Source-to-Mix acoustic evidence。** 任何无法由现有证据安全证明的情况继续 review/BLOCK，**不得静默回退 v3.9，不得手工拼/改 artifact 绕过 lineage。**

## 生产模式选择：任何真实任务必须先做

开始执行前，先判断用户任务属于哪一档。**不要默认从 Full V4/Max 开始，也不要因为韩语、日语或外文就自动升级 Max。**

```text
Standard -> Smart -> Pro -> Max
```

### Standard / 标准

选择条件：

- 用户明确要求时间轴冻结；或
- Jianying SRT timing 被明确视为可信，只需要按规范歌词修正错字、漏字、多字和安全的断句差异。

执行：

```powershell
python scripts/v4_text_repair.py ...
```

约束：

```text
audio_read = false
cue count / number / start / end 全部冻结
canonical lyric = final text/order truth
```

**只要用户要求“不要动时间轴”，就优先 Standard，不要擅自调用 Smart/Pro/Max。**

### Smart / 智能（普通生产默认主力）

选择条件：

- 有规范 timed LRC/QRC/Enhanced LRC；
- 有 Jianying SRT；
- 大部分 timing 本来就是对的，只怀疑少数 cue；
- 单曲通常是一个固定变速倍率，或有可用 BPM / DAW stretch ratio；
- 希望优先 0-audio、低成本完成验证和少量安全修复。

执行：

```powershell
python scripts/v4_smart_repair.py ...
```

Smart 仍然不读音频。它使用 canonical timing、A-anchor majority、可用的逐字 timing 和可选 rate prior。`exact_daw` 可作为 hard rate prior；`bpm_derived` 只是 soft plausibility prior。

Smart v1.1.1 的 `ready` 表示 timing 已被实际验证/安全修复，而不是“因为没能力判断所以原样保留”。下列情况必须 `review` / `pro_escalation_required=true`：

```text
timing model not ready
no unique timed-canonical identity
C-grade identity
BPM soft prior conflicts with an otherwise proposed automatic repair
combined repair would create or worsen overlap
```

**韩文、日文、英文或其他外文不是升级 Max 的条件。** 只要 canonical identity 和 timed anchors 够强，仍先 Smart。

### Pro（Smart unresolved 的局部音频层）

选择条件：

- 已经运行当前生产 Smart；并且
- Smart report 明确存在 unresolved/review，`pro_escalation_required=true`；
- 问题只集中在少量 cue/局部 region，而不是整条 timeline 广泛失真。

执行入口：

```powershell
python scripts/v4_pro_selective.py ...
```

Pro v1.1.1 必须绑定**当前 Smart schema + current Smart policy + exact Smart SRT/canonical hashes**。旧 Smart artifact 不能直接复用；如果版本不匹配，先重新跑 Smart。

Pro 按失败原因选择局部 evidence：

```text
timing review + known canonical identity
    -> bounded source<->mix acoustic first

text / identity review
    -> bounded ASR + word timestamps

no word timing + source-side identity needs reinforcement
    -> external forced alignment when configured

unmapped review
    -> bounded mix ASR only
```

相邻 acoustic jobs 可共享一个 bounded mix region，但 ASR-only job 不得无意义扩大 acoustic decode。歌曲交界的 neighbouring-source competitor 是 shadow evidence，不能直接改 timing。

当前 Pro 仍固定：

```text
timing_mutation_performed = false
```

即 Pro 当前负责**局部取证和定位**，不因为声学结果看起来很强就自动写回字幕时间。

### Max（Full V4 / 重型 fallback）

仅在以下情况选择：

- 整体 Jianying timeline 广泛不可信；
- 大量 cue 无法稳定映射 canonical occurrence；
- 存在复杂 cut / reorder / overlap / 多段结构，无法用少数局部 Pro region 解决；
- Smart/Pro 已证明局部链路不足以收敛；
- 任务本身就是从广泛不可信状态重建 Source-to-Mix。

执行：

```powershell
python scripts/v4_run.py ...
```

**Max 是 fallback，不是“更准所以默认用”的模式。** 日常剪映字幕修复不能为了少量坏 cue 重扫 40–60 分钟完整节目。

### 模式选择速查

```text
用户明确说“只修文字/绝不改时间”
    -> Standard

规范 timed lyrics + Jianying timing 大部分正确
    -> Smart（默认）

Smart 有少量 unresolved/review
    -> Pro，只处理这些 bounded regions

整体 mapping/timeline 广泛失真或复杂结构无法局部解决
    -> Max
```

不要反向调用：

```text
普通任务 -> 直接 Max            禁止作为默认策略
Smart ready -> 无理由再跑 Pro   不需要
Pro local issue -> 全曲 ASR      不允许为了“更智能”扩大成本
外文 -> 自动 Max                 错误
rate change -> 自动当 cut        错误
```

## Codex 开始任何真实任务前必须先读

```text
SKILL.md
references/production-requirements.md
references/v4-runtime-guide.md
references/v4-status.md
references/v4-implementation.md
references/smart-pro-v1-1.md
references/dataset-protocol.md
```

如果正在改生产代码，再读：

```text
references/documentation-contract.md
references/v4-change-record.md
```

## Codex 真实任务开场纪律

真实生产任务与代码开发必须分开。开始真实歌曲任务时：

```text
1. checkout/fetch 到最新 main；不要从旧 agent/* / codex/* 分支恢复实现；
2. 先按上面的 Standard / Smart / Pro / Max 决策树选择最低成本且证据足够的模式；
3. 需要 Full V4/Max formal lineage 时，先运行 scripts/v4_runtime_snapshot.py 固化当前 runtime identity；
4. 已有 Full V4 formal artifact 时，把 payload 与对应 *.artifact.json 成对交给 scripts/v4_doctor.py，并要求 --require lineage；
5. 中断恢复先根据 doctor 的 BLOCK / recommended_next_action 续跑，不要无条件从 reconstruction 重跑；
6. 真实生产任务本身不得顺手修改 tracked production code。
```

如果真实数据暴露代码 bug：停止受影响的生产阶段，保存最小复现和可用的 report/lineage 证据；从**最新 main** 新建独立 bugfix 分支修复、补测试、走 CI/PR。修复合入后再以新版本重跑受影响阶段。不要在生产任务分支边跑数据边改算法，也不要为了过任务手改 formal artifact。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、编辑器、forced aligner 都不能替换 final text/order。
2. **模式必须按 cheapest sufficient evidence 选择。** Standard/Smart 不读音频；Pro 只读 Smart unresolved 的局部音频；Source-to-Mix 全局/重型 reconstruction 主要属于 Max。
3. **Jianying timing 是强但可推翻的先验。** 普通 Smart 任务应保留多数可信 cue，只修少量有多重独立证据支持的 outlier。
4. `rate change != cut`；forward source-position discontinuity 才能进入 candidate-level cut review。
5. `confirmed_cut` 仍必须经过 local cut locator → CUT_AWARE mapping → cut-aware canonical timeline。
6. line-LRC 只有整个可推断行区间都位于 source gap 才可整行删除；partial-line 一律 review。
7. confirmed overlap 保持左右两条独立 canonical cue stream，跨轨实际交集必须完整位于 exact confirmed region。
8. cut/overlap 两边先从同一个 `review_resolution` 独立物化，再由 composition stage 合并；不得互相改写 materializer。
9. cut + overlap 只有两层都安全时自动组合：overlap mix interval 不穿 localized cut boundary；overlap delta canonical source interval 不与 confirmed source gap 相交。
10. overlap delta 缺 canonical source provenance、open source interval 不能证明未穿 gap时继续 BLOCK。
11. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source/LRC/canonical selection。
12. Review Decision 必须 task-scoped + exact base-run-scoped；所有 materialization/evidence 必须绑定 exact source run/artifact lineage。
13. P7 forced alignment 只在 **source time** 产生 auxiliary evidence；进入 legacy Full V4 fusion 前必须经 P8 exact Source-to-Mix projection。
14. P7 protocol `1.0` single-job 与 optional protocol `1.1` batch 只改变 external aligner 进程组织；两者必须产生同一类 source-time auxiliary evidence，batch 不能提升 authority。
15. P8 `CUT_AWARE` line 跨 confirmed gap/cut 必须 `unprojectable`，不得 bridge；spans 可独立保留合法局部证据。
16. P9 fusion 只接受 mix-time editor/ASR/forced evidence；任意可用 auxiliary pair 超阈值就是 `CONFLICT`，不得用 2-of-3 多数票隐藏 outlier。
17. P9 的 `LOW/MEDIUM/HIGH/CONFLICT` 都是 **uncalibrated shadow state**；`HIGH` 也不得自动改 authoritative timing 或视为 release confidence。
18. Final renderer 只接受 `ready_for_render + issues=[] + legacy_fallback_used=false`，并验证 exact task/profile/artifact lineage。
19. 所有 stage 都绑定 task fingerprint、algorithm version、upstream IDs、materialized SHA-256；涉及模型的 evidence 还必须绑定 backend/model revision。
20. 所有实质性更新必须同步 owning docs；CI 不通过不得合并。
21. Runtime snapshot / doctor / family evaluator 都是可复现与诊断层，不改变 timing authority；没有独立 blind-test 结果不得把 auxiliary family 提升为自动 timing/release authority。
22. **Standard/Text Repair V2.1 只在时间轴明确冻结时使用。** 它可处理错字、漏字、多字以及 bounded 1↔N / N↔1 / N↔N 断句差异；任何情况下都不得改变 cue 数、编号或 timing。
23. **Smart 是普通“多数 timing 正确、少数 timing 可疑”任务的默认入口。** 不得把原曲 LRC/source absolute time 直接覆盖到 edited mix；必须经 rate/anchor model。
24. **Pro 只能处理当前 Smart unresolved 的 bounded regions。** 不得无理由重扫已被 Smart 验证的正常 cue；当前仍不得自动 timing writeback。
25. **永远不覆盖原始输入。** Standard/Smart/Pro/Max 都写独立 outputs/artifacts；Smart/Pro CLI 的路径碰撞必须 fail closed。

## 权威文档

- 生产工作负载与模式基线：`references/production-requirements.md`
- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构：`references/v4-implementation.md`
- Smart / Pro：`references/smart-pro-v1-1.md`
- 变更：`references/v4-change-record.md`
- forced batch protocol：`references/forced-alignment-batch-protocol.md`
- 文档契约：`references/documentation-contract.md`
- 数据/盲测：`references/dataset-protocol.md`

## 标准生产流程

### 0. 先选模式

除非用户已经明确指定模式，否则按以下顺序判断：

```text
frozen timing?          -> Standard
mostly-correct timing?  -> Smart
Smart unresolved?       -> Pro
broad structural fail?  -> Max
```

不要把“已有 mix audio / source audio”理解成必须使用 Pro/Max；**有音频只是可用证据，不是升级理由。**

### 1. Standard / Text Repair V2.1

如果规范歌词可信，而且任务明确要求保留剪映现有 cue 数量、编号及全部起止时间，只修文字，直接运行：

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --canonical-lrc "private/<任务>/input/lyrics/02.lrc" `
  --out "output/<任务>/TEXT_REPAIRED.srt" `
  --report "output/<任务>/TEXT_REPAIR.json"
```

批量高频生产使用：

```powershell
python scripts/v4_text_repair_batch.py `
  --manifest "private/<任务>/text-repair.batch.json" `
  --summary "output/<任务>/text-repair.batch.summary.json"
```

V2.1 先用唯一 exact 文本锚点把长字幕切成局部区间，再在区间内运行 bounded monotonic span DP。常见 1↔1 / 1↔2 / 2↔1 / 2↔2 可在高置信下自动处理；真实 canonical gap、额外 subtitle cue、近似重复歌词歧义、gap 邻域弱匹配、会把现有 cue 清空的重分配或结构差异过大继续 `review_required`。

这个入口完全不读取音频，也不依据 LRC timestamp 修改 SRT timing，因此 **BPM 加速/减速不会改变 Standard 的文字修复规则**。

### 2. Smart / Anchor Timeline Repair v1.1.1

日常“剪映 timing 大部分正确”的任务优先：

```powershell
python scripts/v4_smart_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --output-srt "output/<任务>/<任务>_SMART.srt" `
  --report "output/<任务>/<任务>_SMART.json"
```

有 Cubase/DAW 精确 stretch ratio 时：

```powershell
--rate-prior "01.lrc=1.09375"
```

只有 BPM 时：

```powershell
--target-bpm 140 `
--source-bpm "01.lrc=128"
```

`target_bpm/source_bpm` 只作为 soft prior；不要把它伪装成 exact DAW rate。

Smart 输出后：

```text
status == ready
    -> 普通任务结束；不要无理由再跑 Pro

pro_escalation_required == true
    -> 进入 Pro，只处理 unresolved/review cue
```

### 3. Pro / Selective Audio Repair v1.1.1

先生成计划，默认仍不读 audio：

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json"
```

需要 local source↔mix acoustic evidence 时：

```powershell
--mix-audio "private/<任务>/input/mix.wav" `
--source-audio "01.lrc=private/<任务>/source/01.wav" `
--source-audio "02.lrc=private/<任务>/source/02.wav" `
--acoustic-out "output/<任务>/<任务>_PRO_ACOUSTIC.json"
```

需要 ASR 时仅对 planner 选中的 bounded jobs 执行：

```powershell
--mix-audio "private/<任务>/input/mix.wav" `
--asr-model-id "<faster-whisper-model>" `
--asr-out "output/<任务>/<任务>_PRO_ASR.json"
```

需要 external forced alignment 时按 `references/v4-runtime-guide.md` 提供 explicit command/backend/model identity。不要因为 backend 已配置就无条件跑 forced；由 Pro reason-aware routing 决定哪些 jobs 请求它。

Pro 当前输出 evidence，不自动写 SRT timing。真实生产中如果 Pro 仍无法局部收敛，再判断是否需要人工 review 或 Max，而不是擅自扩大 Pro 扫描范围。

### 4. Max / Full V4 Runtime / Resume preflight

只有进入 Max/Full V4 formal chain 时，先做：

```powershell
python scripts/v4_runtime_snapshot.py ...
python scripts/v4_doctor.py ... --require lineage
```

新任务没有任何 formal artifact 时，doctor 的 lineage requirement 可等第一个 run artifact 生成后再启用；中断恢复或已有产物时必须优先验证现有 payload/artifact lineage。具体配对参数见 `references/v4-runtime-guide.md`。

### 5. Max Reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

输出 `ready_for_render` 或 candidate-level `review_required`。

生产 coarse/fine CLI 会在内部只解码当前 occurrence / coarse retrieval windows 所需的 mix 区间并保留少量保护 padding；仍使用完整 mix SHA、absolute mix coordinates 与原有 Source-to-Mix 决策规则。该 bounded decode 不是低精度模式：cut、ambiguity、review/fail-closed 与 threshold 语义不变。

`v4_run.py` 下的 coarse 调用还会把同一原曲重复需要的 harmonic source features 缓存在当前 V4 输出树的 `cache/features`。cache key 绑定 source audio SHA-256、采样参数、feature implementation 与 librosa 版本；损坏、缺失或 identity 不匹配一律按 cache miss 从原曲重算。这个 cache 只是可删除的本地性能层，不进入 formal artifact lineage，也不能绕过 source SHA、task fingerprint、threshold、cut/review 或 release 验证。

### 6. Max Review

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Review Decision schema=`1.2`。主要 actions：

```text
transition candidate: resolved_clear | confirmed_overlap
timewarp discontinuity: confirmed_cut | rejected_requires_remap
generic blocked timewarp: confirmed_requires_rebuild
```

### 7. Max Materialization

Confirmed overlap：

```powershell
python scripts/v4_recompose_overlap.py ...
```

Confirmed cut：

```powershell
python scripts/v4_rebuild_cut.py ...
```

同一任务同时有 confirmed cut + confirmed overlap 时，两条 materializer 必须从同一个 reviewed run 启动，再执行：

```powershell
python scripts/v4_compose_materializations.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --cut-run "output/<任务>/v4/cut_rebuilt_run.json" `
  --cut-artifact "output/<任务>/v4/cut_rebuilt_run.artifact.json" `
  --overlap-run "output/<任务>/v4/recomposed_run.json" `
  --overlap-artifact "output/<任务>/v4/recomposed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/combined" `
  --out "output/<任务>/v4/combined_run.json" `
  --artifact-out "output/<任务>/v4/combined_run.artifact.json" `
  --git-commit "<commit>"
```

有效 authoritative run 可能是：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

后续 evidence 必须绑定你最终选择的 **同一个 effective run artifact**。

### 8. Max Editor / ASR evidence

```powershell
python scripts/v4_editor_evidence.py ...
python scripts/v4_alignment_backends.py
python scripts/v4_plan_alignment.py ...
python scripts/v4_execute_asr_evidence.py ...
python scripts/v4_plan_asr_second_pass.py ...
python scripts/v4_execute_asr_second_pass.py ...
```

ASR 只在 planner/routing 指定的 bounded region 使用；不要为了“看起来更智能”全曲无条件跑昂贵模型。

### 9. Max External forced alignment（需要时）

先检查 external command readiness：

```powershell
python scripts/v4_alignment_backends.py `
  --external-forced-aligner-command '"<executable>" <adapter-args>'
```

默认协议 1.0 保持 one-process-per-job：

```powershell
python scripts/v4_execute_forced_alignment.py ...
```

如果真实 backend 每次进程启动都会重新加载大模型，并且 adapter 实现 `references/forced-alignment-batch-protocol.md` 的 protocol 1.1，可以显式启用：

```powershell
python scripts/v4_execute_forced_alignment.py ... `
  --execution-mode batch `
  --timeout-seconds <整个 batch 可接受的上限秒数>
```

`batch` 会把所有 selected jobs 放入一个临时 request，并只启动一次 external process。Response job IDs 必须与 request 精确一致，每个 job 仍走 P7 原有 source-window/boundary/span fail-closed validator。显式 `--job-id` 为空列表的程序化调用仍表示 zero-work，不会解析/启动 command。`--timeout-seconds` 在 batch 模式覆盖整个 subprocess，因此大任务需要显式设置足够值；不会自动取消 timeout。

P7 formal output 无论 single/batch 都是 source-time：

```text
source_forced_alignment_evidence / forced_alignment_evidence
```

真实 backend 必须记录：backend/package version、model/checkpoint revision、language/G2P resources、runtime/device identity。不要把“executable 找得到”写成“模型准确”。Batch protocol 本身不等于某个 WhisperX/SOFA/MFA adapter 已获生产批准；adapter 仍需对当前 upstream runtime 单独 review，并用 private calibration/blind 验证。

### 10. Max Forced evidence Source→Mix projection

```powershell
python scripts/v4_project_forced_alignment.py ...
```

只有输出：

```text
forced_alignment_mix_projection / forced_alignment_mix_evidence
```

才允许进入 P9 fusion。

### 11. Max Multi-family shadow fusion

```powershell
python scripts/v4_fuse_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --editor-evidence "<editor.json>" `
  --editor-evidence-artifact "<editor.artifact.json>" `
  --asr-evidence "<asr.json>" `
  --asr-evidence-artifact "<asr.artifact.json>" `
  --forced-mix-evidence "<forced_mix.json>" `
  --forced-mix-evidence-artifact "<forced_mix.artifact.json>" `
  --out "output/<任务>/v4/evidence/fusion.json" `
  --artifact-out "output/<任务>/v4/evidence/fusion.artifact.json"
```

优先检查：

```text
shadow_level == CONFLICT
max_auxiliary_boundary_disagreement_ms
forced_alignment_line_counts.unprojectable
missing family / unavailable reasons
```

**不要把 `HIGH` 直接写回 timeline。** fusion 是定位风险和收集 calibration 数据的工具。

### 12. Max Final Render / Release

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Render/release 仍以 authoritative canonical timeline 为准，不读取未校准 shadow fusion 来偷偷改 timing。具体 algorithm version 使用当前 `references/v4-status.md` / runtime snapshot 记录值，不在 SKILL.md 中硬编码旧版本号。

### 13. Real-data family evaluation

真实人工 truth 到位后：

```powershell
python scripts/v4_evaluate_evidence_families.py ...
```

它比较 Source-to-Mix/editor/ASR/forced 的真实边界误差与 coverage，但仍然只是 calibration evidence。Dataset/runtime/fusion-policy identity 与 blind-test 纪律见 `references/v4-runtime-guide.md` 和 `references/dataset-protocol.md`。

## 第一次真实数据生产纪律

Smart/Pro 是日常主力时，优先拿真实生产文件统计：

```text
Smart false timing repair
Smart false-ready
Smart -> Pro escalation rate
Pro acoustic false match / ambiguity
Pro 实际读取 audio 时长占整条节目比例
中/英/韩/日及 code-switch 分桶表现
歌曲开始/结束/transition/repeated chorus 风险桶
```

Max/legacy family calibration 仍可按需要准备 30–90 秒片段覆盖：

```text
normal global-rate
dynamic local stretch
cut附近
overlap
弱人声/强伴奏
editor识别差语言
```

先跑 calibration，再冻结 threshold/model/profile/runtime identity，然后用独立 blind set 验证。没有 blind 结果，不得宣称某个真实 backend 或某套 fusion threshold 已经达到生产准确率目标。

## 当前仍 BLOCK 的边界

- Smart 无法建立可验证 timing model；
- Smart canonical occurrence identity 不唯一；
- Smart 自动 repair 会制造或扩大 overlap；
- Pro report / Smart SRT / canonical hashes 或 Smart policy/schema 不匹配；
- Pro local evidence 不足或明显冲突；
- overlap interval 与 localized cut boundary 相交；
- overlap delta canonical source interval 与 confirmed source gap 相交；
- overlap delta 缺 source provenance；
- line-LRC partial-line cut；
- timed token 本身被 cut 穿过；
- 任一 cut/overlap mapping 或 lineage 不确定；
- relevant forced mapping/provenance 不完整；
- forced line 跨 confirmed cut/gap；
- auxiliary families 明显冲突；
- runtime/payload/artifact identity 不一致；
- real private calibration / blind-test 尚未完成时尝试提升 auxiliary timing authority；
- 任一模式试图覆盖原始 SRT/LRC/audio input。

## 回归纪律

```powershell
python -m compileall -q lyric_aligner scripts
python scripts/validate_docs_contract.py
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/privacy_scan.py
python scripts/check_environment.py
git diff --check
```

如果 CI 与本地结果冲突，以 **latest-head、相同 Python/dependency、完整日志** 为准调查；不得为了合并而删除失败测试。

## 后续优先级

1. 日常真实任务优先按 **Smart -> Pro** 生产链收集 private calibration / blind 数据；
2. 先修真实样本暴露的 bug、误判和性能回归，不无样本继续堆算法；
3. Pro 自动 timing writeback、B-grade 更大权限、piecewise 扩展等都必须等独立 blind 数据证明安全后再考虑；
4. Max 继续保留为 broad-untrusted / complex-structure fallback，不把其成本结构重新带回普通生产主路径。
