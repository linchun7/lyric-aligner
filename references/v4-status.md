# Lyric Aligner v4 当前实施状态

更新日期：2026-08-20  
主线算法版本：`4.0.0a9`

> P3 前完整历史状态见 `references/archive/2026-08-19-pre-p3-v4-status.md`。真实生产设计基线见 `references/production-requirements.md`。Smart / Pro 细节见 `references/smart-pro-v1-1.md`。

## 1. 当前四档产品路径

```text
Standard -> Text Repair V2.1
Smart    -> Canonical Sequence Reconciliation + Anchor Timeline Repair v1.2.2（no-audio）
Pro      -> Selective Audio Repair（局部 audio evidence）
Max      -> Full V4 Alignment
```

共同事实：Canonical lyric 是最终**文字/顺序** truth；它的行换行不是最终 subtitle cue segmentation authority。Jianying timing / cue boundary 是强但可推翻的先验。只有更强的 token/word/audio evidence 能证明边界错误时，高阶模式才可推翻已经可信的 editor segmentation。

模式必须满足能力单调性：**更高模式可以增加证据和修复能力，但不得在没有更强反证时破坏更低模式已经安全成立的文字、cue ownership 或 timing。**

韩文、日文或整段外文不因语言本身自动进入 Max。只要 canonical identity 与 timing anchors 足够，Smart 仍可成立；局部难点升级 Pro；只有整体 mapping/timeline 广泛不可信、anchor coverage 太弱或复杂结构无法局部解决时进入 Max。

## 2. Standard — Text Repair V2.1

已在 main 成立。适用于“规范歌词可信、剪映时间轴冻结、只修文字”：

- 不读取 audio；
- 不改变 cue count / number / start / end；
- deterministic 文字纠错与 bounded 1↔N / N↔1 / N↔N span；
- canonical 连续文字已经与 editor cue 拼接内容一致时，保留 editor 的原始 cue ownership，不因 LRC 行换行不同而跨 cue 搬字；
- ambiguous/mixed/unsafe layout 情况继续 fail closed；
- production `auto-threshold >= 0.72`；
- report schema `2.1`。

## 3. Smart — Sequence Reconciliation + Anchor Timeline Repair v1.2.2

Smart 是日常主力 no-audio 模式：**大部分信剪映 timing，但 canonical lyric 始终是最终文字/顺序 truth。**

实现：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
lyric_aligner/timeline/sequence_reconcile.py
lyric_aligner/timeline/bpm_sequence_reconcile.py
lyric_aligner/timeline/ownership_guard.py
scripts/v4_smart_repair.py
```

### 3.1 为什么 v1.2.0 重构文字 identity 层

真实生产回归证明 v1.1.x 存在 text-first bootstrap deadlock：严重 ASR 乱码越离谱，Text Repair 越难形成正确的 1↔N / N↔N canonical span；A anchor 因此可能不足 4 个，主 timing model 无法 ready，随后 timing-based text recovery 又无法启动，最终把已知错误的 editor ASR 原样 materialize 到 SRT。

v1.2.0 不降低 Text Repair 的 similarity/span threshold，也不降低主 timing model 的四-A gate。新增独立的 **Sequence Projection**，仅用于 canonical text identity/reconciliation：

```text
Text Repair V2 safe baseline
    -> ready four-A timing recovery（若已有）
    -> baseline strong text identities 构建 text-only Sequence Projection
    -> bounded canonical sequence reconciliation / cautious frontier walk
    -> materialize canonical text into existing editor cues
    -> final Smart timing plan（sequence-recovered text 不参与 A/B 建模）
