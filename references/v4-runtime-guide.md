# Lyric Aligner v4 生产运行手册

更新：2026-08-19  
主线算法版本：`4.0.0a9`

> 真实生产 workload 与产品设计基线见 `references/production-requirements.md`；Smart / Pro v1.1 设计细节见 `references/smart-pro-v1-1.md`。

## 1. 四档模式

```text
Standard -> Smart -> Pro -> Max
```

- **Standard**：只修文字，不读 audio，不改 timing。
- **Smart**：默认主力。0 audio；利用 timed LRC/QRC、逐字 timing、剪映多数可信 cue、DAW/BPM rate，修少量有充分证据的 timing outlier。
- **Pro**：只处理 Smart unresolved 的局部 audio region；当前执行 local acoustic / bounded ASR / external forced alignment evidence，仍不自动写 timing。
- **Max**：整体时间轴不可信、复杂 cut/overlap/reorder 或 Pro 无法收敛时才进入完整 Full V4。

韩文/日文不是自动 Max 条件。有规范 timed lyrics 且 editor/canonical 能稳定配对时仍先 Smart。

## 2. Standard

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --out "output/<任务>/<任务>_TEXT_REPAIRED.srt" `
  --report "output/<任务>/<任务>_TEXT_REPAIR.json"
```

Text Repair V2.1 冻结 cue count/number/start/end，canonical 是最终文字/顺序 truth，production `--auto-threshold >= 0.72`。

## 3. Smart v1.2.9

### 3.1 基本调用

```powershell
python scripts/v4_smart_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --output-srt "output/<任务>/<任务>_SMART.srt" `
  --report "output/<任务>/<任务>_SMART.json"
```

Smart 要求 timestamped canonical。Enhanced LRC / QRC 的逐字/逐词 timestamp 会自动利用。

**v1.1.1 写文件前会检查所有路径碰撞。** `output-srt` 或 `report` 不得与 source SRT、任一 canonical lyric 或彼此同路径；发现碰撞直接 fail closed，不会覆盖原件。

### 3.2 BPM / exact stretch

已知目标 140 BPM：

```powershell
--target-bpm 140 `
--source-bpm "01.lrc=128" `
--source-bpm "02.lrc=132"
```

内部仍记录：

```text
rate_prior = target_bpm / source_bpm
```

但从 v1.1.1 起，`bpm_derived` 是 **soft plausibility**，不再像 DAW 精确倍率一样固定模型 rate。若 A anchors 能稳定估计 rate，则以 A anchors 为主；BPM 与其差异过大时阻止自动 mutation，并进入 review。

若 DAW/Cubase 有真实 stretch ratio，优先：

```powershell
--rate-prior "01.lrc=1.09375"
```

`exact_daw` 仍可作为 hard prior。

report 现在区分：

```text
models[].rate_provenance
models[].rate_prior_provenance
models[].rate_prior_value
models[].bpm_prior_relative_error
models[].bpm_prior_compatible
```

### 3.3 Smart ready / overlap 安全

`ready` 表示 timing 已被 Smart 实际验证/安全修复；以下会 `review` 并交给 Pro：

- timing model 不 ready；
- 无唯一 timed-canonical mapping；
- C-grade identity。

B-grade 不能建立 timing model，只能由 already-ready A-anchor model 二次确认。

原 v1 的 leave-one-out、左右 anchor、edge hard-rate prior、最大 shift 等限制继续有效。v1.1.1 对自动 repair 做两层 overlap guard：

1. 单条 proposal 不得制造原 SRT 中不存在的新 overlap；
2. 所有 repair 组合成最终 proposal timeline 后再次检查，相邻 cue 的 overlap 不得比编辑器原值更大。

因此两条 cue 即使分别检查安全，但组合后互相冲突，也会统一降级 review。

Smart report schema 仍是 `smart-1.1`，current policy 为 `smart-validation-policy-2026-08-22-v1.2.9`；产品字段：

```text
status
policy_id
pro_escalation_required
timing_validated_preserve_count
timing_repair_count
timing_validated_count
timing_suspected_count
timing_suspected_actionable_count
timing_suspected_within_display_tolerance_count
timing_unvalidated_count
manual_timing_review_candidate_count
timing_high_value_pro_candidate_count
timing_actionable_strong_model_count
timing_actionable_weak_or_unknown_model_count
text_cross_script_vocalization_recovery_count
timing_review_count  # legacy unresolved total，不是人工队列
```

`manual_timing_review_candidate_count` 是所有明确 actionable timing suspicion；`timing_high_value_pro_candidate_count` 是 Pro 预算优先级子集，不是 vocal-onset 错误概率。只要 actionable count 非零，`product_status` 必须为 `review_required`。跨文字拟声恢复要求前一 resolved canonical occurrence 证明 exact adjacency，并保持一对一 cue ownership。

角色/metadata 过滤发生在 shared canonical parser 建立 canonical lines/ordinal 之前，会影响所有下游模式。裸中文短行默认保留；明确角色词、多人分隔名单和显式角色括号直接过滤。v1.2.9 允许同文件多人 cast 证明 exact bare member；cast 外裸标签只有在强 ensemble grammar、重复出现且每次两秒内紧接 lexical 行时才过滤。“夏天：”“白天：”“向前：”回归仍必须保留。

## 4. Pro v1.2.4

### 4.1 先只计划，不读 audio

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --source-language "01.lrc=zh" `
  --source-language "02.lrc=zh" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json"
