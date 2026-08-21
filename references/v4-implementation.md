# Lyric Aligner v4 实施记录与关键代码说明

> 真实生产 workload 的 normative baseline 见 `references/production-requirements.md`。当前状态见 `references/v4-status.md`，Smart / Pro 兼容约束见 `references/smart-pro-v1-1.md`。

## 1. Responsibility graph

```text
Canonical lyric -> final text/order truth
Editor SRT      -> strong but rebuttable mix-time + display-segmentation prior
Timed canonical -> Smart no-audio sequence/timing evidence
Source-to-Mix   -> Pro/Max primary acoustic timing truth
ASR / forced    -> auxiliary evidence
P9 fusion       -> legacy Partial shadow diagnostics
P4 trust lock   -> legacy Partial calibrated proposal eligibility
```

产品路径：

```text
Standard -> Smart -> Pro -> Max
```

跨模式 authority contract：

```text
canonical lyric text/order = authority
canonical LRC line break   = grouping/onset evidence, not subtitle cue authority
trusted Jianying cue       = display segmentation strong prior
word/token/audio evidence  = may rebut editor boundary when independently strong
```

更高模式可以增加证据、减少 review，但没有更强反证时不得破坏较低模式已经安全成立的文字、cue ownership 或 timing。

禁止：

```text
ASR/forced text -> final canonical lyric
LRC line break -> silently resegment trusted editor cues
higher mode -> regress lower-mode safe result without stronger evidence
one cue -> prove its own timing model
sequence/timing/BPM-recovered text -> become a primary timing anchor
unvalidated preserve -> task ready
BPM-derived prior -> silently become exact DAW rate
combined repair -> worsen editor overlap
rate change -> implicit cut
foreign-language label -> automatic Max
boundary competitor -> direct timing mutation
output artifact -> overwrite any production input
rare piecewise case -> force all normal songs through heavy mapping
```

## 2. Standard / Text Repair V2.1

Standard 冻结 editor timeline，只做 deterministic canonical text repair。它不读取 audio，不改变 cue count/number/start/end；production text threshold floor = 0.72。

Text Repair V2 负责 lexical-first 主文本匹配，包括 bounded 1↔N / N↔1 / N↔N span。similarity、length-ratio、ambiguity、layout-boundary guards 不因 Smart 升级而降低。

`text_repair._assign_targets()` 以 editor 原字符 ownership 为主做 canonical edit script。连续 editor/canonical 文本已经一致而仅 LRC 换行不同的 span，必须保持 editor cue segmentation；LRC 行边界本身不拥有跨 cue 搬字权限。

严重 ASR 乱码如果 lexical evidence 不够，会进入 review；Smart 可以用独立 sequence/timing/BPM-validated text evidence继续处理，但不得通过降低 Text Repair threshold 来制造更多 false auto。

## 3. Smart / Sequence Reconciliation + Anchor Timeline Repair v1.2.2

