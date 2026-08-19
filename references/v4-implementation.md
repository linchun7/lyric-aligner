# Lyric Aligner v4 实施记录与关键代码说明

> 真实生产 workload 的 normative baseline 见 `references/production-requirements.md`。Smart / Pro v1.1 设计细节见 `references/smart-pro-v1-1.md`。

## 1. Responsibility graph

```text
Canonical lyric -> final text/order truth
Editor SRT      -> strong but rebuttable mix-time + display-segmentation prior
Timed canonical -> Smart no-audio timing evidence
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

更高模式必须满足**能力单调性**：可以增加证据、解决更多 review，但没有更强反证时不得破坏较低模式已经安全成立的文字、cue ownership 或 timing。

禁止：

```text
ASR/forced text -> final canonical lyric
LRC line break -> silently resegment trusted editor cues
higher mode -> regress a lower-mode safe result without stronger evidence
text-only recovery model -> automatic timing mutation authority
one cue -> prove its own timing model
text recovered from timing -> become a primary timing anchor
unvalidated preserve -> task ready
BPM-derived prior -> silently become exact DAW rate
combined repair -> worsen editor overlap
rate change -> implicit cut
foreign-language label -> automatic Max
boundary competitor -> direct timing mutation
output artifact -> overwrite any production input
```

## 2. Standard / Text Repair V2.1

Standard 冻结 editor timeline，只做 deterministic canonical text repair。它不读取 audio，不改变 cue count/number/start/end；production text threshold floor = 0.72。

Text Repair V2 负责 lexical-first 主文本匹配，包括 bounded 1↔N / N↔1 / N↔N span。similarity、length-ratio、ambiguity、layout-boundary guards 不因 Smart 升级而降低。

重要 segmentation contract：当一个多 cue span 的 editor 连续文字与 canonical 连续文字规范化后已经一致，只是 canonical LRC 行换行不同，Text Repair 保留原 editor cue ownership。LRC 行换行本身不能把词从一个正确 cue 搬到另一个 cue。

严重 ASR 乱码如果无法通过 lexical guards，会先进入 review，再由 Smart 的独立 timing/order evidence 决定是否能恢复 canonical text。

## 3. Smart / Anchor Timeline Repair v1.1.4

核心文件：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
lyric_aligner/timeline/text_recovery_consensus.py
lyric_aligner/text/canonical_lyrics.py
lyric_aligner/text/normalization.py
scripts/v4_smart_repair.py
lyric_aligner/io/path_safety.py
```

### 3.1 Canonical representation + ordinary consumer LRC metadata

Smart 复用 `text/canonical_lyrics.py`，保留：

```text
CanonicalLine.time_ms
CanonicalLine.tokens[]
CanonicalToken.start_ms/end_ms
line_lrc / enhanced_lrc / qrc_word_timing
```

`parse_timed_canonical_files()` 同时提供 timing-rich occurrence 与 Text Repair canonical view。首 token onset 在合理 line-local 范围内可作为更细 anchor onset；token end 不直接强迫 SRT end，canonical 行/token 边界也不自动成为 final subtitle segmentation。

v1.1.4 额外收口普通 LRC 的 timed metadata：

- `META_RE` 支持常见中文/英文 credit labels 的 `:` / `：`；
- Latin/digit singer/role labels（如 `Name:`）由 `ROLE_LABEL_RE` 识别；
- 最前约 1 秒内常见 `artist - title` timed row 视为 title intro；
- 在没有显式 TrackAsset selection 时，metadata-only timestamp group 直接忽略；
- 真正存在多条 lexical alternatives 的 timestamp group 仍 `CanonicalLyricError` fail closed；
- 显式 selection 指向 metadata/blank/title intro 仍拒绝。

这只修 parser compatibility，不改变 canonical truth 选择纪律。

### 3.2 Strict timing authority

Identity grade 继续：

```text
A: exact + unique + 1:1 + unchanged
B: unique 1:1 + high similarity + safe small text repair
C: merge/split/gap/repeated/ambiguous/other
```

只有 A 可建立**最终 automatic timing mutation** 主模型。普通单曲仍使用：

```text
source_time = offset + rate * mix_time
```

rate authority：

- `exact_daw` 进入 `build_anchor_timing_plan()` hard prior；
- `bpm_derived` 不进入 hard-prior map，由 strict A anchors robust estimate rate；
- BPM-derived 只做兼容性检查，相对差异超过 3% 时阻止自动 repair；
- 没有 external hard prior 时使用 `robust_anchor_estimate`。

report 将实际模型 rate 来源与外部 prior 来源分开：

```text
rate_provenance
rate_prior_provenance
rate_prior_value
bpm_prior_relative_error
bpm_prior_compatible
```

### 3.3 Anti-circular + readiness

