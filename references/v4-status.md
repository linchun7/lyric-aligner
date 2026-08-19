# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
主线算法版本：`4.0.0a9`

> P3 前完整历史状态见 `references/archive/2026-08-19-pre-p3-v4-status.md`。真实生产设计基线见 `references/production-requirements.md`。

## 1. 当前四档产品路径

```text
Standard -> Text Repair V2.1
Smart    -> Anchor Timeline Repair（no-audio）
Pro      -> Selective Audio Repair（局部 audio evidence）
Max      -> Full V4 Alignment
```

共同事实：Canonical lyric 是最终文字/顺序 truth。Jianying timing 是强先验但可被充分独立证据推翻。Smart 使用 timed canonical + editor majority anchors 建 no-audio timing evidence；Pro/Max 才引入 acoustic Source-to-Mix。

韩文、日文或整段外文不因语言本身自动进入 Max。只要 canonical identity 与 timing anchors 足够，Smart 仍可成立；局部难点升级 Pro；只有整体 mapping/timeline 广泛不可信、anchor coverage 太弱或复杂结构无法局部解决时进入 Max。

## 2. Standard — Text Repair V2.1

已在 main 成立。适用于“规范歌词可信、剪映时间轴冻结、只修文字”：

- 不读取 audio；
- 不改变 cue count / number / start / end；
- 支持 deterministic 文字纠错与 bounded segmentation span；
- timestamped metadata、mixed timed/untimed canonical、layout-boundary insertion、ambiguous nearby match 等继续 fail closed；
- unmatched canonical occurrence 只形成 coverage warning，不与 cue review 混淆；
- production `auto-threshold >= 0.72`；
- report schema `2.1`；输出后重新断言 timeline signature 完全不变。

## 3. Smart — Anchor Timeline Repair v1

已实现：

```text
lyric_aligner/timeline/anchor_repair.py
scripts/v4_smart_repair.py
scripts/test_v4_anchor_timeline_repair.py
references/production-requirements.md
```

Smart 的职责是：**大部分信剪映，只推翻少量能被多重独立证据证明错误的 timing，并且默认完全不读音频。**

### 3.1 Canonical timing

Smart 使用现有 `lyric_aligner/text/canonical_lyrics.py`，因此保留：

- line-level LRC timestamp；
- Enhanced LRC word/token timestamp；
- QRC word/token timestamp。

逐字 timing 用于加强 lyric onset/boundary evidence；v1 不直接用 token end 强制重写 SRT end，以免把 karaoke segmentation 当作 subtitle display semantics。

### 3.2 Anchor trust

v1 建立 A/B/C identity grade：

- A：original editor text 与 canonical 唯一 exact 1:1 match，可建 timing model；
- B：仅经过小规模安全文字修复且 identity 强，可辅助/检查，但不建立主模型；
- C：merge/split/gap/repeated/ambiguous/较大修改，不建主模型，也不自动 timing repair。

因此 Text Repair 的猜测不会循环变成 timing proof。

### 3.3 Dominant affine model

每首歌默认：

```text
source_time = offset + rate * mix_time
```

常规单倍率变速优先。无先验时从 A anchors robust 估计 pairwise median slope；有 exact DAW stretch ratio 时使用 exact `rate_prior`；仅知道 BPM 时：

```text
rate_prior = target_bpm / source_bpm
```

如果 anchor 估计与 prior 冲突，模型不自动修 timing。少数同歌多速度/局部 cut 在 v1 先表现为 unstable/conflict 并升级，不把 rare piecewise 成本施加给普通任务。

### 3.4 Anti-circular / auto-repair boundary

A cue 被判定为 timing outlier 时，会把该 cue 从 anchor 集移除后重新拟合 leave-one-out model。

v1 初始保守边界：

```text
preserve tolerance <= 350ms
auto-repair candidate >= 900ms
max auto shift <= 8000ms
model median abs residual <= 450ms
model inlier fraction >= 0.70
```

interior auto repair 要求左右各至少 2 个独立 A anchors；song start/end 只有在存在 rate prior 且单侧至少 3 个 A anchors 时可外推。所有 candidate 还必须通过 monotonic/neighbor structural guard。

这些常量是首版安全边界，不是公开准确率承诺；后续用 private real-song blind 数据校准后再决定是否扩大 B anchor、piecewise 或自动写回范围。

### 3.5 Output

Smart 输出独立 SRT + strict JSON report，禁止覆盖 source SRT。report 记录：

- text repair/review；
- word-timed canonical coverage；
- per-song model；
- timing preserve/repair/review；
- evidence reasons；
- `audio_read=false`。