核心文件：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
lyric_aligner/timeline/sequence_reconcile.py
lyric_aligner/timeline/bpm_sequence_reconcile.py
lyric_aligner/timeline/ownership_guard.py
scripts/v4_smart_repair.py
lyric_aligner/io/path_safety.py
```

Smart report schema 继续 `smart-1.1`；当前 policy id：

```text
smart-validation-policy-2026-08-20-v1.2.2
```

### 3.1 Canonical representation

`parse_timed_canonical_files()` 复用 `text/canonical_lyrics.py`，同时提供：

```text
TimedCanonicalOccurrence.time_ms
TimedCanonicalOccurrence.tokens[]
CanonicalToken.start_ms/end_ms
line_lrc / enhanced_lrc / qrc_word_timing
RepairCanonicalLine text view
```

首 token onset 在合理 line-local 范围内可成为更细 timing onset；token end/LRC line break 不直接强迫 final SRT end 或 cue segmentation。

### 3.2 Primary timing identity / affine model

主 timing identity grade：

```text
A: exact + unique + 1:1 + unchanged
B: 1:1 + high similarity + safe text repair
C: merge/split/gap/repeated/ambiguous/sequence/BPM-recovered/other
```

只有 A 可建立 `SongTimingModel`。普通单曲仍优先：

```text
source_time = offset + rate * mix_time
```

- `exact_daw` -> hard rate prior；
- `bpm_derived` -> soft plausibility only；
- 无 hard prior -> A anchors robust pairwise median rate；
- candidate 自动 timing repair 使用 leave-one-out / independent support，不能用自身证明自身；
- B 只能被 already-ready A model 二次确认，不得建立 primary model；
- C 永不建立 primary model。

### 3.3 v1.1.x ready-model text recovery

`text_recovery.py` 保留两条更强 no-audio text recovery：

1. bilateral interior：ready four-A timing model + 左右同源 strong text anchors + complete canonical gap + compatible onsets；
2. narrow song-edge：ready model + one-sided consecutive strong anchors + edge scope + tighter onset guard，并只允许真正 unmapped editor-only ad-lib 作为透明层。

成功 reason：

```text
timing_model_confirms_canonical_sequence
timing_model_confirms_song_edge_canonical
```

它们仍在 Sequence Projection 之前执行，因为 independently-ready timing model 是更强证据。

### 3.4 为什么需要 Sequence Projection

真实 severe-ASR 会出现 bootstrap deadlock：

```text
editor text 错成另一句话
-> lexical span similarity 低 / mapping 错
-> 只有 3 个真正 A anchors
-> primary timing model 按四-A gate不 ready
-> ready-model text recovery 无法启动
-> 错误 editor text 被原样 materialize
```

放宽 lexical threshold 或四-A timing gate都会增加 false repair / circularity。v1.2.0 因此新增**只用于文字 identity 的独立投影**，不改变 timing gate。

### 3.5 SequenceProjectionModel

`sequence_reconcile.build_sequence_projection_models()` 从 baseline text decisions 建 text-only affine projection。

无 exact hard prior：

```text
>= 3 unique/exact/1:1/unchanged A text anchors
>= 4 total A/B strong text anchors
source span >= 8000ms
mix span    >= 8000ms
robust pairwise rate in [0.5, 2.0]
median abs residual <= 450ms
750ms inlier fraction >= 0.75
```

有 exact hard rate prior 时可在 `>=2 A strong anchors` 下使用 hard rate + median offset。BPM-derived prior不进入 hard map。

这个 model 的唯一职责：**判断 canonical sequence 在 editor 时间轴上的大致投影位置，从而恢复文字 identity。** 它不是 `SongTimingModel`，也不能授权 timing repair。

### 3.6 Anti-circularity：sequence text 永远不变 timing anchor

`sequence_reconcile._projected_decision()` 将所有 sequence-projected decision 的 score cap 到 `0.91`：

```text
score < 0.92
=> anchor_repair._decision_grade() 最多 C
=> 不可能成为 A/B timing anchor
```

因此典型目标行为是：

```text
baseline: 3 A + 1 B
text-only Sequence Projection: ready
severe-ASR text: safely recovered
final primary timing model: anchor_count 仍然 = 3
final timing status: 仍可能 insufficient_anchors / review
```

文字可以确定而 timing 仍 unresolved；这是刻意设计，不是失败。

### 3.7 Strongly bounded canonical sequence reconciliation

对 model-consistent strong anchors 按 editor cue 顺序排序。只有**相邻 strong anchors 同 source**且中间至少存在一个 review 时，才尝试 bounded sequence reconciliation。

设：

```text
left strong anchor
editor weak/review cues...
right strong anchor
```

两侧 canonical ordinal 唯一确定完整 gap。`_partition_bounded_region()` 不使用乱码 lexical similarity作为入场门槛，而是把 gap 的连续 canonical rows 分给现有 editor cues。

约束：

- strong anchors 自身 residual <= 750ms；
- block 最多 16 cues；
- canonical row count 必须在 `[cue_count, 4*cue_count]`；
- editor starts 与 projected onsets 单调；
- 每 cue 第一 projected onset 与 editor start <=1300ms；
- assigned last onset 不得明显越过 editor cue end；
- canonical gap 必须完整消费，不能丢行/回退/跨 source。

partition cost：

```text
current cue first-onset error
+ next canonical onset vs next editor cue start boundary error
+ small text-length ownership penalty
```

关键点是**用“下一条 canonical onset 是否对得上下一 editor cue start”决定 1↔N 分配**。例如 8 条短 LRC lines 可以稳定分入 4 个较长 editor cues；不会因为 LRC 有 8 行就创建 8 个 SRT cues。

成功 reason：

```text
sequence_projection_confirms_bounded_canonical
```

### 3.8 Frontier walk

如果 severe-ASR 位于最前/最后 strong anchor 外侧，`reconcile_text_from_sequence_projection()` 可沿 source canonical 顺序向外逐 cue 处理。

保守停止条件：

- current first projected onset 与 cue start >900ms；
- editor time 非单调；
- 遇到另一 model-consistent strong anchor；
- 没有可接受 candidate；
- 已证明当前 cue 后，下一 canonical onset 与下一 editor cue start boundary delta >1600ms。

frontier multi-line assignment 还要求 editor/canonical similarity >=0.42；这是为了防止在只有单侧序列约束时，仅凭 timing 把多条 canonical line 塞入一个弱 cue。

成功 reason：

```text
sequence_projection_confirms_frontier_canonical
```

这个 stop rule 是 cut/ad-lib 安全边界：允许恢复 break 之前已经被证明的 lyric cue，但禁止越过 break 继续追更远 LRC。

### 3.9 Segmentation authority

Sequence Projection 不处理纯 Standard-safe、无 review 的 region。因此已有可信 editor cue ownership不会因为新层存在而被重新分句。

对 severe-ASR bounded region，canonical 决定完整字符/行顺序；projection 只决定这些 canonical rows 应归属哪个**已有** editor cue。cue number/start/end 不改变。

要真正移动 editor cue boundary，仍需要 Enhanced/QRC word evidence 或 Pro/Max audio evidence；line-LRC 不足以授权。

### 3.10 Final timing / overlap safety

Sequence reconciliation 后重新调用 `build_anchor_timing_plan()`。sequence/BPM decisions 是 C grade，不增加 A model anchors。

`smart_policy.py` 的 timing hardening继续：

- `timing_model_not_ready` / no unique mapping / C identity -> review/Pro escalation；
- soft BPM conflict 阻止 mutation；
- 单 cue repair不得制造新 overlap；
- 所有 repairs 合成后逐相邻 pair 检查：`new_overlap_ms <= original_overlap_ms`；否则相关 repair全部降 review。

### 3.11 BPM-validated text-only recovery（v1.2.2）

`bpm_sequence_reconcile.py` 解决的是：已知每首歌通常以固定 BPM 变速到成片，但 repeated lyric / severe-ASR 导致 unique A anchor 不足时，怎样安全利用该信息帮助**文字**而不提升 timing 权限。

`bpm_derived` 仍不是 hard rate。每个 source 的 BPM text projection 要进入 `ready`，至少要求：

```text
>= 3 baseline-safe 1:1 text anchors
750ms 固定-rate inlier >= 3
inlier fraction >= 0.75
median abs residual <= 300ms
mix/source span >= 8000ms
anchor canonical order strictly monotonic
pairwise anchor-estimated rate 与 BPM rate relative error <= 2.5%
```

baseline-safe anchor 只接受原 Text Repair 已安全成立的：

```text
canonical_content_matches_source_segmentation
high_confidence_span_preserving_match
```

BPM recovery 只考虑当前仍为 `review` 且已经有**单一 canonical occurrence claim** 的 cue。候选还必须通过：

- projected onset 与 editor cue start 的紧阈值；
- interior 前后 inlier bracketing，或极窄 strict leading-edge 条件；
- adjacent cue 不得同时 claim 同一 canonical occurrence；
- split-continuation 风险阻断；
- 下一 lexical canonical 不得已经明显落入当前 cue；
- pure vocalization cue 不得被填成 lexical lyric。

此外，单条 BPM recovery 不能把 LRC line break 当成 editor ownership 真源。`_adjacent_lexical_overlap_risk()` 在 materialize 前检查当前 editor cue 的 normalized 文本：若 cue 开头已经包含上一 lexical canonical 的至少 2 字连续尾部，或 cue 结尾已经包含下一 lexical canonical 的至少 2 字连续前缀，则说明 editor 已经识别到跨 LRC 行的真实片段，该 cue 必须继续 review，禁止用单条 canonical 覆盖并删除相邻歌词。该 guard 只收紧 auto-recovery，不新增 mapping，也不改变 timing authority。

可选 vocalization trim 仅允许：editor 文字去掉边缘 `哦/啊/耶/oh/yeah/...` 后，normalized text **精确等于** canonical。此时只去掉多余 vocalization，不改变 canonical ownership。纯 vocalization 继续保留 review/供生产策略处理。

BPM-recovered decision 继续保持低权限：score cap 在 B-grade 以下，不得成为 primary timing anchor，也不得反向使自身 projection ready。

成功 report 字段：

```text
text_bpm_projection_recovery_count
text_bpm_projection_vocalization_trim_count
text_bpm_projection_models
```

`text_bpm_projection_models` 与 `text_sequence_projection_models` 都是 text-only diagnostics，不能与 primary timing `models` 混用。

### 3.12 v1.2.1 editor cue ownership guard

`timeline/ownership_guard.py` 位于全部 text recovery 之后、SRT text materialization 之前。输入是原 editor cues、当前 text decisions 与 replacements。它只检查相邻 cue 边界：若原 editor 可识别文本能证明 2–6 字短语属于另一侧，而当前 sequence/BPM 结果把该短语搬错，则可在相邻 cue 之间搬回；若同一短语被重复到边界两侧，则只允许删除一份已证明的短重复副本。普通搬移必须保持 pair-combined normalized lyric stream 完全不变。输出 decision 固定低于 B-grade，并清除 canonical span 以阻止其成为 timing anchor。

### 3.13 Report

既有字段：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_edge_timing_recovery_count
text_edge_timing_recovery_block_count
text_review_count
```