候选 A cue 必须 leave-one-out 后重新建 independent model，再判断自身 residual。

- `timing_model_not_ready` -> review；
- 无唯一 timed canonical mapping -> review；
- C-grade identity -> review；
- B 不参与建模，只能被 already-ready strict A-anchor model 二次确认。

v1.1.4 在 `smart_policy._text_payload(..., for_timing=True)` 对所有 text-recovery reasons 强制：

```text
score < 0.92
```

这不是改变 report 原始 score，而是只对**timing planner 的内部 payload**降权，保证任何由 local/consensus/strict text recovery 得到的 cue 都不能成为 A/B primary timing evidence。

### 3.4 v1.1.4 local bilateral TEXT recovery

新模块 `timeline/text_recovery_consensus.py` 在原 strict recovery 之前运行。第一类能力是 local bilateral recovery：

1. 连续 review block 最多 8 cue；
2. 左右相邻 decisions 都必须已经非 review，且分别是单 cue↔单 canonical occurrence，score `>=0.80`；
3. 两侧 canonical occurrence 必须同 source，且中间 canonical gap 连续、同源；
4. 只用左右两个 boundary 的 `editor start ↔ canonical onset` 构造 bounded local affine interpolation；rate 必须在 `0.5–2.0`；
5. 该 local model **永远不写入 final timing model**。

#### Whole-span ownership projection

优先路径是把 review block 所有 editor cue 的连续文本与 canonical gap 连续文本一起比较。若 grouped score `>=0.55`：

- 调用 Text Repair 已有 `_assign_targets()`；
- 用 canonical 正确字符修复整个 span；
- 字符仍按原 editor cue ownership 投影；
- insertion 落 cue/layout boundary 或会清空 cue 时继续 fail closed；
- 因为一个 canonical line 可能横跨多个 editor cue，这一路径故意设置 `canonical_span=None`，不伪造 1↔1 timing identity。

reason：

```text
local_bilateral_span_preserves_editor_segmentation
```

#### Local onset partition

如果 grouped lexical evidence 太弱，则使用 local affine + canonical line onsets：

- cue first-onset tolerance `750ms`；
- boundary guard `500ms`；
- 每 cue 最多吸收 4 canonical lines；
- canonical gap 必须完整消费、顺序单调。

reason：

```text
local_bilateral_timing_confirms_canonical_sequence
```

它解决 severe-ASR 使文本相似度失效、但左右 canonical order + local timing 已足以确定文字的场景；仍然只是 TEXT recovery。

### 3.5 v1.1.4 exact-consensus TEXT model

严格 A timing 模型为了 mutation 安全会排除重复歌词；但重复副歌中的 original editor text 如果已经与某个 monotonic canonical occurrence **exact + unchanged + 1↔1**，仍可安全用于一个独立的 text-recovery consensus。

`_build_exact_consensus_models()` 只消费 recovery 前的 original decisions。重复 exact text 可以参加 robust fit，但 model 必须同时满足：

```text
anchor_count >= 6
distinct normalized exact texts >= 3
inlier_fraction >= 0.80
median_abs_residual_ms <= 150
```

其 provenance：

```text
text_exact_consensus_only
```

remaining 1↔1 text review 只有在 mapped canonical onset 经该模型投影后落入：

```text
editor cue interval ±250ms
```

才允许 canonical text recovery。模型本身不进入 final timing planner。

对于 editor cue 开头的单个常见中文语气词，只有当 predicted canonical onset 比 cue start 至少晚 `300ms` 时，才保留该 leading editor-only ad-lib；否则直接使用 canonical text，避免凭文本猜测保留。

reason：

```text
exact_consensus_timing_confirms_mapped_canonical
```

### 3.6 Existing strict bilateral / edge recovery

`timeline/text_recovery.py` 继续保留 v1.1.2 bilateral interior 和 v1.1.3 one-sided edge contracts。v1.1.4 的区别是：**它们只处于 text recovery 阶段时**，可从 `_recovery_model_pool()` 获得：

1. 首选 strict ready A model；
2. strict model 不存在时，允许上述通过高质量 gate 的 exact-consensus text model。

这不会提升 timing authority，因为 final timing planner 不接收 consensus model，而且 recovered reason 在 timing payload 中被 `<0.92` score cap。

### 3.7 Segmentation authority regression

Public regression 使用合成文本锁定以下结构：

```text
editor cues:
第一段歌词到这里
下一小句仍在同一画面
最后几个字继续播放

canonical LRC lines:
第一段歌词到这里下一小句
仍在同一画面最后几个字继续播放
```

两边连续规范文本一致，只有行分句不同。Standard/Smart 必须保持 editor cue ownership。v1.1.4 还新增“一个 canonical line 横跨两个 review editor cue”的 local whole-span regression，证明发生纠错时也不能因为 LRC line boundary 把词搬错 cue。

