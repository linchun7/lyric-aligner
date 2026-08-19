# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
主线算法版本：`4.0.0a9`

> P3 前完整历史状态见 `references/archive/2026-08-19-pre-p3-v4-status.md`。真实生产设计基线见 `references/production-requirements.md`。Smart / Pro v1.1 细节见 `references/smart-pro-v1-1.md`。

## 1. 当前四档产品路径

```text
Standard -> Text Repair V2.1
Smart    -> Anchor Timeline Repair（no-audio）
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

## 3. Smart — Anchor Timeline Repair v1.1.4

Smart 是日常主力 no-audio 模式：**大部分信剪映 timing，但 canonical lyric 始终是最终文字/顺序 truth。** v1.1.4 的关键变化不是放宽 Text Repair 阈值，而是把“文字恢复证据”和“timing mutation authority”彻底分开，让 Smart 能吸收旧轻量模式在严重 ASR/复杂分句上的优势而不降低 timing 安全边界。

实现：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
lyric_aligner/timeline/text_recovery_consensus.py
scripts/v4_smart_repair.py
```

### 3.1 Canonical parser / metadata hardening

普通消费级 LRC 常把标题、制作人员、歌手角色标签与歌词一样加 timestamp。v1.1.4 的 canonical parser 明确：

- timestamp group 只有 metadata/credit/role/title 时自动忽略；
- 常见中英文冒号 credit label、Latin/digit role label 与首行 `artist - title` 不进入 canonical lyric；
- 同一 timestamp 真正存在多条 lexical alternatives 时仍 fail closed；
- 显式 TrackAsset selection 指向 metadata/blank 时仍拒绝；
- 不能为了兼容普通 LRC 而把真实多候选歧义静默选掉。

### 3.2 Strict timing authority 不变

Smart 保留 line-level LRC timestamp、Enhanced LRC/QRC word/token timestamp。A/B/C identity 分级不变：只有 A-grade exact/unique/1:1 editor identity 可建立**自动 timing mutation 的主模型**；B/C 不得建立该模型。

每首歌默认：

```text
source_time = offset + rate * mix_time
```

rate authority：

```text
exact_daw        -> hard prior，可固定 rate
bpm_derived      -> soft plausibility，不硬锁 rate
anchor_estimated -> 无 hard prior 时由 strict A anchors robust estimate
```

BPM-derived 与稳定 A-anchor rate 冲突时，不用软先验重建模型；但该冲突会阻止自动 timing mutation。少量同歌多 rate / cut 仍升级而不是强迫普通任务进入重链路。

### 3.3 Segmentation authority / lower-mode monotonicity

```text
canonical lyric text/order -> authority
LRC line break             -> grouping/onset evidence only
trusted Jianying cue       -> display segmentation strong prior
word/token/audio evidence  -> may rebut editor boundary when independently strong
```

连续歌词内容完全一致、只是 LRC 与剪映换行分组不同，Smart 必须继承 Standard 的 cue ownership；不能仅因为 canonical 行换行不同，把一段文字从正确的 editor cue 搬到前一个或后一个 cue。

### 3.4 v1.1.4 text-only recovery：局部双侧 evidence

`text_recovery_consensus.py` 新增 bounded local bilateral recovery。它只解决**文字**，不成为全歌 timing 模型：

- review block 最多 8 cue；
- block 左右必须各有一个已非 review、1↔1、single-canonical mapping，score 至少 0.80；
- 两侧属于同一 source，且 canonical gap 连续、同源；
- 由两侧 canonical onset 与 editor cue start 只在这个 bounded block 内推导一个 local affine mapping，rate 必须在 0.5–2.0；
- 如果整个 editor review block 与 canonical gap 的连续文字已经有足够 lexical 支持，优先把**完整 canonical span 通过原 editor 字符 ownership 投影回各 cue**；这一路径明确不制造伪 1↔1 canonical timing identity；
- lexical 证据太弱但 canonical onsets 与 cue starts 可由 local interpolation 证明时，再按 onset 单调分配；start tolerance 750ms、boundary guard 500ms、每 cue 最多 4 canonical lines；
- 成功 reason 分别为：

```text
local_bilateral_span_preserves_editor_segmentation
local_bilateral_timing_confirms_canonical_sequence
```

这个能力覆盖“一个 LRC 行跨两个正确 editor cue”和“严重 ASR 使文字相似度低、但左右顺序/timing 足以证明中间歌词”的场景，同时不降低全局 `auto-threshold`。