v1.2.0 新增：

```text
text_sequence_reconciled_cue_count
text_sequence_reconciled_region_count
text_sequence_resolved_review_count
text_sequence_frontier_cue_count
text_sequence_frontier_run_count
text_sequence_projection_models
```

v1.2.2 新增：

```text
text_bpm_projection_recovery_count
text_bpm_projection_vocalization_trim_count
text_bpm_projection_models
```

### 3.14 Artifact path safety

`io/path_safety.py::validate_separate_artifact_paths()` 继续在任何 artifact write 前拒绝 output-input / output-output 路径碰撞。

## 4. Pro / Selective Audio Repair v1.1.1

Pro 仍是 staged evidence path：

```text
Smart unresolved
-> reason-aware bounded plan
-> selected local acoustic / ASR / forced evidence
-> review/calibration
```

`timing_mutation_performed=false` 保持。

### 4.1 Exact Smart policy binding

`build_selective_repair_plan_v11()` 必须验证：

```text
smart_report.schema_version == SMART_SCHEMA_VERSION
smart_report.policy_id      == SMART_POLICY_ID
```

Smart policy 升到 v1.2.2 后，所有 v1.2.1 及更早 report 自动 stale，必须重跑 Smart。

### 4.2 Reason-aware routing

```text
timing review + mapped canonical -> source_local_acoustic_match
text/identity review             -> mix_asr + word_timestamps
no word timing + source identity needs reinforcement -> source_forced_alignment
unmapped review                  -> mix_asr + word_timestamps only
```