### 3.8 Report

Smart schema 继续 `smart-1.1`；policy id 更新到 v1.1.4。report 新增/明确：

```text
text_local_bilateral_recovery_count
text_local_bilateral_recovery_block_count
text_local_segmentation_preserve_count
text_local_timing_partition_count
text_consensus_timing_recovery_count
text_consensus_model_count
text_strict_timing_recovery_count
```

已有 `text_timing_recovery_count` 现在表示各 text recovery stage 的总 cue 数；`text_timing_recovery_block_count` 表示 block-based local/strict recovery 总 block 数。

### 3.9 Final timeline overlap guard / artifact safety

`smart_policy.py` 两层 overlap guard 保持：

1. 单条 proposal 不得制造原本不存在的新 overlap；
2. 所有 repair 合成最终 timeline 后要求 `new_overlap_ms <= original_overlap_ms`。

Smart CLI 在任何 artifact write 前检查 source SRT、canonical lyrics、output SRT、report；output-input / output-output 碰撞 fail closed。

## 4. Pro / Selective Audio Repair v1.1.1

核心文件：

```text
lyric_aligner/alignment/selective_repair.py
lyric_aligner/alignment/selective_policy.py
lyric_aligner/alignment/local_acoustic_match.py
lyric_aligner/alignment/local_acoustic_v11.py
scripts/v4_pro_selective.py
lyric_aligner/io/path_safety.py
```

Pro 仍是 staged evidence path：

```text
Smart unresolved
-> reason-aware bounded plan
-> selected local acoustic / ASR / forced evidence
-> review/calibration
```

`timing_mutation_performed=false` 保持。

### 4.1 Exact Smart policy binding

`build_selective_repair_plan_v11()` 验证：

```text
smart_report.schema_version == SMART_SCHEMA_VERSION
smart_report.policy_id      == SMART_POLICY_ID
```

Smart policy 升到 v1.1.4 后，旧 Smart report 自动 stale，必须重跑。

### 4.2 Reason-aware routing

```text
timing review + mapped canonical -> source_local_acoustic_match
text/identity review             -> mix_asr + word_timestamps
no word timing + source identity needs reinforcement -> source_forced_alignment
unmapped review                  -> mix_asr + word_timestamps only
```

如果 Smart 已安全恢复 text，但 timing 仍 review，Pro 只按剩余 timing reason 路由，不重复做已解决的 text review。

Pro 只能处理 Smart 自己明确 unresolved 的 cue；因此 Smart false-ready 不会被 Pro 自动兜底，segmentation/monotonicity contract 必须在 Smart 入口自身测试。

### 4.3 Existing v1.1.1 hardening

继续保持：

- Enhanced LRC final token `end_ms=None` compatibility；
- adaptive source window 至少覆盖 `mix query duration × max candidate slope + 750ms`；
- ASR-only jobs 不扩大 acoustic region；
- final `max_jobs` 包含 shadow competitors；
- boundary competitor `shadow_evidence_only=true`；
- per-line language hint 优先 whole-track profile；
- only-needed source audio hash/bind；
- Smart/Pro output path collision fail closed。

## 5. Max / Full V4

Max 保留 coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整能力，只处理 broad untrusted timeline 与复杂 source identity。

Max 也必须遵守 segmentation authority：line-LRC 本身不能强迫 final subtitle cue boundary。要推翻可信 editor segmentation，需要更强的 word/token/audio evidence；更重的模式不是“天然可以重分句”的许可证。

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

- timestamped credits / role labels / title intro 不污染 canonical lyric；真实多 lexical alternatives 仍 fail closed；
- Standard/Smart 不因 LRC line grouping 跨可信 editor cue 搬字；
- local bilateral whole-span projection 保留 editor character ownership；
- severe-ASR local onset partition 只在 bounded bilateral evidence 下恢复；
- exact-consensus model 只有达到 6 anchors、3 distinct texts、80% inliers、150ms MAD 才可使用；
- candidate canonical onset 落在 cue window 外时不恢复；
- leading ad-lib 只有 timing lead 证据才保留；
- recovered decisions 在 timing payload 中强制 `<0.92`，不能变成 primary A/B anchors；
- strict A timing mutation / leave-one-out / overlap / DAW-vs-BPM contracts不回归；
- Enhanced LRC / stale Smart / acoustic source-window / ASR-only region / max-jobs / path collision / forced protocol / multilingual routing继续不回归。

Public CI 仍不能证明真实歌曲 false-auto / false acoustic match。Pro evidence fusion 与自动 timing writeback必须等待 private real-song calibration + independent blind。真实生产 failure 应转换成同构的合成 regression，禁止将歌曲、cue、timestamp 或任务文本 hard-code 到 production algorithm/public test。
