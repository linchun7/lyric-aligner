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

## 3. Smart — Anchor Timeline Repair v1.1.3

Smart 是日常主力 no-audio 模式：**大部分信剪映 timing，但 canonical lyric 始终是最终文字/顺序 truth。**

实现：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
scripts/v4_smart_repair.py
```

### 3.1 Canonical timing / anchors

Smart 保留 line-level LRC timestamp、Enhanced LRC/QRC word/token timestamp。A/B/C identity 分级不变：只有 A-grade exact/unique/1:1 editor identity 可建立主 timing model；B/C 不得建立模型。

每首歌默认：

```text
source_time = offset + rate * mix_time
```

单倍率优先。rate authority：

```text
exact_daw        -> hard prior，可固定 rate
bpm_derived      -> soft plausibility，不硬锁 rate
anchor_estimated -> 无 hard prior 时由 A anchors robust estimate
```

BPM-derived 与稳定 A-anchor rate 冲突时，不用软先验重建模型；但该冲突会阻止自动 timing mutation。少量同歌多 rate / cut 仍升级而不是强迫普通任务进入重链路。

### 3.2 Segmentation authority / lower-mode monotonicity

v1.1.3 把真实生产回归暴露出的 segmentation 责任固定为生产合同：

```text
canonical lyric text/order -> authority
LRC line break             -> grouping/onset evidence only
trusted Jianying cue       -> display segmentation strong prior
word/token/audio evidence  -> may rebut editor boundary when independently strong
```

因此，连续歌词内容完全一致、只是 LRC 写成两行而剪映显示成三条 cue 时，Smart 必须继承 Standard 的 cue ownership；不能仅因为 canonical 行换行不同，把一段文字从正确的 editor cue 搬到前一个或后一个 cue。

### 3.3 Severe-ASR canonical text recovery

Smart 不降低 Text Repair V2 阈值。它先使用原有高可信 A anchors 建 independently-ready affine model，再二次处理 Text Repair 留下的 severe-ASR text review。

**Interior / 双侧恢复**继续要求：

- review block 两侧都有 `score >= 0.92` 的 single-line canonical text anchors；
- 两侧 anchors 同歌并与 ready affine model 在 750ms 内一致；
- 中间 canonical gap 连续、同源、完整；
- review cue starts 与 predicted canonical onsets 单调且每 cue 首 onset 在 750ms 内；
- 每 cue 最多 4 canonical lines，单 block 最多 8 cues。

**v1.1.3 新增 song-edge / 单侧恢复**，只用于歌曲最前/最后少数 canonical lines，且比 interior 更严格：

- initial affine model 必须已经由独立 A anchors `ready`；
- 可用的一侧必须至少有 **2 条紧邻、连续 canonical、`score >= 0.92` 的强 anchor**，每条也要与 model 在 750ms 内；
- candidate 只能位于该歌首/尾 **4 条 canonical rows** 内；
- candidate predicted onset 与 editor cue start 必须在更紧的 **500ms** 内；
- 允许跨过的 review cue 只能是**完全没有 canonical claim 的 unmapped editor-only ad-lib**，最多 3 条；弱 mapped cue 不能被当透明层跨过；
- ad-lib 本身保持原文和 review，不因恢复旁边歌词而被删除；
- recovery 只恢复 canonical text，不把 cue 提升为 A anchor，不获得 timing 自动写回权限。

恢复 reason：

```text
timing_model_confirms_canonical_sequence       # interior
timing_model_confirms_song_edge_canonical      # song edge
```

report 记录：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_edge_timing_recovery_count
text_edge_timing_recovery_block_count
text_review_count
```

### 3.4 Validation / overlap safety

- `timing_model_not_ready` -> review / Pro escalation；
- 无唯一 timed canonical mapping -> review / Pro escalation；
- C-grade identity -> review / Pro escalation；
- B-grade 只能由已经 ready 的 A-anchor model 做二次确认，不能反向参与建模；
- report schema 保持 `smart-1.1`，policy id 更新为当前 v1.1.3。

自动 timing repair 仍要求 leave-one-out independent model、足够 anchors / rate-supported edge extrapolation、最大 shift guard，并同时满足：

- 不得制造新 overlap；
- 不得扩大编辑器原本已有 overlap；
- 多条 proposal 组合后若冲突，相关 repair 全部降级 review。

### 3.5 Output safety

Smart CLI 在任何 artifact write 前检查 source SRT、canonical lyrics、output SRT、report 的解析路径；任何 output-input 或 output-output 碰撞 fail closed。

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

因此 Smart policy 升到 v1.1.3 后，旧 Smart artifact 自动 stale，必须重跑当前 Smart。

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
- severe-ASR interior recovery 只在 bilateral anchors + independently-ready affine model + compatible onset 成立；
- song-edge recovery 只在 edge scope + ready model + 连续一侧强 anchors + 500ms onset 成立；
- unmapped ad-lib 保留，弱 mapped cue 不可被跨过借远处 anchor；
- recovery 不降低 Text Repair threshold、不把 recovered text 变成 A timing anchor；
- exact DAW hard prior / BPM-derived soft prior；
- Enhanced LRC open-ended token、stale Smart rejection、adaptive source window、ASR-only region、max-jobs、path collision、source-I/O 继续不回归；
- Python/ASR environment 与 legacy tests 全部继续通过。

Private real-song calibration + independent blind 仍是 Pro 自动写回前的关键 gate。真实任务发现的新 failure pattern 应继续转换成**通用、合成、无任务数据硬编码**的 regression，再决定是否升级生产算法。
