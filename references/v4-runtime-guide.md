# Lyric Aligner v4 生产运行手册

更新：2026-09-03
主线算法版本：`4.0.0a11`

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

## 3. Smart v1.2.10

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

Smart report schema 仍是 `smart-1.1`，current policy 为 `smart-validation-policy-2026-08-22-v1.2.10`；产品字段：

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

v1.2.10 通过版本隔离开关启用 split-line guard，历史 policy 的显式入口保持可复现。它不再把一个 canonical line onset 重复授予映射到该行的多个 editor cues。span 首 cue 可继续使用 line onset；内部 cue 只有在合并后的 editor 文本与 canonical token stream 精确一致、且内部边界正好落在严格后移、仍处于该 canonical line 内的可靠 token boundary 时，才使用对应 token onset。否则输出 `segmentation_internal_boundary_unvalidated` 且不生成 timing proposal。

## 4. Pro v1.2.6

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

Pro v1.2.6 必须读取**当前 Smart v1.2.10 policy** 产出的 `smart-1.1` report。旧 Smart report 即使 schema 相同，只要 policy id 不是当前版本，也会要求重新跑 Smart。

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
asr_language_hint_counts
asr_force_auto_detect_count
```

`--max-jobs` 在当前 Pro v1.2.6 中约束 **primary unresolved cues**。Smart 的 `timing_high_value_pro_candidate_positions` 先获得预算优先级；其后再按 actionable/text、strong-vs-weak local model 与 `|Smart shift|` 排序。该优先级不改变完整 manual queue。Shadow competitor 不消耗 primary budget。

`asr_language_hint=auto` 表示“没有具体 override”。当前 planner 只有在 canonical-local language 与显式 `zh/en/ko/ja` source language 一致时才固定 ASR；局部 code-switch 与整首语言冲突、mixed/unknown、或 source language 本身为 auto 时写入 `asr_force_auto_detect=true` 并继续 backend auto-detect，不从 Han/Latin script 静态猜语言。原因是 Pro 的宽 timing-search window 可能包含相邻歌词，不能把单行 script 误当成整段音频语言。

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

语言路由以 bounded-window 安全为准：local line 与已知 source language 一致才固定 `zh/en/ko/ja`；中文歌纯英文 rap、其他跨 source-language 的 code-switch、mixed/unknown -> `asr_force_auto_detect=true`。

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

正式 `scripts/v4_run.py` 会按职责调用 coarse CLI：primary occurrence 使用默认 `--purpose primary_timewarp`；shared-boundary 双侧 activity probe 使用 `--purpose transition_activity`。后者只产出完整 retrieval windows，不产出 Source-to-Mix mapping；不要把 `NOT_REQUESTED` 的 transition coarse artifact 手工接到 Fine 或 timeline projection。purpose 已进入 artifact fingerprint，恢复运行时不得跨 purpose 复用。

Max orchestration 会另外记录物理 `mix_duration` 与保守 `content_end`。`content_end` 只会在音频尾部存在至少 30 秒**解码后逐样本精确为 0**的 digital-zero run 时缩短；普通淡出、近静音、底噪或弱信号不会被当成空白。该边界只限制最后一个 occurrence 的 production window/terminal clamp，物理文件时长仍保留作 provenance。这样可避免导出文件尾部的大段数字静音把最后一首搜索区间错误扩到容器末尾，同时不引入主观 silence threshold。

当独立、已验证的同曲 reference audio 能证明 Max primary mapping 在局部重复段失真时，可在 overlap/cut review 已完全闭合之后使用 `scripts/v4_retime_reference.py` 做窄 reference retime。普通平移/插入使用单调 `segments`；reference 本身存在明确删除/拼接时必须使用 `retained_segments` 明示每个保留 reference interval 与 target start，删除区内 canonical cue 会被丢弃，跨切点 cue 只裁剪到实际存活音频。一个 cue 若在两个保留段都存活会 fail closed，不自动猜分段。reference task fingerprint、canonical selection、reference/target audio SHA、source resolved-run artifact 与 retime spec 都必须进入 lineage。`4.0.0a10` 起，无 confirmed overlap 的任务只要 `review_resolution` 已完全闭合（`ready_for_render`、issues 为空、非 legacy fallback），即可直接作为 reference-retime source；存在 overlap 时仍必须先完成原有 `overlap_recomposition`。`4.0.0a11` 起 renderer 也按 reference-retime metadata 中已验证的 `source_run_stage` 继续对应 materialization 校验，不再把 direct-review reference-retime 错当成 overlap run。两条路径都显式绑定 source review artifact，不允许绕过 review authority。不要用搜索半径不足、最佳点贴搜索边界或无强相关锚点的窄 lag scan 推断全曲平移；这类结果只能作为 diagnostic，必须扩大搜索范围或直接建立结构证据。

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
8. Max review/cut/overlap 闭合后先生成 canonical evaluation render
9. 通过 Editor-Cue Reconciliation 获得可证明的 production segmentation authority；不满足 gate 则继续 review，不手改 artifact
10. 如需平台展示修订，再运行 task-bound display policy
11. 运行 `v4_audit_final.py` 做只读 final geometry/presentation QA
12. 最后运行 `v4_validate_release.py`；只有 release manifest ready 才作为正式交付
13. 永远保留原输入，写独立 outputs/artifacts；路径碰撞必须 fail closed
```

