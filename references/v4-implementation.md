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
one cue -> prove its own timing model
text recovered from timing -> become a primary timing anchor
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

重要 segmentation contract：当一个多 cue span 的 editor 连续文字与 canonical 连续文字规范化后已经一致，只是 canonical LRC 行换行不同，Text Repair 保留原 editor cue ownership。LRC 行换行本身不能把词从一个正确 cue 搬到另一个 cue。

严重 ASR 乱码如果无法通过 lexical guards，会先进入 review，再由 Smart 的独立 timing evidence决定是否能恢复 canonical text。

## 3. Smart / Anchor Timeline Repair v1.1.3

核心文件：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
scripts/v4_smart_repair.py
lyric_aligner/io/path_safety.py
```

### 3.1 Canonical representation

Smart 复用 `text/canonical_lyrics.py`，保留：

```text
CanonicalLine.time_ms
CanonicalLine.tokens[]
CanonicalToken.start_ms/end_ms
line_lrc / enhanced_lrc / qrc_word_timing
```

`parse_timed_canonical_files()` 同时提供 timing-rich occurrence 与 Text Repair canonical view。首 token onset 在合理 line-local 范围内可作为更细 anchor onset；token end 不直接强迫 SRT end，canonical 行/token 边界也不自动成为 final subtitle segmentation。

### 3.2 Identity / affine model

Identity grade：

```text
A: exact + unique + 1:1 + unchanged
B: unique 1:1 + high similarity + safe small text repair
C: merge/split/gap/repeated/ambiguous/other
```

只有 A 可建立主 timing model。普通单曲仍使用：

```text
source_time = offset + rate * mix_time
```

rate authority：

- `exact_daw` 进入 `build_anchor_timing_plan()` hard prior；
- `bpm_derived` 不进入 hard-prior map，由 A anchors robust estimate rate；
- BPM-derived 只做兼容性检查；相对差异超过 3% 时阻止自动 repair；
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
- B 不参与建模，只能被 already-ready A-anchor model 二次确认；
- recovered text 不倒灌为 A/B primary timing evidence。

### 3.4 Severe-ASR canonical text recovery

执行顺序固定：

```text
Text Repair V2 lexical plan
    -> original A-grade decisions only
    -> build initial affine timing model
    -> text_recovery revisits bounded review blocks
    -> materialize recovered canonical text
    -> rebuild final timing plan/report
```

#### Interior bilateral path

延续 v1.1.2：

1. review block 左右都是 `score >= 0.92` single-line text anchors；
2. 两侧 anchors 同 source；
3. source initial affine model 已 `ready`；
4. 两侧 anchor 各自与 model 误差 `<= 750ms`；
5. canonical gap 非空、连续、同源；
6. cue starts 与 predicted onsets 单调；
7. 每 cue 第一 canonical onset 与 editor start `<= 750ms`；
8. 单 cue 最多 4 canonical lines，单 block 最多 8 cues；
9. canonical gap 必须完整消费。

成功 reason：

```text
timing_model_confirms_canonical_sequence
```

#### Song-edge one-sided path — v1.1.3

真实 190《在梅边》暴露：歌曲开头被 editor-only ad-lib 隔开时，双侧 anchor contract 会漏掉一个 timing 已高度吻合的 severe-ASR lyric cue。v1.1.3 增加更窄的一侧恢复，不放宽 Text Repair 阈值：

1. initial affine model 必须已由独立 A anchors `ready`；
2. candidate 只能位于该 source 的首/尾 4 条 canonical rows；
3. 可用的一侧必须至少有 2 条**紧邻、连续 canonical、score>=0.92** 强 anchor；
4. anchor/model residual 各 `<=750ms`；
5. candidate predicted onset 与 editor cue start 使用更紧的 `<=500ms`；
6. block 中允许留在原地的跨越项只允许 `canonical_ordinal=None && canonical_span=None` 的真正 unmapped review cue，即 editor-only ad-lib；最多 3 条；
7. 任何 weak mapped cue 都不是透明层，立即阻断 one-sided recovery；
8. ad-lib 不删除、不改写，继续 review；
9. 恢复后的 lyric cue 仍不成为 A timing anchor，也不获得 timing 自动写回权限。

成功 reason：

```text
timing_model_confirms_song_edge_canonical
```

report：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_edge_timing_recovery_count
text_edge_timing_recovery_block_count
text_review_count
```

### 3.5 Segmentation authority regression

190 的 11:24 案例成为永久回归：

```text
editor cues:
为他而学着唱的情歌
他早忘了但是还在你的播放
列表里面排到前几位

canonical LRC lines:
为他而学着唱的情歌他早忘了
但是还在你的播放列表里面排到前几位
```

两边连续规范文本一致，只有行分句不同。Standard/Smart 必须保持 editor 三个 cue 的文字 ownership；不得把“他早忘了”移到前一 cue。这个 contract 与“canonical lyric 决定文字/顺序”不冲突：canonical 决定**字是什么、顺序是什么**，可信 editor cue 决定**这些字在哪个显示时间块**，除非有更强 boundary evidence 推翻它。

### 3.6 Final timeline overlap guard

`smart_policy.py` 有两层 guard：

1. 单条 proposal 不得制造原本不存在的新 overlap；
2. 所有 repair 合成最终 timeline 后逐相邻 pair 检查：

```text
new_overlap_ms <= original_overlap_ms
```

组合后 overlap 增大时，涉及 repair 全部降级 review。

### 3.7 Artifact path safety

`io/path_safety.py::validate_separate_artifact_paths()` 检查：

- output 不得等于任何 input；
- 两个 output 不得共享同一路径。

Smart CLI 在任何 artifact write 前检查 source SRT、canonical lyrics、output SRT、report。

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

Smart policy 升到 v1.1.3 后，旧 Smart report 自动 stale，必须重跑。

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

- Standard/Smart 不因 LRC 行换行不同跨可信 editor cue 搬字；
- Smart no-audio / A-anchor / leave-one-out contracts；
- insufficient/unvalidated timing 必须 Pro escalation；
- severe-ASR interior recovery 仅在 bilateral anchors + independently-ready model + compatible onset 成立；
- song-edge recovery 仅在 edge scope + ready model + >=2 consecutive one-sided strong anchors + <=500ms candidate onset 成立；
- unmapped ad-lib 保留，weak mapped cue 不可被跨过；
- recovered text 不升级为 A timing anchor；
- final combined overlap 不新增/扩大；
- exact DAW hard prior 与 BPM-derived soft prior；
- Enhanced LRC / stale Smart / acoustic source-window / ASR-only region / max-jobs / path collision / forced protocol / multilingual routing继续不回归。

Public CI 仍不能证明真实歌曲 false-auto / false acoustic match。Pro evidence fusion 与自动 timing writeback必须等待 private real-song calibration + independent blind。190 真实回归继续作为 Smart 文本/segmentation 方向的生产验收样本，但不得将具体歌曲、cue 或 timestamp hard-code 到 production algorithm。