```

Pro v1.2.4 必须读取**当前 Smart v1.2.9 policy** 产出的 `smart-1.1` report。旧 Smart report 即使 schema 相同，只要 policy id 不是当前版本，也会要求重新跑 Smart。

reason-aware routing：

```text
timing review -> local source<->mix acoustic first
text/identity review -> bounded ASR + word timestamps
no word timing + source-side identity needs help -> forced alignment
unmapped review -> bounded ASR only
```

已有逐字 Enhanced LRC/QRC 时不重复请求 source forced alignment。Enhanced LRC 最后 token 合法的 `end_ms=None` 已兼容，不再导致计划阶段报错。

### 4.2 Region 合并与 source window

**只有 acoustic jobs 才参与 acoustic region 合并。** ASR-only jobs 保持各自 mix window，不会把相邻 acoustic decode/feature 区域无意义扩大。

计划会记录：

```text
job_count
primary_job_count
boundary_competitor_job_count
boundary_competitor_omitted_due_to_max_jobs
region_count
acoustic_region_count
planned_mix_audio_ms_unmerged / merged
planned_acoustic_mix_audio_ms_unmerged / merged
```

`--max-jobs` 在当前 Pro v1.2.4 中约束 **primary unresolved cues**。价值顺序为：actionable timing suspicion（strong local model 先于 weak/unknown，同层再按 `|Smart shift|` 降序）、text review、显示容差内 timing suspicion、纯 timing-unvalidated。Shadow competitor 不消耗 primary budget。

source window 仍优先使用逐字 timing；否则利用下一 canonical onset；最后一行使用 bounded fallback。除此之外，任何 acoustic source window 都必须满足：

```text
source_window_duration
>= mix_query_duration × max_candidate_slope + frame_margin
```

这避免 query 需要的 source span 比窗口本身更长而出现“无候选”的假失败。

### 4.3 局部 source↔mix 声学验证

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json" `
  --mix-audio "private/<任务>/input/mix.wav" `
  --source-audio "01.lrc=private/<任务>/source/01.wav" `
  --source-audio "02.lrc=private/<任务>/source/02.wav" `
  --acoustic-out "output/<任务>/<任务>_PRO_ACOUSTIC.json"
```

可靠 Smart rate 存在时仍使用 narrow slope search。歌曲首/尾 timing review 可能增加前/后歌曲 shadow competitor：

```text
shadow_evidence_only = true
boundary_role = previous_source | next_source
```

这是 join/crossfade 双源判断，不是自动 timing authority。

v1.1.1 acoustic 输出只 hash 当前 acoustic plan 真正使用到的 source audio；未被本次 Pro 任务使用的原曲不做额外整文件 I/O。

Acoustic schema v1.3 同时输出 `acoustic_shift_ms = predicted - editor`、`local_match_gate_passed`、`slope_search_min/max`、`slope_search_boundary_hit` 和 `timing_fusion_evidence_eligible`。`local_match_gate_passed` 只表示 bounded retrieval 成功；最佳 slope 命中或接近搜索边界时，证据保留为 diagnostic/unresolved，不能 support/rebut Smart，也不能独立声明 timing anomaly。不要通过降低 score/margin 门槛规避这个边界。

### 4.4 Pro decision fusion

在同一 invocation 已执行的 evidence 上生成 fail-closed 产品裁决：

```powershell
--decision-out "output/<任务>/<任务>_PRO_DECISIONS.json"
```

