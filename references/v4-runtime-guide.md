# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 reconstruction/review/overlap/cut/combined/render/release 与 strict calibration/blind framework。P1.1 新增 private dataset scaffold/readiness，目标是让真实数据准备可执行、可检查，但绝不生成假字幕、假 QA 或假准确率。

## 1. 生产重建主链

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

需要 review 时：

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Materialization：

```powershell
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
```

Final：

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Same-region cut+overlap、partial-line ambiguity、证据不足 mapping 仍 fail-closed。

## 2. 新建真实 private dataset 骨架

推荐目录：

```text
private/datasets/<dataset-name>/
```

运行：

```powershell
python scripts/v4_dataset_readiness.py scaffold `
  --out-dir "private/datasets/<dataset-name>" `
  --dataset "opaque-dataset-id" `
  --dataset-revision "2026-08-r1" `
  --candidate-id baseline `
  --calibration-cases 6 `
  --blind-cases 6
```

生成：

```text
baseline.dataset.json
calibration-policy.template.json
blind-policy.template.json
READINESS.json
reference/
predictions/baseline/
```

**不会生成：**

```text
reference/*.srt
predictions/**/*.srt
predictions/**/*.qa.json
真实 cut/overlap 标注
任何 accuracy metric
```

初始 `READINESS.json` 应明确 references/evaluation 尚未 ready；这是正常状态。

## 3. 填写真实 reference / truth

`baseline.dataset.json` 使用 strict schema `1.1`。

每个 case 至少检查/填写：

```json
{
  "id": "calibration-0001",
  "source_group": "sg-calibration-0001",
  "split": "calibration",
  "language": "zh",
  "reference_srt": "reference/calibration-0001.srt",
  "predicted_srt": "predictions/baseline/calibration-0001.srt",
  "qa_json": "predictions/baseline/calibration-0001.qa.json",
  "audio_duration_seconds": 90,
  "expected_cuts": [],
  "predicted_cuts": [],
  "expected_overlaps": [],
  "predicted_overlaps": [],
  "expected_occurrences": []
}
```

注意：

- `id/source_group/dataset` 用 opaque 值，不写曲名/艺人；
- 同一 source/version family 的 clips 必须共享 source_group；
- 同一 source_group 不能跨 train/calibration/blind_test；
- `language` 应按真实 canonical language/profile 标注；
- expected cut/overlap 是人工 truth，不从模型输出复制；
- `audio_duration_seconds=0` 只是 scaffold 占位，真实评估前应补真实值。

## 4. 检查数据准备状态

### 只检查 metadata

```powershell
python scripts/v4_dataset_readiness.py check `
  --dataset "private/datasets/<name>/baseline.dataset.json" `
  --require metadata
```

### reference 是否齐全

```powershell
python scripts/v4_dataset_readiness.py check `
  --dataset "private/datasets/<name>/baseline.dataset.json" `
  --split calibration `
  --require references
```

### prediction + QA 是否齐全

```powershell
python scripts/v4_dataset_readiness.py check `
  --dataset "private/datasets/<name>/baseline.dataset.json" `
  --split calibration `
  --require predictions
```

### 是否真正可进入 P1 evaluate

```powershell
python scripts/v4_dataset_readiness.py check `
  --dataset "private/datasets/<name>/baseline.dataset.json" `
  --split calibration `
  --require evaluation `
  --out "output/evaluation/baseline.calibration.readiness.json"
```

`evaluation` readiness 同时要求：

- reference files 全部存在；
- prediction + QA 全部存在；
- QA JSON 有 algorithm/profile runtime identity；
- selected split runtime identity 唯一一致。

返回码：ready=0；不 ready=2。缺失只报告 opaque case IDs，不输出歌词/绝对路径。

## 5. 派生 candidate manifest

不要手工复制一份 baseline manifest 后逐行改 prediction path。

使用：

```powershell
python scripts/v4_dataset_readiness.py clone-candidate `
  --source "private/datasets/<name>/baseline.dataset.json" `
  --candidate-id candidate-a `
  --out "private/datasets/<name>/candidate-a.dataset.json"
```

它保持 ground truth 不变，只把：

```text
predicted_srt -> predictions/candidate-a/<case>.srt
qa_json       -> predictions/candidate-a/<case>.qa.json
```

并清空 inherited `predicted_cuts/predicted_overlaps`，防止把 baseline 预测冒充 candidate 预测。

## 6. Calibration strict workflow

当 readiness 通过后：

```powershell
python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/baseline.dataset.json" `
  --split calibration `
  --candidate-id baseline `
  --candidate-revision "<baseline-commit/build>" `
  --out "output/evaluation/baseline.calibration.json"

python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/candidate-a.dataset.json" `
  --split calibration `
  --candidate-id candidate-a `
  --candidate-revision "<candidate-commit/build>" `
  --out "output/evaluation/candidate-a.calibration.json"
```

然后 review `calibration-policy.template.json`，复制/改名为正式 versioned policy 后执行：

```powershell
python scripts/v4_calibration_workflow.py select `
  --baseline "output/evaluation/baseline.calibration.json" `
  --candidate "output/evaluation/candidate-a.calibration.json" `
  --policy "private/datasets/<name>/calibration-policy.json" `
  --out "output/evaluation/selection.json"
```

**模板 policy 不是经过真实数据校准的 production threshold。** 不应因为名字叫 template 就直接当最终门槛长期使用。

## 7. Blind-test

Selection artifact 生成之后才准备/读取 blind predictions。

```powershell
python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/baseline.dataset.json" `
  --split blind_test `
  --candidate-id baseline `
  --candidate-revision "<exact-baseline-revision>" `
  --out "output/evaluation/baseline.blind.json"

python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/candidate-a.dataset.json" `
  --split blind_test `
  --candidate-id candidate-a `
  --candidate-revision "<exact-selected-revision>" `
  --out "output/evaluation/candidate-a.blind.json"

python scripts/v4_calibration_workflow.py blind `
  --baseline "output/evaluation/baseline.blind.json" `
  --candidate "output/evaluation/candidate-a.blind.json" `
  --selection "output/evaluation/selection.json" `
  --policy "private/datasets/<name>/blind-policy.json" `
  --out "output/evaluation/blind-gate.json"
```

Blind 会锁 baseline + selected candidate revision/runtime identity；不能看完 blind 后悄悄替换代码/profile。

## 8. GitHub Actions 能做 / 不能做

### 能做

- scaffold/readiness synthetic E2E；
- split/source_group isolation；
- candidate clone truth-preservation；
- QA runtime identity consistency；
- P1 strict selection/blind lock；
- Python 3.10/3.12/3.14 regression；
- ASR dependency environment；
- docs/privacy/environment/diff-check。

### 当前明确不能做

公共 GitHub runner 没有用户授权的 private real-song 音频/reference truth，因此不能真实生成：

- 各语言真实字幕准确率；
- real boundary MAE/P95；
- real cut/overlap P/R；
- production runtime/review density；
- real blind improvement percentage。

这些只能在真实 private dataset 被提供并实际跑过后汇报；不得用 synthetic tests 代替。

## 9. 下一步

P1.1 完成后并行做 P2 Editor Evidence + LanguageSpan shadow artifact。默认不改 canonical text、不把 editor timing 变成真源；自动 boundary refinement 要等 real calibration 再打开。Forced Alignment/ASR v2 根据真实 error breakdown 决定。
