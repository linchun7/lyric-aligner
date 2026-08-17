# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成重建、review、cut/overlap/combined、render/release、strict calibration/blind 和 private dataset scaffold/readiness。P2 新增 editor/Jianying **shadow evidence**；未经真实 calibration，它不会修改 canonical text 或最终字幕时间。

## 1. Production reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

Review/materialization：

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
```

Final：

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Canonical lyric 仍是文字真源；Source-to-Mix 仍是主要时间真源。

## 2. Private dataset / calibration

数据骨架与 readiness：

```powershell
python scripts/v4_dataset_readiness.py scaffold ...
python scripts/v4_dataset_readiness.py clone-candidate ...
python scripts/v4_dataset_readiness.py check ...
```

Strict calibration/blind：

```powershell
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

没有授权 real-song reference truth 时，不得把 synthetic CI 指标描述为真实准确率。

## 3. P2 Editor Evidence Shadow

在已有 effective v4 run 上执行：

```powershell
python scripts/v4_editor_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --out "output/<任务>/v4/editor/editor_evidence.json" `
  --artifact-out "output/<任务>/v4/editor/editor_evidence.artifact.json" `
  --git-commit "<commit>"
```

如果 run 已经过 review/overlap/cut/combined materialization，把 `--run/--run-artifact` 指向**实际准备作为当前有效时间线的 run**。支持：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

## 4. P2 的权威边界

输出 artifact：

```text
editor_evidence_shadow / editor_evidence
```

固定：

```text
mode = shadow_only
policy_calibrated = false
automatic_timing_change_allowed = false
```

因此 P2 当前：

- 不修改 canonical lyric；
- 不修改 Source-to-Mix mapping；
- 不修改 canonical timeline；
- 不修改 run status；
- 不修改 FINAL.srt；
- 不允许因为 editor cue 看起来更近就覆盖 source timing。

`suggested_onset_delta_ms / suggested_offset_delta_ms` 只是 calibration/review 数据。

## 5. LanguageSpan 路由

```text
en  -> direct_text
zh  -> direct_text
ko  -> phonetic_hint
ja  -> phonetic_hint
yue -> timing_hint
unknown/generic -> timing_hint
mixed -> per-span
```

具体：

- EN/ZH：可记录 normalized direct-text support；
- KO：Hangul 可转 conservative Romanization，与 editor Latin phonetic output 比较；仍是弱 evidence；
- JA Kana：可 conservative Romanize；
- JA Kanji：没有 vetted reading backend 时不猜读音，只保留 timing；
- YUE：editor text weight=0，没有 vetted Jyutping backend 时禁止用普通话拼音冒充；
- mixed：按 canonical span 路由，不能把整行当一个语言。

## 6. Evidence JSON

每条 canonical line 只存：

```text
canonical_line_index
canonical_text_sha256
canonical_mix_start/end
span language/script/mode/text_sha256
editor cue number/start/end/text_sha256
timing_support_score
direct_text_support_score
phonetic_support_score
text_support_score
effective weights
rank_score_uncalibrated
best-vs-second margin
suggested onset/offset delta
```

不输出 raw canonical/editor text。

Bootstrap weights 仅用于 shadow candidate 排序，不是经过真实数据验证的置信度门槛。

## 7. Lineage / tamper protection

P2 验证：

- task manifest 与 input hashes；
- task 中 `source_srt` SHA；
- source run artifact；
- source run algorithm/task identity；
- 每个 canonical timeline artifact；
- timeline artifact 必须在 run upstream；
- occurrence/track identity。

如果 task manifest 创建后 `source_srt` 被修改，`verify_manifest_inputs` 必须先失败，不能静默读新字幕。

## 8. 如何用 P1 校准 P2

第一阶段只收集 shadow evidence：

1. 对 baseline v4 run 生成 editor evidence；
2. 保留每行 candidate support / margin / delta；
3. 用 private reference SRT 计算真实 onset/offset error；
4. 分语言/模式检查：editor delta 是否在不降低 line/cut/overlap correctness 前提下有稳定改善；
5. 只有 calibration split 选定 policy 后，再对 blind_test 做一次锁定评估。

在完成这一步以前，不应该新增 `--apply-editor-timing` 或类似开关。

## 9. GitHub Actions 能做 / 不能做

CI 可以验证：

- EN/ZH/KO/JA/YUE/mixed synthetic routing；
- Korean/Kana phonetic helper；
- Kanji/Yue no-guess；
- privacy；
- task/run/timeline lineage；
- source_srt tamper；
- no automatic timing mutation；
- 既有 P0/P1/P1.1 regression。

CI 当前不能证明 editor shadow policy 在真实歌曲上提升准确率，也不能校准真实权重/自动应用阈值。

## 10. 后续

P2 shadow 全绿后优先使用真实 private dataset 做 error correlation。若有稳定收益，下一 milestone 才做 calibrated Editor Boundary Fusion。Forced Alignment/ASR v2 仍由真实剩余误差决定；same-region cut+overlap joint model 继续 fail-closed。
