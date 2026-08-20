---
name: lyric-aligner
description: Reconstruct, review, materialize, diagnose and render multilingual canonical lyric subtitles for edited music mixes using Standard text repair, Smart no-audio anchor repair, Pro selective local audio evidence, and Max full Source-to-Mix reconstruction.
---

# Lyric Aligner

当前生产主路径为 **Standard -> Smart -> Pro -> Max**。

当前正式生产版本：

```text
Standard -> Text Repair V2.1
Smart    -> Sequence Reconciliation + Anchor Timeline Repair v1.2.2
Pro      -> Selective Audio Repair v1.1.1
Max      -> Full V4 Alignment（具体算法版本以 references/v4-status.md / runtime snapshot 为准）
```

当前 Smart policy：

```text
smart-validation-policy-2026-08-20-v1.2.2
```

这个项目的生产原则不是“让 ASR 重写歌词”，而是：**canonical lyric 决定最终文字与顺序；canonical LRC line break 不等于最终 subtitle cue boundary；Jianying timing / cue segmentation 是强但可推翻的先验；Smart 先用 timed canonical + editor majority anchors 做 0-audio 验证；Pro/Max 才引入 Source-to-Mix acoustic evidence。**

更高模式可以增加证据和修复能力，但没有更强独立反证时，不得破坏较低模式已经安全成立的 text / cue ownership / timing。任何无法由现有证据安全证明的情况继续 review/BLOCK，**不得静默回退 v3.9，不得手工拼/改 artifact 绕过 lineage。**

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
trusted editor cue ownership = display segmentation prior
LRC line break != subtitle cue boundary authority
```

如果 editor 多个 cue 拼接后的连续文字已经与 canonical 连续文字一致，只是 LRC 的行换行不同，**必须保留 editor 原 cue ownership，不得仅为模仿 LRC 分行跨 cue 搬字。**

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

Smart v1.2.2 仍然不读音频。它从 Standard/Text Repair V2.1 的安全文字结果开始，再按以下顺序增加证据：

```text
Text Repair V2.1 safe baseline
    -> independently-ready four-A timing recovery（若已有）
    -> baseline strong text identities 建立 text-only Sequence Projection
    -> bounded canonical sequence reconciliation / cautious frontier walk
    -> BPM-derived rate 经 baseline-safe anchors 验证后的 text-only recovery
    -> editor cue ownership guard
    -> final Smart timing plan