```

### 3.2 Canonical timing / primary timing anchors

Smart 保留 line-level LRC timestamp、Enhanced LRC/QRC word/token timestamp。主 timing 的 A/B/C identity 分级不变：只有 A-grade exact/unique/1:1 editor identity 可建立主 timing model；B/C 不得建立模型。

每首歌默认：

```text
source_time = offset + rate * mix_time
```

rate authority：

```text
exact_daw        -> hard prior，可固定 rate
bpm_derived      -> soft plausibility，不硬锁 rate
anchor_estimated -> 无 hard prior 时由 A anchors robust estimate
```

BPM-derived 与稳定 A-anchor rate 冲突时，不用软先验重建模型；但该冲突会阻止自动 timing mutation。少量同歌多 rate / cut 仍升级而不是强迫普通任务进入重链路。

### 3.3 Sequence Projection：只恢复文字，不获得 timing authority

无 exact hard rate prior 时，一个 text-only projection 至少要求：

- 3 个 unique/exact/1:1/unchanged A text anchors；
- 再加至少 1 个 `score >= 0.92` 的 A/B strong text anchor；
- strong anchors 在 source 与 mix 两侧都有至少 8 秒有效跨度；
- robust pairwise affine rate 在 `[0.5, 2.0]`；
- strong-anchor median absolute residual `<= 450ms`；
- `750ms` inlier fraction `>= 0.75`。

若有 exact hard rate prior，可在至少 2 个 A strong anchors 下建立 text-only projection；BPM-derived prior 仍不能进入这里的 hard rate map。

这个 projection **不是** `SongTimingModel ready` 的替代品。所有 sequence-projected decision 的 score 强制 cap 到 `< 0.92`，因此即使恢复后的字与 canonical 完全一致，也只能作为 C-grade final-text evidence，不能成为 A/B timing anchor，也不能把主 timing model 从 3 A 人为变成 4 A。

### 3.4 Strongly bounded severe-ASR sequence reconciliation

当两个 model-consistent strong anchors 在 cue 顺序与 canonical 顺序上共同夹住一段 weak/review 区域时，Smart 可以忽略中间乱码自身的 lexical similarity，按完整 canonical gap 重建文字 identity。

安全合同：

- 两侧 strong anchors 必须同 source，且各自与 Sequence Projection 在 `750ms` 内一致；
- block 内至少存在一个 Text Repair review；纯 Standard-safe 区域不进入此层；
- canonical gap 必须连续、同源、完整消费；
- canonical rows 数必须介于 `cue_count` 与 `4 * cue_count`；
- 单个 bounded block 最多 16 个 editor cues；
- editor cue starts 与 projected canonical onsets 必须保持单调；
- 每个 cue 的首 canonical onset 与 cue start 最大 `1300ms`；
- partition 同时最小化“当前 cue 首 onset 误差 + 下一 canonical onset 对下一 editor cue start 的边界误差 + 文本长度 ownership 变化”；
- LRC 行数不是 cue 数。允许一个 editor cue 吸收多条 canonical lines，但最终 cue count/start/end 不变。

成功 reason：

```text
sequence_projection_confirms_bounded_canonical
```

这条路径专门解决“一个 editor cue 实际覆盖两三条 LRC line、但 editor ASR 已错成另一句话”时 Text Repair span gating 无法 bootstrap 的问题。

### 3.5 Outer frontier walk / cut-ad-lib stop rule

在某首歌最前或最后一个 model-consistent strong anchor 外侧，Sequence Projection 可逐 cue 向外走，恢复仍与 projected onset 高度吻合的 canonical text；但**不允许越过 timing break 继续追 LRC**。

- 当前 cue 第一 projected canonical onset 必须在 `900ms` 内；
- multi-line frontier assignment 还要求 editor/canonical text similarity `>= 0.42`，避免仅凭时间把多条歌词塞进一个弱 cue；
- 一旦下一 canonical onset 与下一 editor cue start 的 boundary delta `>1600ms`，当前已证明 cue 可完成后立即停止；
- 遇到另一个 strong anchor、非单调 editor time、或没有合格候选时立即停止；
- 不跳过 break 去寻找更远 canonical match。

成功 reason：

```text
sequence_projection_confirms_frontier_canonical
```

这保留真实 cut / editor-only ad-lib 的 review 权利，同时允许在强 anchor 边缘恢复显然属于 canonical 的严重错词。

### 3.6 Existing ready-model recovery 保留

v1.1.2/v1.1.3 的 independently-ready timing recovery 仍先执行：

```text
timing_model_confirms_canonical_sequence
timing_model_confirms_song_edge_canonical
```

已有四-A ready model 时继续使用更强的 timing evidence；Sequence Projection 只补它无法启动或无法覆盖的 severe-ASR 区域，不替代其更高证据等级。

### 3.7 Segmentation authority / lower-mode monotonicity

```text
canonical lyric text/order -> authority
LRC line break             -> grouping/onset evidence only
trusted Jianying cue       -> display segmentation strong prior
word/token/audio evidence  -> may rebut editor boundary when independently strong
```

因此，连续歌词内容完全一致、只是 LRC 写成两行而剪映显示成三条 cue 时，Smart 必须继承 Standard 的 cue ownership；不能仅因为 canonical 行换行不同，把一段文字从正确的 editor cue 搬到前一个或后一个 cue。

Sequence Projection 只在 weak/review severe-ASR region 或明确的 outer frontier 上运行；已经由 Standard 安全解决、没有 review 的 segmentation 不会被该层重新切分。

### 3.8 Report / validation / overlap safety

report schema 保持 `smart-1.1`。v1.2.0 的 sequence report 字段继续保留：

```text
text_sequence_reconciled_cue_count
text_sequence_reconciled_region_count
text_sequence_resolved_review_count
text_sequence_frontier_cue_count
text_sequence_frontier_run_count
text_sequence_projection_models
```

原有：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_edge_timing_recovery_count
text_edge_timing_recovery_block_count
text_review_count
```