## 9. 验证边界

Public CI 能验证 deterministic policy、最终 overlap guard、soft BPM semantics、Enhanced LRC open token、stale Smart rejection、reason routing、acoustic-only region reuse、source-window minimum、path collision、forced orchestration contract 与 Python/ASR compatibility。真实歌曲 false-auto / false acoustic match 仍必须通过 private calibration + independent blind；通过前不开放 Pro 自动 timing write-back。

### 2026-08-21 CLI / Pro budget maintenance

`python scripts/v4_smart_repair.py --help` 与 `python scripts/v4_pro_selective.py --help` 现在会自行把 repository root 加入 import path，正式文档中的直接入口不要求调用者额外设置 `PYTHONPATH`。

当前 Pro v1.2.6 的 `--max-jobs` 是 **primary unresolved-cue budget**。Shadow boundary competitors 只附着于已经选中的 primary，属于 additive evidence；`plan.config.max_jobs` 对外报告调用者请求的 primary budget，内部完整 candidate-pool 扩池不是公开预算语义。Acoustic schema 1.4 同时记录 slope 与 source-start 搜索边界；任一 optimum 命中/接近边界时都不得参与 timing fusion。

## Max evaluation render vs production release — 2026-08-22 safety contract

`scripts/v4_render.py` currently renders canonical timelines for evaluation/QA only. A successful command can still write `FINAL.srt`, audit CSV and QA JSON, but success no longer means that file is production-release eligible.

Expected current output semantics:

```text
publish_ready = false
segmentation_authority = canonical_line_evaluation_only
release_blocked_reason = editor_cue_reconciliation_required
```

The renderer also fails before writing a normal final cue stream when a timeline reports `projection_coverage.authority_omitted_line_count > 0`; rerun/remap/rebuild that occurrence rather than accepting a subtitle with silently omitted canonical lines.

Running `scripts/v4_validate_release.py` on the current canonical-line evaluation render is expected to fail with a segmentation-authority error. This is intentional. Do not bypass the gate by editing the artifact or relabeling the render. V4 release requires a bound final-render artifact with:

```text
normalized_config.segmentation_authority = editor_reconciled
```

That value must be produced by a validated production materializer that consumes Editor-Cue Reconciliation evidence. The current narrow topology-rebuttal path is `v4_materialize_editor_reconciled.py`; a future preserve-topology materializer is still required for `full_topology_candidate=true`. Resolving transition/cut/overlap review alone does not create this authority.