```

#### Smart timing authority

Smart 使用 canonical timing、A-anchor majority、可用的逐字 timing 和可选 rate prior：

```text
exact_daw        -> hard rate prior
bpm_derived      -> soft plausibility prior
anchor_estimated -> 无 hard prior 时由 A anchors robust estimate
```

`bpm_derived` 不能冒充 exact DAW rate。BPM soft prior 与稳定 A-anchor evidence 冲突时，不靠 BPM 强行改 timeline。v1.2.2 即使使用 BPM 帮助文字 recovery，也不会把 BPM 升级成 timing hard prior。

Smart v1.2.2 的 `ready` 表示 timing 已被实际验证/安全修复，而不是“因为没能力判断所以原样保留”。下列情况必须 `review` / `pro_escalation_required=true`：

```text
timing model not ready
no unique timed-canonical identity
C-grade identity
BPM soft prior conflicts with an otherwise proposed automatic repair
combined repair would create or worsen overlap
```

#### Severe-ASR recovery / Sequence Projection

Smart 不通过降低 Text Repair 阈值来解决严重 ASR。v1.2.x 增加独立的 **Sequence Projection**，只用于恢复 canonical text identity：

- interior review 优先消费双侧 canonical strong anchors；已有 independently-ready four-A timing model 时，继续使用更强的 timing recovery；
- 主 timing model 不足以启动时，可由 baseline strong text identities 建立只读 text-only projection；
- bounded reconciliation 只在 weak/review severe-ASR region 中按连续 canonical sequence 恢复文字；
- outer frontier 只能谨慎向歌曲边缘推进，遇到 timing discontinuity / cut / editor-only ad-lib / bad boundary 必须停止，不能越过断点追更远 LRC；
- editor-only ad-lib 只有完全没有 canonical claim 时才可作为透明间隔，ad-lib 本身不删除、不改写；
- weak mapped cue 不能被跨过借远处 anchor；
- sequence/timing recovered text 永远不能因为“文字被修对了”就升级成 A/B primary timing anchor。

Sequence Projection **不是** `SongTimingModel ready` 的替代品。sequence-projected decision 固定保持在 B-grade 以下，只提供 final-text evidence，不得反向制造 timing authority。

#### v1.2.2 BPM-validated text recovery

当生产提供“原 BPM -> 目标 BPM”且一首歌主要是固定匀速变化时，BPM 可以帮助 repeated lyric / severe-ASR 的文字定位，但只有经过独立文本证据验证后才能启用：

- `bpm_derived` 仍为 soft prior，不进入 timing hard-rate map；
- 至少 3 个 baseline-safe 1:1 text anchors 验证固定 BPM rate；
- 要求有效 source/mix 跨度、稳定 offset/residual、足够 inlier fraction，并且 safe-anchor pairwise rate 与 BPM rate 一致；
- 只处理已经有单一 canonical occurrence claim 的 `review` cue，不靠 BPM 猜 unmapped cue，不进行整块 LRC 重分；
- interior candidate 必须被同源安全 anchors 夹住；歌曲前缘只允许极窄 leading-edge recovery；
- 相邻 cue 共同 claim 同一 occurrence、split-continuation、下一 lexical canonical 已进入当前 cue、pure vocalization 等情况 fail closed；
- pure vocalization 不得被 BPM 自动填成 lexical lyric；
- 只有去掉受限的 `哦/啊/耶/oh/yeah/...` 边缘 vocalization 后，剩余 editor text **精确等于 canonical** 时，才允许自动裁掉这些边缘 vocalization；普通英文词不能当 vocalization 删除；
- BPM-recovered text 保持 B-grade 以下，不能成为 A/B primary timing anchor。

report 记录：

```text
text_bpm_projection_recovery_count
text_bpm_projection_vocalization_trim_count
text_bpm_projection_models
```

#### v1.2.1 editor cue ownership guard

v1.2.1 起在 final text materialization 前增加 editor cue ownership guard，用来修复 sequence/BPM/text-repair 已经产生的局部边界误搬，而不是扩大 canonical recovery 权限。

规则：

- canonical 继续拥有最终文字与顺序权威；
- line-LRC 行边界不能单独把 editor 已经清楚识别的短语搬到相邻 cue；
- 普通 boundary restore 必须保持相邻 cue **合并后的 normalized lyric stream 完全不变**，只允许重新分配 ownership；
- 仅在能证明相邻两边出现同一短 boundary fragment 的窄场景，才允许删除一份短重复；不能借此做广泛 deletion；
- ownership guard 不改变 cue count / number / start / end；
- ownership-restored decision 保持低于 timing-anchor authority，不能成为 A/B primary timing anchor；
- report 使用 `text_editor_ownership_repartition_count` 记录实际触发次数。

因此像“前一 cue 末尾词被错误搬到后一 cue”“后一 cue 开头词被错误搬到前一 cue”这类问题，应优先恢复可信 editor ownership，而不是按 LRC 换行机械重分。

#### Smart 输出语义

Smart report schema 当前仍为：

```text
schema_version = smart-1.1
policy_id      = smart-validation-policy-2026-08-20-v1.2.2
```

生产判断：

```text
status == ready
AND pro_escalation_required == false
    -> 普通 Smart 任务可结束

status == review_required
OR pro_escalation_required == true
    -> 该 Smart SRT 是中间/诊断产物，不得宣称“全部校正完成”；进入 Pro 或人工 review