如果 Smart 已安全恢复 text、但 timing 仍 review，Pro 只按剩余 timing reason 路由，不重复做已解决 text review。

Pro 只能处理 Smart 明确 unresolved 的 cue；因此 Smart false-ready 不会被 Pro 自动兜底，segmentation/sequence monotonicity contract 必须在 Smart 自身 CI。

### 4.3 Existing hardening

继续保持：Enhanced LRC final token `end_ms=None` compatibility、adaptive source window、ASR-only region isolation、final `max_jobs` cap、shadow competitors、per-line language hint、only-needed source hash/bind 与 path safety。

## 5. Max / Full V4

Max 保留 coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整能力，只处理 broad untrusted timeline 与复杂 source identity。

Max 也必须遵守 segmentation authority：line-LRC 本身不能强迫 final subtitle cue boundary；推翻可信 editor segmentation 需要更强 word/token/audio evidence。

## 6. Legacy Partial Timeline Repair P1–P5

旧 formal proposal chain继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro 与旧 P9/P4 authority 来源独立，不能互相提升。

## 7. Validation boundary

Public tests必须证明：

- Standard/Smart 不因 LRC 行换行不同跨可信 editor cue 搬字；
- Smart primary timing four-A/leave-one-out contracts不变；
- 3A+1B 只能建立 text-only Sequence Projection，不能提升 primary timing anchor count；
- severe-ASR bounded canonical sequence 可在 projection 稳定时恢复；
- projection 证据不足/不稳必须 fail closed；
- BPM-derived rate 只有被多个 baseline-safe anchors 验证后才可提供 text-only recovery，不能成为 timing hard prior；
- pure vocalization、split continuation、重复 occurrence/邻 cue 冲突等必须 fail closed；
- editor cue 已识别上一 canonical 尾部或下一 canonical 前缀时，BPM 单行 recovery 必须 fail closed，不得删除该相邻歌词 ownership；
- optional vocalization trim 只能在剩余文字精确等于 canonical 时执行；
- BPM/sequence recovered text 不能增加 A/B primary timing anchor；
- frontier 遇到 timing break/cut/ad-lib 必须停止；
- existing ready-model bilateral/song-edge recovery继续成立；
- insufficient/unvalidated timing 必须 Pro escalation；
- final combined overlap 不新增/扩大；
- exact DAW hard prior 与 BPM-derived soft prior；
- Enhanced LRC / stale Smart / acoustic source-window / ASR-only region / max-jobs / path collision / forced protocol / multilingual routing继续不回归。