继续保持：

- `timing_model_not_ready` -> review / Pro escalation；
- 无唯一 timed canonical mapping -> review / Pro escalation；
- C-grade identity -> review / Pro escalation；
- B-grade 只能由已经 ready 的 A-anchor model 做二次确认，不能反向参与主 timing 建模；
- sequence/timing/BPM recovered text 不倒灌成 A/B timing anchor；
- 自动 timing repair 必须满足 leave-one-out、最大 shift、no-new-overlap 与 combined-overlap guard。

### 3.9 Output safety

Smart CLI 在任何 artifact write 前检查 source SRT、canonical lyrics、output SRT、report 的解析路径；任何 output-input 或 output-output 碰撞 fail closed。

### 3.10 Smart v1.2.2 — BPM-validated text-only recovery

v1.2.2 解决一种真实生产里很常见、但 v1.2.0 Sequence Projection 会因 unique A 不足而保守 review 的情况：**歌曲存在大量重复歌词，但生产明确提供“原 BPM -> 目标 BPM”的固定匀速信息，而且多个 Standard-safe editor/canonical identity 已独立验证该倍率。**

BPM 仍然不是 timing authority。新增 `timeline/bpm_sequence_reconcile.py` 只建立 text-only projection，并要求：

- `provenance == bpm_derived`；
- anchor 只能来自 Text Repair 已安全成立的 1:1 baseline decision，不消费 timing/sequence/BPM 自己恢复的文字，避免循环自证；
- 至少 3 个安全 anchor，`750ms` inlier fraction `>=0.75`，source/mix span 均 `>=8s`，inlier median residual `<=300ms`；
- safe-anchor pairwise affine rate 与 BPM-derived rate 相对误差 `<=2.5%`，且 cue/canonical 顺序单调；否则整个 BPM text projection fail closed；
- 只恢复**已经映射到单条 canonical 的 review cue**，不靠 BPM 猜 unmapped cue，也不做整段 LRC repartition；
- candidate onset 默认需在 projection `450ms` 内；极低 lexical score `<0.20` 时收紧到 `220ms`；
- 相邻 cue 共同 claim 同一 canonical、明显 split continuation、下一条 lexical canonical 已在当前 cue 内开始等场景继续 review，避免把一整行/下一行强塞进当前格；
- interior candidate 必须由同源 inlier anchors 夹住；song-start 只开放最多 2 条紧邻 first inlier 的 lexical leading edge，不开放泛化 trailing chase；
- pure editor vocalization 不被 BPM 投影替换成 lexical lyric；pure canonical vocalization 也不用于该层 lexical recovery；
- 仅当删除受限的 `Oh/Ah/Yeah/哦/啊/耶/...` 边缘 vocalization 后，剩余 editor text 与 canonical lexical text **精确相等**时，才允许裁掉边缘 vocalization；`try/you/go/check it` 等普通词不属于该自动裁剪集合；
- recovered score cap `<=0.90`，永远低于 B-grade timing authority；final timing engine 继续完全忽略 BPM text recovery 作为 A/B anchor。