```

**不要把 unresolved review cue 中仍保留的 editor ASR 当成已校正 canonical final。** review 的含义就是“现有无音频证据不足以安全自动改”。

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

Pro v1.1.1 必须绑定**当前 Smart schema + current Smart policy + exact Smart SRT/canonical hashes**。Smart policy 已升到 v1.2.2，因此 v1.2.1 及更早 Smart artifact 不能直接复用；版本/policy/hash 不匹配时先重新跑当前 Smart。

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

即 Pro 当前负责**局部取证和定位**，不因为声学结果看起来很强就自动写回字幕时间。Pro 只处理 Smart 明确 unresolved 的 cue；因此不能假设 Smart 的 false-ready 会自动被 Pro 兜底。

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

**Max 是 fallback，不是“更准所以默认用”的模式。** 日常剪映字幕修复不能为了少量坏 cue 重扫 40–60 分钟完整节目。Max 同样必须遵守 segmentation authority：line-LRC grouping 不能单独推翻可信 editor cue boundary；需要更强 word/token/audio evidence。

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
LRC 换行 -> 强制重分 editor cue  错误
higher mode -> 无证据覆盖 lower-mode safe result  错误
review_required Smart -> 当 final 发布  错误
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
2. 先按 Standard / Smart / Pro / Max 决策树选择最低成本且证据足够的模式；
3. Smart/Pro 运行前确认当前 schema/policy/hash，不复用 stale artifact；
4. 需要 Full V4/Max formal lineage 时，先运行 scripts/v4_runtime_snapshot.py 固化当前 runtime identity；
5. 已有 Full V4 formal artifact 时，把 payload 与对应 *.artifact.json 成对交给 scripts/v4_doctor.py，并要求 --require lineage；
6. 中断恢复先根据 doctor 的 BLOCK / recommended_next_action 续跑，不要无条件从 reconstruction 重跑；
7. 真实生产任务本身不得顺手修改 tracked production code。
```

如果真实数据暴露代码 bug：停止受影响的生产阶段，保存最小复现和可用的 report/lineage 证据；从**最新 main** 新建独立 bugfix 分支修复、补测试、走 CI/PR。修复合入后再以新版本重跑受影响阶段。不要在生产任务分支边跑数据边改算法，也不要为了过任务手改 formal artifact。

真实歌曲、cue 编号、时间戳、歌词、真实 BPM 不得硬编码进 production algorithm/public regression。真实 failure pattern 应转成通用 synthetic regression。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源，但不是无条件的 line-break / subtitle segmentation 真源。** ASR、编辑器、forced aligner 都不能替换 final text/order；LRC 行换行也不能单独把文字跨可信 editor cue 搬移。
2. **模式必须按 cheapest sufficient evidence 选择。** Standard/Smart 不读音频；Pro 只读 Smart unresolved 的局部音频；Source-to-Mix 全局/重型 reconstruction 主要属于 Max。
3. **Jianying timing / credible cue segmentation 是强但可推翻的先验。** 普通 Smart 任务应保留多数可信 cue，只修少量有多重独立证据支持的 outlier。
4. **Metadata/credits 不是 canonical lyric。** 明显制作信息、商务合作、版权提示等不得进入 lyric truth；无法安全区分时 fail closed，不用 metadata 驱动 sequence/timing。
5. `rate change != cut`；forward source-position discontinuity 才能进入 candidate-level cut review。
6. `confirmed_cut` 必须经过 local cut locator -> CUT_AWARE mapping -> cut-aware canonical timeline。
7. line-LRC 只有整个可推断行区间都位于 source gap 才可整行删除；partial-line 一律 review。
8. confirmed overlap 保持左右两条独立 canonical cue stream，跨轨实际交集必须完整位于 exact confirmed region。
9. cut/overlap 两边先从同一个 `review_resolution` 独立物化，再由 composition stage 合并；不得互相改写 materializer。
10. cut + overlap 只有两层都安全时自动组合；无法证明时继续 BLOCK。
11. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source/LRC/canonical selection。
12. Review Decision 必须 task-scoped + exact base-run-scoped；所有 materialization/evidence 必须绑定 exact source run/artifact lineage。
13. P7 forced alignment 只在 **source time** 产生 auxiliary evidence；进入 legacy Full V4 fusion 前必须经 P8 exact Source-to-Mix projection。
14. P7 protocol `1.0` single-job 与 optional protocol `1.1` batch 只改变 external aligner 进程组织；batch 不能提升 authority。
15. P8 `CUT_AWARE` line 跨 confirmed gap/cut 必须 `unprojectable`，不得 bridge。
16. P9 fusion 只接受 mix-time editor/ASR/forced evidence；任意可用 auxiliary pair 超阈值就是 `CONFLICT`，不得用 2-of-3 多数票隐藏 outlier。
17. P9 的 `LOW/MEDIUM/HIGH/CONFLICT` 都是 **uncalibrated shadow state**；`HIGH` 也不得自动改 authoritative timing 或视为 release confidence。
18. Final renderer 只接受 `ready_for_render + issues=[] + legacy_fallback_used=false`，并验证 exact task/profile/artifact lineage。
19. 所有 stage 都绑定 task fingerprint、algorithm version、upstream IDs、materialized SHA-256；涉及模型的 evidence 还必须绑定 backend/model revision。
20. 所有实质性更新必须同步 owning docs；CI 不通过不得合并。
21. Runtime snapshot / doctor / family evaluator 都是可复现与诊断层，不改变 timing authority；没有独立 blind-test 结果不得把 auxiliary family 提升为自动 timing/release authority。
22. **Standard/Text Repair V2.1 只在时间轴明确冻结时使用。** 它可处理错字、漏字、多字以及 bounded 1↔N / N↔1 / N↔N 断句差异；任何情况下都不得改变 cue 数、编号或 timing。
23. **Smart v1.2.2 是普通“多数 timing 正确、少数 timing 可疑”任务的默认入口。** 不得把原曲 LRC/source absolute time 直接覆盖到 edited mix；必须经 rate/anchor model。
24. **Pro 只能处理当前 Smart unresolved 的 bounded regions。** 不得无理由重扫已被 Smart 验证的正常 cue；当前仍不得自动 timing writeback。
25. **永远不覆盖原始输入。** Standard/Smart/Pro/Max 都写独立 outputs/artifacts；Smart/Pro CLI 的路径碰撞必须 fail closed。
26. **Higher mode 必须保持能力单调性。** 没有更强独立证据时，不得退化 lower-mode 已安全成立的 text、cue ownership/display segmentation 或 timing。
27. **Recovered text 不得形成循环证据。** 无论 ready-model recovery、Sequence Projection 还是 BPM-validated recovery，都不能反向把自己的恢复结果提升为 primary timing anchor。
28. **Editor ownership restoration 不是新的 canonical/timing authority。** 它只允许在已有 canonical text stream 内修正相邻 cue 的边界归属；普通移动保持 pair-combined text invariant，窄 duplicate drop 也不得扩张为一般删除规则。
29. **`review_required` 不是 final-ready。** unresolved editor ASR 可以保留用于诊断，但不能被描述成“规范歌词已全部校正”。
30. **BPM-derived text evidence 仍必须 fail closed。** 没有足够 baseline-safe anchors、occurrence 不唯一、split/邻 cue 冲突或 pure vocalization 时，不得为了降低 review 数量强行自动修复。

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