## 10. Editor-Cue Reconciliation evaluation — 2026-08-23

在已有 Max canonical evaluation render 后运行：

```powershell
python scripts/v4_editor_cue_reconcile.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --evaluation-srt "output/<任务>/FINAL.srt" `
  --report "output/<任务>/FINAL.csv" `
  --qa-json "output/<任务>/FINAL.qa.json" `
  --render-artifact "output/<任务>/FINAL.render.artifact.json" `
  --out "output/<任务>/EDITOR_RECONCILE_EVAL.json" `
  --artifact-out "output/<任务>/EDITOR_RECONCILE_EVAL.artifact.json"
```

输入 `final_render` 必须仍是 #70 contract：

```text
segmentation_authority = canonical_line_evaluation_only
publish_ready = false
release_blocked_reason = editor_cue_reconciliation_required
```

CLI 会从 task manifest 解析并验证 exact `source_srt`，不会接受另一份手工替换的 editor SRT。canonical evaluation SRT / audit / QA 必须与同一个 `final_render` artifact 精确 hash-bound。

输出只用于评估：

```text
stage = editor_cue_reconciliation_evaluation
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

逐 editor cue 结果：

- `resolved`：canonical interval(s) 完整落入唯一 editor cue，且同 cue 内 canonical material 不互相 overlap；
- `still_review`：跨 editor boundary、多个重叠 editor cue ownership ambiguity、或同 editor cue 内 canonical overlap；
- `not_evaluable`：没有 canonical temporal evidence；
- `rebutted`：schema 保留，但首版不会自动产生。

`full_topology_candidate=true` 仍不能直接进入 release；它不是 `editor_reconciled`。对于另一类经 evaluation 明确证明 editor topology 不完整的任务，可使用 `v4_materialize_editor_reconciled.py` 的窄 rebuttal path：必须存在至少一个 `canonical_unassigned.reason=no_editor_temporal_overlap` witness，reconciliation 内部 assigned/unassigned/status 计数必须闭合，而且 canonical audit 的每一行必须来自 `line_lrc / enhanced_lrc / qrc_word_timing` 显式 timing。editor SRT 文件顺序通常必须单调；若存在逆序，只有当**每一个相邻逆序对在时间上完全不重叠**（逆序后的 cue 已在前一个 cue 开始前结束）时，evaluation 才会显式记录 `editor_file_order_recoverable_nonoverlap_reordering=true`，rebuttal materializer 才可接受该可恢复文件顺序。任何时间重叠的逆序仍 fail closed；`full_topology_candidate` 本身仍要求原始 editor file order 单调。普通跨 editor boundary 不能单独触发 rebuttal。

```powershell
python scripts/v4_materialize_editor_reconciled.py `
  --task-manifest <task_manifest.json> `
  --evaluation-srt <canonical_eval.srt> `
  --report <canonical_eval.audit.csv> `
  --qa-json <canonical_eval.qa.json> `
  --render-artifact <canonical_eval.artifact.json> `
  --reconciliation <editor_reconciliation.json> `
  --reconciliation-artifact <editor_reconciliation.artifact.json> `
  --final-srt <FINAL.srt> `
  --final-report <FINAL.audit.csv> `
  --final-qa <FINAL.qa.json> `
  --artifact-out <FINAL.render.artifact.json>