Public CI 不能证明真实歌曲 false-auto。每次 private real-song failure 应抽象成同构 synthetic regression，禁止歌曲、cue、timestamp、BPM 或真实歌词 hard-code 到 production algorithm/public test。

### 3.15 v1.2.2 report / diagnostic semantics hardening

`smart_policy.py` 的 report 层增加只读诊断，不改变 text/timing mutation gate：

- `_bpm_prior_compatibility()` 跳过 `rate_source=none/invalid` 的 placeholder model，只比较真正有 timing-rate evidence 的 source；
- `_text_materialization_counts()` 从实际 materialized text-only SRT 计算 exact display change 与 normalized semantic change，避免把 `MatchDecision.action` 误当成最终文件 diff；
- review reason counts 与 mapped/unmapped text review 直接由最终 decisions 汇总；
- timing review 按 `proposed_start_ms/proposed_end_ms` 是否存在拆成 concrete proposal 与 no-proposal 两类；后者表示当前 no-audio 证据不足，不能被解释为已知 timing 错误；
- `text_status/timing_status` 与 `pro_text_escalation_required/pro_timing_escalation_required` 是 strict overall status 的可解释分解，旧字段继续兼容。

### Smart v1.2.3 BPM bounded canonical stream

`timeline/bpm_sequence_reconcile.py` may now consume a complete lexical canonical gap between adjacent same-source BPM inlier anchors and repartition that stream across the existing editor cues. `_assign_targets` is used only inside a region that has passed BPM projection, bilateral-anchor, source-consistency, length, vocalization/ad-lib, boundary-insertion, short-cue, unmapped lexical-floor, and lower-mode immutability guards. Canonical row boundaries remain non-authoritative: one canonical row may intersect more than one editor cue. The resulting decisions use `sequence_projection_confirms_bpm_bounded_stream`, remain C-grade/below B timing authority, and cannot feed timing model construction.

### Smart v1.2.4 bounded-stream production guards

`timeline/bpm_sequence_reconcile.py` normalizes absent and zero-width canonical claims into one unmapped semantic state. The v1.2.3 bilateral stream path is further constrained so a previously mapped review cannot expand beyond its existing canonical span; this prevents canonical correctness at region level from overriding editor cue ownership. Until token-boundary-aware Latin rendering exists, the new multi-cue bounded tier rejects gaps containing Latin text; the older mapped 1:1 BPM text path remains unchanged.