### 3.5 v1.1.4 text-only recovery：exact-consensus evidence

严格 A 模型为了 timing mutation 必须排除重复歌词；但重复副歌中的**原始 exact + unchanged + 1↔1** editor/canonical 映射仍可为“文字确认”提供强 affine 共识。v1.1.4 因此增加独立的 text-consensus model，且明确与 timing authority 隔离。

模型只从 recovery 前的原始 decisions 构造，并要求：

```text
anchor_count >= 6
distinct normalized exact texts >= 3
inlier_fraction >= 0.80
median_abs_residual_ms <= 150
```

重复歌词可以参与这个**text-only** robust consensus；模型 provenance 为 `text_exact_consensus_only`。一个 remaining 1↔1 text review 只有当其 mapped canonical onset 落在 editor cue interval 前后 250ms margin 内，才允许恢复 canonical text。

如果 editor cue 以单个常见中文语气词开头，且模型证明 canonical lyric onset 比 cue start 至少晚 300ms，可保留这个 leading editor-only ad-lib，再接 canonical text；否则不能凭文本猜测保留。

成功 reason：

```text
exact_consensus_timing_confirms_mapped_canonical
```

### 3.6 Anti-circularity：文字模型永不提升 timing 权限

所有 v1.1.2/v1.1.3/v1.1.4 text recovery reason 在送入最终 timing planner 时都会被 score-cap 到 `<0.92`。因此：

- recovered text 不可能成为 A；
- 也不会因为 recovery 自己升为 B；
- text-consensus/local-bilateral model 永远不作为 final automatic timing mutation model；
- final timing planner 仍只使用原 strict A model / exact DAW hard prior；
- recovered text 即使已正确，也可以继续 timing review / Pro。

已有 `text_recovery.py` 的 strict bilateral / one-sided edge recovery 继续存在；它在文字恢复阶段可优先消费 strict A-ready model，若该歌没有 strict model，也可消费满足上述高质量门槛的 text-consensus model，但这种消费仍是 text-only。

### 3.7 Smart report

schema 保持 `smart-1.1`，policy id 升到 v1.1.4。新增/明确：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_local_bilateral_recovery_count
text_local_bilateral_recovery_block_count
text_local_segmentation_preserve_count
text_local_timing_partition_count
text_consensus_timing_recovery_count
text_consensus_model_count
text_strict_timing_recovery_count
text_edge_timing_recovery_count
text_edge_timing_recovery_block_count
text_review_count
```

### 3.8 Validation / overlap safety

- `timing_model_not_ready` -> review / Pro escalation；
- 无唯一 timed canonical mapping -> review / Pro escalation；
- C-grade identity -> review / Pro escalation；
- B-grade 只能由已经 ready 的 strict A-anchor model 做二次确认，不能反向参与建模；
- 自动 timing repair 不得制造新 overlap、不得扩大已有 overlap；
- 多条 proposal 组合后若冲突，相关 repair 全部降级 review。

Smart CLI output/input path collision 继续 fail closed。

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

因此 Smart policy 升到 v1.1.4 后，旧 Smart artifact 自动 stale，必须重跑当前 Smart。

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

- consumer LRC 的 timed credit/role/title 行不会进入 canonical lyric，真实 lexical alternatives 仍 fail closed；
- Standard/Smart 不因 LRC 行换行不同跨可信 editor cue 搬字；
- local bilateral whole-span projection 保留 editor cue ownership；
- local bilateral timing partition 能恢复 bounded severe-ASR block，但不获得 timing authority；
- exact-consensus text model 只有满足 6 anchors / 3 distinct texts / 80% inlier / 150ms MAD 才可工作；
- canonical onset 不在 cue window 时 consensus recovery 必须拒绝；
- leading ad-lib 只有存在 timing lead 证据才保留；
- 所有 recovered reason 在 timing payload 中被压到 `<0.92`，不能升级成 primary A/B timing evidence；
- Smart escalation + final overlap safety；
- exact DAW hard prior / BPM-derived soft prior；
- Enhanced LRC open-ended token、stale Smart rejection、adaptive source window、ASR-only region、max-jobs、path collision、source-I/O 继续不回归；
- Python/ASR environment 与 legacy tests 全部继续通过。

Private real-song calibration + independent blind 仍是 Pro 自动写回前的关键 gate。真实任务发现的新 failure pattern 应继续转换成**通用、合成、无任务数据硬编码**的 regression，再决定是否升级生产算法。