## 4. Pro — Selective Audio Repair v1

Smart→Pro production bridge 已实现到 **bounded evidence execution**，当前仍不自动 timing write-back。

新增：

```text
lyric_aligner/alignment/selective_repair.py
lyric_aligner/alignment/local_acoustic_match.py
scripts/v4_pro_selective.py
scripts/test_v4_selective_repair.py
```

### 4.1 Smart unresolved -> local jobs

Pro planner 只选择 Smart 中 `timing review` 或 `text review` cue。Smart 已 preserve/repair 的 cue 不进入 audio path。

每个 mapped job 带：

- cue/canonical/source identity + canonical SHA；
- bounded mix window；
- canonical line/token timing 派生的 bounded source window；
- Smart affine rate（若有效）；
- per-line ASR language hint；
- `source_local_acoustic_match`、`source_forced_alignment`、`mix_asr`、`word_timestamps` capability request。

plan 不保存 raw canonical text。没有单一 canonical identity 的 review cue 仍可请求 bounded mix ASR，但不能伪造 source-side evidence，并计入 `unmapped_review_count`。

### 4.2 Local source↔mix acoustic evidence

`local_acoustic_match.py` 复用 Full V4 已有 HPSS + Chroma CENS + MFCC retrieval，但只 decode 每个 Smart-selected job 的局部 mix/source 窗口。

有 Smart rate 时 slope search 收窄到约 `rate ± 0.06`；无 prior 时才使用较宽 local range。结果输出：

- estimated slope；
- Chroma/MFCC/fused score；
- candidate margin / ambiguity；
- matched source start；
- canonical onset 对应的 predicted mix start；
- 相对 editor cue start 的 residual。

`timing_mutation_performed=false`：当前只建立第二个独立 timing evidence family，不直接改 SRT。

### 4.3 Mixed-language ASR routing

ASR 不再只用 occurrence/track `language_profile`。

优先级：

```text
explicit job asr_language_hint
-> current canonical line language spans
-> whole-track profile only when canonical text unavailable
```

因此：

- 中文歌纯英文 rap line -> `en`；
- 中文+英文同一行 -> `None/auto`；
- 韩文纯行 -> `ko`；
- 日文纯行 -> `ja`；
- 不再因全曲标记 `zh` 把英文 rap 强制识别成中文。

### 4.4 Pro CLI

`scripts/v4_pro_selective.py`：

- Smart report + Smart SRT + timed canonical -> Pro plan；
- 不传 audio 时只 plan，0 audio execution；
- `--mix-audio + --source-audio + --acoustic-out` -> bounded source↔mix evidence；
- `--asr-model-id + --asr-out` -> 仅 plan windows 的 faster-whisper evidence；
- source language 可按 canonical filename/ordinal传入。

当前 source forced-alignment capability 已进入 plan，但 standalone Pro CLI 尚未直接编排 external forced-aligner；旧 executor 仍可复用，后续在真实任务需要时再完成统一 evidence fusion。

## 5. Max — Full V4 Alignment

现有 Full V4 继续作为 heavy fallback：coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整链路用于整体 timing 不可信、复杂结构或 Smart/Pro 无法安全收敛的任务。

Max 不再是“只要需要改 timing 就先跑”的默认路径。

## 6. Legacy Partial Timeline Repair P1–P5

P1–P5 的 formal calibration/P9 proposal chain 继续存在且不被 Smart/Pro 替换。它仍固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

P3 formal effective-run/fusion lineage、P4 strict calibration + independent blind trust lock、P5 Doctor/readiness 的原安全契约不变。

Smart/Pro 不借用 P9 HIGH 或 P4 trust lock 获得新的自动修复权限，也不会反向提升旧 Partial chain 的 authority。

## 7. 当前验证边界与下一步

Public synthetic CI 需要证明：

- Smart isolated outlier / repeated lyric / edge prior / word timing 合同继续成立；
- Pro 只计划 Smart unresolved cue，而不是重扫正常 cue；
- raw canonical text 不写进 Pro plan；
- 中文 track 中纯英文 line route 到 `en`，真实 code-switch 留 auto；
- bounded source↔mix evidence 能生成独立 timing prediction 且不自动 mutation；
- Python / ASR environment / legacy V4 tests 全部保持通过。

Private real-song calibration/blind 仍是下一关键 gate：

```text
Smart + Pro real-song calibration/blind
-> false timing repair / false ready / acoustic false-match
-> 设定 Pro evidence fusion 与自动 write-back threshold
-> 需要时接 source forced alignment 到 standalone Pro orchestration
-> 再决定 evidence-triggered piecewise Smart/Pro
```
