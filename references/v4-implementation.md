# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2 新增的是非权威 evidence layer，不改变 calibrated acoustic profile 或 final timeline authority。

## 1. 当前分层

```text
lyric_aligner/
  assets/       # TrackAsset / occurrence / fail-closed resolution
  audio/        # HPSS/chroma/MFCC / coarse / TimeWarp / Fine / transition / cuts
  contracts/    # immutable artifact lineage
  evidence/
    editor.py   # P2 language-aware editor shadow evidence
  evaluation/   # P1 strict calibration/blind + P1.1 readiness
  pipeline/     # production orchestration/context
  review/       # replayable candidate-level decisions
  text/
    canonical_lyrics.py
    language_spans.py
  timeline/
    projector.py
    overlap.py
    cuts.py
    composition.py
    composer.py
  qa/
```

关键 CLI：

```text
v4_run.py
v4_review.py
v4_recompose_overlap.py
v4_rebuild_cut.py
v4_compose_materializations.py
v4_render.py
v4_validate_release.py
v4_dataset_readiness.py
v4_calibration_workflow.py
v4_editor_evidence.py
```

## 2. Authority graph

不变的事实真源：

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
```

P2 增加：

```text
Editor/Jianying SRT -> non-authoritative evidence sensor
```

因此数据流是：

```text
TrackAsset + Source audio
        ↓
Source-to-Mix
        ↓
Canonical Timeline ───────────────┐
                                  ├─> editor_evidence_shadow
Task-fingerprinted source_srt ────┘
```

而不是：

```text
editor SRT -> rewrite canonical timeline
```

## 3. `evidence/editor.py`

### 3.1 Span-level policy

复用 `text/language_spans.py` 对 canonical line 做脚本/语言 span：

```text
EN/ZH -> direct_text
KO/JA -> phonetic_hint
YUE/unknown -> timing_hint
mixed -> per-span
```

每个 span 保留：language、script、mode、text SHA、text/timing weight。

### 3.2 Direct text

EN/ZH 使用 NFKC/casefold/alnum normalization，加 sequence ratio 与 LCS containment coverage 形成 shadow direct support。

该 score 只说明“editor text 与某 canonical span 有多像”，不允许产生 canonical text replacement。

### 3.3 Korean phonetic

内置 Hangul syllable decomposition 生成 conservative Romanization，用于比较类似：

```text
canonical Hangul
vs
editor Latin phonetic approximation
```

它属于 `phonetic_hint`，权重低于 direct EN/ZH，并且永远不能变成 final text。

### 3.4 Japanese phonetic

Kana 使用保守内置 romanization。

Canonical span 若包含 Kanji/Han，而没有 vetted pronunciation backend：

```text
phonetic_support_score = null
reason = kanji_reading_unavailable
```

禁止猜读音。

### 3.5 Cantonese

当前没有 vetted Jyutping backend，因此：

```text
mode = timing_hint
text_weight = 0
```

禁止拿 Mandarin pinyin、同形汉字 direct match 或 editor Latin output 伪装成 Cantonese pronunciation evidence。

## 4. Timing evidence

每条 canonical line 只搜索 configurable local radius（bootstrap 2500ms）的 editor cues。

Timing score 使用：

- canonical/editor interval overlap IoU；
- interval center proximity。

每个候选保存 onset/offset delta，但：

```text
automatic_timing_change_allowed = false
```

P2 没有 timeline mutation function。

## 5. Candidate ranking

Shadow candidate rank 将 timing support 与当前 span-derived text/phonetic support 按 bootstrap trust 合成，只用于：

- 排序 top editor candidates；
- 计算 best-vs-second margin；
- real calibration/review 分析。

明确字段：

```text
rank_score_uncalibrated
best_candidate_margin_uncalibrated
policy_calibrated = false
```

这些不是 production confidence thresholds。

## 6. Privacy

Evidence artifact 不存 raw canonical/editor text。

存储：

```text
canonical_line_index
canonical_text_sha256
editor cue number/start/end
editor_text_sha256
span text_sha256
score/margin/delta
```

这样 artifact 可提交/对比而不复制私有歌词正文。

## 7. `v4_editor_evidence.py`

### Input

```text
task_manifest
source effective v4 run + run artifact
```

Editor SRT 不单独从命令行随意传入；它从 task manifest 的 `source_srt` role 解析，因此已经属于 task fingerprint。

### Supported source run stages

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

### Timeline validation

每个 occurrence 的 timeline artifact 必须：

- stage 属于 canonical/overlap/cut/combined timeline；
- artifact output hash 正确；
- task/algorithm identity 正确；
- artifact ID 在 source run upstream；
- occurrence/track identity 与 run 一致。

没有可 materialize timeline 的 blocked occurrence 会记录 skipped；如果整次 run 没有任何 canonical timeline，stage 失败。

### Output artifact

```text
stage = editor_evidence_shadow
role = editor_evidence
upstream = source run artifact + exact timeline artifact IDs
```

Normalized config 绑定：policy ID、uncalibrated state、search radius、candidate count、source run ID、source SRT SHA。

## 8. Legacy policy adapter

旧 `scripts/editor_evidence.py` 不再拥有独立 profile truth，而只是 package policy adapter。

特别是：

- YUE 旧 line-level text weight 不再保留；
- mixed/auto/generic line-level adapter 只能给 timing-only conservative view；
- 真正 mixed handling 在 package per-span layer。

## 9. P1/P2 连接方式

P2 的正确下一步不是直接写入 final timing，而是：

```text
P2 shadow evidence
 + private reference SRT
 + P1 calibration split
 -> evaluate editor delta correlation / error reduction
 -> lock policy
 -> blind_test once
```

如果真实 blind gate 证明某语言/mode 下能稳定降低 onset/offset error 且不伤害 line/cut/overlap correctness，才新增独立 calibrated boundary-fusion stage。

## 10. Forced Alignment / ASR

P2 不引入新的模型依赖。Forced Alignment / ASR v2 仍是后续独立 evidence family，应由真实 P1 error breakdown 决定；不能为了“模型更大”替代已经稳定的 Source-to-Mix timing truth。

## 11. Explicit boundaries

当前仍 fail-closed / 不自动化：

- editor shadow score 直接改 final timing；
- Japanese Kanji reading guessing；
- Cantonese pronunciation guessing；
- editor text 改 canonical text；
- same-region cut+overlap joint acoustic reasoning；
- 没有 real blind-test 支持的 confidence/threshold promotion。