新增 report：

```text
text_bpm_projection_recovery_count
text_bpm_projection_vocalization_trim_count
text_bpm_projection_models
```

当前 Smart policy id：

```text
smart-validation-policy-2026-08-20-v1.2.2
```

### 3.11 v1.2.2 adjacent lexical ownership guard

BPM 单行 recovery 还必须尊重 editor 已经识别出的跨 LRC 行 ownership：如果当前 review cue 的 normalized 开头与上一 lexical canonical 的尾部有至少 2 字连续重合，或 normalized 结尾与下一 lexical canonical 的开头有至少 2 字连续重合，则该 cue 不允许被单条 canonical 自动覆盖。此类情况继续 review，避免为了模仿 LRC 行边界而删除 editor 已存在的相邻歌词片段。

该 guard 只减少自动 recovery 范围，不产生新的 canonical mapping，不改变 cue count/number/timing，也不获得 A/B timing authority。

## 4. Pro — Selective Audio Repair v1.1.1

Pro 是 Smart unresolved 的局部声学层，仍保持：

```text
timing_mutation_performed = false
```

Pro 只接受当前：

```text
schema_version = smart-1.1
policy_id      = current Smart production policy
```

因此任何旧 Smart policy artifact 在 v1.2.2 后都自动 stale，必须重跑当前 Smart。

reason-aware routing：

- timing review + canonical identity -> local source↔mix acoustic first；
- text/identity review -> bounded ASR + word timestamps；
- 无 word-timed canonical 且 source-side identity 需要加强 -> external forced alignment；
- unmapped review -> bounded ASR only。

Pro 只处理 Smart 明确 unresolved 的 bounded regions；**Smart 的 false-ready 不会被 Pro 自动兜底**，因此 lower-mode monotonicity / false-ready 回归测试属于 Smart 本身的发布门槛。

Enhanced LRC `end_ms=None`、adaptive source-window minimum、ASR-only region isolation、final `max_jobs` cap、shadow boundary competitor 与 source-I/O/path safety 继续保持 v1.1.1 收口语义。

## 5. Max — Full V4 Alignment

Full V4 继续作为 heavy fallback：coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整链路用于整体 timing 不可信、复杂结构或 Smart/Pro 无法安全收敛的任务。

Max 也必须遵守 segmentation authority：**更重的 evidence 不等于 LRC 行换行天然拥有最终 subtitle segmentation authority。** line-LRC 只能提供 line onset/grouping；要推翻可信 editor cue boundary，需要 word/token/audio 等独立证据。

## 6. Legacy Partial Timeline Repair P1–P5

旧 formal calibration/P9 proposal chain 不被 Smart/Pro 替换，继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro 不借用 P9 HIGH 或 P4 trust lock 获得新的自动修复权限，也不会反向提升旧 Partial chain authority。

## 7. 当前验证边界与下一步

Public CI 必须证明：