decision artifact 分离 text/timing 两轴，列出 `high_priority_manual_review_count` 与精确 cue/start positions。Smart 与合格 local acoustic 都消费 canonical/LRC timeline，因此二者同向属于相关证据，不能冒充独立 vocal onset；仅此类 timing support/conflict 降为 medium。只有同时解决一对一 canonical text occurrence 等额外高价值问题时才进入 high。Source-side forced alignment 仍是 auxiliary evidence，未严格形成独立 mix vocal-onset 映射前，`independent_vocal_onset_evidence_used=false`。当前固定 `automatic_timing_change_allowed=false`、`automatic_text_change_allowed=false`、`timing_mutation_performed=false`，不生成 `*_PRO.srt`。

### 4.5 局部 Whisper

```powershell
--mix-audio "private/<任务>/input/mix.wav" `
--asr-model-id "<faster-whisper-model>" `
--asr-out "output/<任务>/<任务>_PRO_ASR.json"
```

语言按当前 canonical line 路由：中文歌纯英文 rap -> `en`；code-switch -> auto；韩/日 pure line -> `ko/ja`。

### 4.6 External forced alignment

```powershell
python scripts/v4_pro_selective.py `
  --smart-report "output/<任务>/<任务>_SMART.json" `
  --smart-srt "output/<任务>/<任务>_SMART.srt" `
  --canonical-lyrics "lyrics/01.lrc" "lyrics/02.lrc" `
  --plan-out "output/<任务>/<任务>_PRO_PLAN.json" `
  --source-audio "01.lrc=private/<任务>/source/01.wav" `
  --source-audio "02.lrc=private/<任务>/source/02.wav" `
  --forced-out "output/<任务>/<任务>_PRO_FORCED.json" `
  --forced-command "<external-aligner-command>" `
  --forced-backend-id "<backend>" `
  --forced-backend-version "<version>" `
  --forced-model-id "<model>" `
  --forced-model-revision "<revision>"
```

Forced alignment 仍是 auxiliary source-side evidence；canonical lyric 仍拥有最终文字/顺序 authority。v1.1.1 只为实际请求 forced alignment 的 source 建 binding/hash。

### 4.7 Pro artifact 路径安全

在写 `plan-out / acoustic-out / asr-out / forced-out` 前，Pro 会统一检查它们不得覆盖：

- Smart report；
- Smart SRT；
- canonical lyrics；
- mix audio；
- 任一 source audio；
- 其他 Pro output artifact。

所有碰撞均 fail closed。

## 5. 少量同歌多速度

Smart 继续 `Affine first`。同一首歌出现少量多 rate 时，先表现为 unstable/conflict 并升级 Pro；只有真实 private 样本证明有必要才增加 evidence-triggered piecewise。`rate change != cut`。

## 6. Max

Smart/Pro 解决不了、或整体 timeline 本来就不可信时再运行完整 Source-to-Mix 主链。Max 不再是普通 timing 修复默认入口。

## 7. Legacy Partial Timeline Repair

旧 P1–P5 formal proposal/calibration chain 继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro 不借用 P9/P4 authority，也不会反向提升旧 chain。

## 8. 推荐日常生产顺序

```text
1. canonical lyrics + Jianying SRT
2. timing 完全可信 -> Standard
3. 大部分可信、少量可疑 -> Smart
4. Smart unresolved -> Pro plan
5. Pro 只处理 bounded regions，按原因选择 acoustic / ASR / forced
6. Pro evidence 当前仍不自动写 timing
7. broad untrusted / complex structure / Pro 无法收敛 -> Max
8. 永远保留原输入，写独立 outputs/artifacts；路径碰撞必须 fail closed
```

## 9. 验证边界

Public CI 能验证 deterministic policy、最终 overlap guard、soft BPM semantics、Enhanced LRC open token、stale Smart rejection、reason routing、acoustic-only region reuse、source-window minimum、path collision、forced orchestration contract 与 Python/ASR compatibility。真实歌曲 false-auto / false acoustic match 仍必须通过 private calibration + independent blind；通过前不开放 Pro 自动 timing write-back。

### 2026-08-21 CLI / Pro budget maintenance

`python scripts/v4_smart_repair.py --help` 与 `python scripts/v4_pro_selective.py --help` 现在会自行把 repository root 加入 import path，正式文档中的直接入口不要求调用者额外设置 `PYTHONPATH`。

当前 Pro v1.2.4 的 `--max-jobs` 是 **primary unresolved-cue budget**。Shadow boundary competitors 只附着于已经选中的 primary，属于 additive evidence；`plan.config.max_jobs` 对外报告调用者请求的 primary budget，内部完整 candidate-pool 扩池不是公开预算语义。