如果 span 连续文字已经一致，仅 LRC/editor line grouping 不同，V2.1 保留 editor cue ownership，不执行仅由 LRC 换行驱动的跨 cue 文字迁移。

这个入口完全不读取音频，也不依据 LRC timestamp 修改 SRT timing，因此 **BPM 加速/减速不会改变 Standard 的文字修复规则**。

### 2. Smart / Sequence Reconciliation + Anchor Timeline Repair v1.2.2

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

`target_bpm/source_bpm` 只作为 soft prior；不要把它伪装成 exact DAW rate。v1.2.2 可在该 soft prior 被多个 baseline-safe text anchors 独立验证后，将其用于**文字 recovery**，但仍不增加 timing mutation authority。

Smart 输出后：

```text
status == ready AND pro_escalation_required == false
    -> 普通任务结束；不要无理由再跑 Pro

status == review_required OR pro_escalation_required == true
    -> 进入 Pro / 人工 review；当前 Smart SRT 不是 final-ready
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

需要 local source<->mix acoustic evidence 时：

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

### 5. Max Reconstruction / Review / Materialization / Evidence / Render

主入口：

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

后续按 `references/v4-runtime-guide.md` 与当前 `references/v4-status.md` 执行：

```text
review
-> overlap/cut materialization（需要时）
-> editor / ASR / forced evidence（按 planner，bounded only）
-> forced source->mix projection
-> shadow fusion
-> final render / release validation
```

关键 authority 保持：

- authoritative run 的 payload/artifact lineage 必须精确绑定；
- forced alignment 原始结果在 source time，进入 fusion 前必须经 Source->Mix projection；
- P9 fusion 是 shadow evidence，不得把 `HIGH` 直接写回 timeline；
- Render/release 只接受当前 formal renderer/release gate 所要求的 authoritative state；
- 具体 algorithm version 不在 `SKILL.md` 硬编码旧值，以 `references/v4-status.md` / runtime snapshot 为准。