- Standard/Smart 对“canonical 连续内容一致但 LRC/editor 分句不同”的案例不跨可信 editor cue 搬字；
- Smart escalation + final overlap safety；
- severe-ASR 在主 timing model 只有 3 个 A 时，可由 `3A + >=1 strong B` 的独立 text-only projection 恢复 canonical sequence，但主 timing anchor count 仍保持 3；
- bounded sequence 能把多条 canonical LRC rows 稳定分配到较少的现有 editor cues，而不改变 cue timeline；
- Sequence Projection 不足证据（<=3 strong / 不足 A / span 不足 / unstable residual）必须 fail closed；
- frontier 遇到 timing discontinuity 必须停止，不能越过 cut/ad-lib 追 LRC；
- BPM-derived text projection 必须由独立 baseline-safe anchors 验证；BPM 不一致、anchor 不足、split continuation、pure vocalization 等必须 fail closed；
- BPM 单行 recovery 遇到 editor 已识别的上一行尾部/下一行开头 ownership 时必须 fail closed，不得删除相邻歌词片段；
- BPM-recovered text 不增加主 timing anchor_count，不获得 timing mutation authority；
- existing ready-model interior/song-edge recovery 不回归；
- recovery 不降低 Text Repair threshold、不把 recovered text 变成 A timing anchor；
- exact DAW hard prior / BPM-derived soft prior 的 timing authority 语义不变；
- Enhanced LRC open-ended token、stale Smart rejection、adaptive source window、ASR-only region、max-jobs、path collision、source-I/O 继续不回归；
- Python/ASR environment 与 legacy tests 全部继续通过。

Private real-song calibration 仍是 text recovery false-auto 风险的重要验收。真实任务发现的新 failure pattern 应继续转换成**通用、合成、无任务数据硬编码**的 regression；不得为了提高覆盖率把歌曲名、cue 编号、真实时间戳或真实歌词写进 production algorithm/public test。

### Smart v1.2.1 ownership closeout

Smart final text materialization 增加 editor cue ownership guard。canonical 继续拥有文字/顺序权威，但 line-LRC 行边界不能单独把原 editor 已识别的短语跨 cue 搬移。该 guard 只修复 sequence/text-repair 已产生的局部边界搬移或短重复，不扩大 canonical recovery 范围，不改变时间轴，不提升 timing authority。

### Smart v1.2.2 BPM text closeout

BPM-derived rate 在 timing 层仍是 soft prior；只有被多条 baseline-safe text identities 独立验证后，才可建立**文字专用** projection。该 projection 只减少可证明的 mapped review，不做全块重分、不填 pure vocalization、不改变 cue timeline，且任何恢复结果都不能成为 A/B timing anchor。BPM 单行 recovery 还必须保留 editor 已识别出的相邻 canonical 前缀/后缀 ownership；命中该 guard 时继续 review。当前 policy 为 `smart-validation-policy-2026-08-20-v1.2.2`。

### Smart v1.2.2 report semantics closeout

当前 v1.2.2 自动修复 authority 不变，但 report 现在明确区分：

```text
text_decision_replacement_count  = MatchDecision.action == replace
text_materialized_change_count   = 最终 text-only SRT 与 editor 原文逐 cue 的显示字符串变化
text_semantic_change_count       = normalized lyric 语义变化
```

同时输出 `text_mapped_review_count / text_unmapped_review_count / text_review_reason_counts`，以及 `timing_review_with_proposal_count / timing_review_without_proposal_count / timing_review_reason_counts`。`text_status` 与 `timing_status` 分轴报告；legacy strict `status` 仍只有两轴都 ready 才为 ready。

BPM compatibility 只对有真实 rate evidence 的 timing model 评估；`insufficient_anchors` + `rate_source=none` 的 placeholder rate 现在显示 `bpm_prior_compatible=null`，不再伪装成 BPM 冲突。

## Smart v1.2.3 bounded-stream closeout

Smart now has two BPM-derived text-only recovery tiers: conservative mapped 1:1 recovery and a stricter bilateral bounded-stream recovery. The latter is interior-only, requires same-source baseline-safe BPM inlier anchors on both sides, preserves all non-review lower-mode text, and fails closed on vocalization/ad-lib/cut/frontier evidence. It does not change cue count, numbering, start/end timing, primary timing authority, or the four-A timing gate.

## Smart v1.2.4 production-acceptance closeout

Smart v1.2.4 hardens the bounded-stream tier after a private 578-cue acceptance rerun: production zero-width unmatched spans are recognized, mapped reviews cannot absorb adjacent canonical rows, and Latin/mixed bounded repartition fails closed until token-aware display layout is implemented. These changes do not increase timing authority, do not alter cue count/timing, and do not lower the v1.2.2 mapped 1:1 recovery thresholds.