```

成功时 final SRT/audit 与 canonical evaluation 逐字节一致；只有 QA 与新的 `final_render` artifact 获得 production authority。新 artifact 必须记录 source evaluation render、reconciliation artifact、rebuttal witness count 与 timing-format counts，且三层 production authority 均为 `editor_reconciled` / `publish_ready=true`。随后仍必须正常运行 `v4_validate_release.py`；release validator 没有 topology-rebuttal 特例。

### Production display policy（可选，production authority 之后）

已获得 `editor_reconciled` / `publish_ready=true` 的 production render 如需做平台展示处理，可再运行 `scripts/v4_apply_display_policy.py`。该阶段不重新推导歌词时间轴：cue 数量、编号、开始时间、occurrence、track 与 canonical line identity 必须完全保持不变。viewer-facing 文本可以按下面的严格规则改写；结束时间只允许在显式启用 `trim_extreme_unknown_end_v1` 时**缩短**，禁止延长、禁止移动开始时间。

显式模型修订通过 task-bound `display-text-policy-1.0` JSON 提供；每条 override 必须绑定 `occurrence_id + track_id + canonical_line_index + expected_text`，并明确 `confidence=high`、reviewer 与 reason。源 SRT 文本与 `expected_text` 不完全一致、override 未命中或命中不唯一时均 fail closed。模型修订不得回写 canonical lyric truth。

`strong_profanity_v1` 是窄自动打码 profile，只处理明确强脏词，例如 `fuck/fucking -> f*`；`sexy`、`shot`、`bullet`、`kill`、`damn` 等语境相关词不会自动改写，必须由模型/人工语境审查决定。

可选 `timing_policy.mode=trim_extreme_unknown_end_v1` 只解决 line-LRC 没有真实 vocal-end、被 `next_line_start` 被动拉长的极端挂字幕：`source_end_basis` 只能是 `next_line_start`；只有源 duration 达到 policy 阈值时才允许把显示 end 缩到 `start + max_display_hold_ms`，且 `max_display_hold_ms` 必须小于触发阈值。`open_end`、显式 word timing、普通短/中等 duration 均不受该规则影响。输出 audit 必须同时保留 source/display start/end、`canonical_text` / `display_text`、policy identity、reviewer 与 change reasons，并重新计算 final `text_sha256/cue_id`。

```powershell
python scripts/v4_apply_display_policy.py `
  --task-manifest <task_manifest.json> `
  --source-srt <production.srt> `
  --source-report <production.audit.csv> `
  --source-qa <production.qa.json> `
  --source-render-artifact <production.render.artifact.json> `
  --display-policy <display_text_policy.json> `
  --final-srt <DISPLAY_FINAL.srt> `
  --final-report <DISPLAY_FINAL.audit.csv> `
  --final-qa <DISPLAY_FINAL.qa.json> `
  --artifact-out <DISPLAY_FINAL.render.artifact.json>
```

该阶段生成一个新的、仍为 `stage=final_render` 的 hash-bound production artifact，并以上一层 production render 为 upstream。发布时只把**新的 display final-render artifact**交给 `v4_validate_release.py`，因此现有“exactly one final_render”与三层 `editor_reconciled` authority gate 不需要任何例外。

### Final candidate audit（推荐，release 前最后一层只读 QA）

`scripts/v4_audit_final.py` 是 diagnostic-only 检查，不生成 production artifact，也不授予 timing/text/segmentation/release authority。它要求 final SRT 与 audit CSV exact binding、QA 已 publish-ready，并从同 task 的 run/timeline 读取 authoritative occurrence windows、`content_end` 与已确认 overlap regions；`--out` 不能覆盖 task/direct/run 声明的任何输入路径。

它统一报告 cue duration 分布、<500 ms 短 cue、>6 s 长驻留、>=8 s 极端驻留、final file order、occurrence-window containment、content-end 越界，以及 cue overlap。长驻留只作为 presentation warning，不自动判错；跨 occurrence overlap 只有在交集完整落入该 pair 的 confirmed-overlap region 时才允许，同 occurrence overlap 或未确认 cross-track overlap 都是 structural error。命令返回 `0` 表示结构检查通过（可以仍有 warning），返回 `2` 表示发现 structural error。该检查不能替代 `v4_validate_release.py`；推荐顺序是 production/display materialization -> `v4_audit_final.py` -> `v4_validate_release.py`。
