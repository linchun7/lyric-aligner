# ASR Second-Pass Execution — Composite Local Evidence

状态：P6 implementation / evidence-only  
日期：2026-08-18

## 1. 目标

P5 只产生第二遍 accuracy-model 计划；P6 真正执行 P5 选中的 local jobs，并把结果与第一遍 evidence 合成一份可直接交给 P4 的完整 ASR evidence。

数据流：

```text
P3 original alignment plan
+ P3 first-pass ASR evidence
+ P5 second-pass plan
        ↓
P6 execute selected local jobs only
        ↓
composite ASR evidence
  - unselected jobs: retain first-pass evidence
  - selected jobs: replace with second-pass evidence
        ↓
P4 evidence fusion
```

P6 不改 canonical lyric、不改 Source-to-Mix、不改 canonical timeline、不直接改 FINAL.srt。

## 2. 最重要的空选择安全规则

历史 P3 CLI 的手工 `--job-id` 语义是：没有提供任何 ID 时表示不筛选，即可能执行所有计划中的 ASR jobs。

P6 **禁止继承这个语义**。

P5 second-pass plan：

```json
{
  "selected_job_ids": [],
  "jobs": []
}
```

在 P6 中必须解释为：

```text
execute exactly zero second-pass jobs
model_loaded_second_pass = false
second_pass_executed_job_count = 0
```

绝不能解释成“执行全部 original jobs”。

实现上 P6 显式构造：

```text
execution_plan.jobs = selected original jobs
```

因此空选择得到 `execution_plan.jobs=[]`，底层 executor 会在 model factory/import 前返回。

## 3. Exact-window validation

P6 不信任 P5 plan 里的时间窗可以任意变化。

每个 second-pass selected job 必须在 original P3 `mix_asr` plan 中存在，且以下字段逐项相等：

```text
occurrence_id
track_id
canonical_line_index
language_profile
mix_window_ms
source_window_ms
canonical_text_sha256
```

任一字段变化都 fail-closed。

P6 从 original plan 构造实际执行 plan，而不是直接执行 P5 job object，所以 P5 不能扩大窗口。

## 4. Model identity

必须满足：

```text
first_pass_evidence.config.model_id exists
P5 second_pass_model_id exists
P6 executor --model-id == P5 second_pass_model_id
P6 second model != first-pass model
```

这样 runtime 不会把错误模型或同一模型重复执行静默当成 accuracy pass。

## 5. Composite semantics

最终 payload：

```text
backend = faster_whisper
mode = composite_second_pass_evidence
policy_calibrated = false
scope_policy = reuse_exact_first_pass_local_windows
```

每个结果 job：

```text
evidence_pass = first | second
evidence_model_id = actual model ID
```

规则：

- second-pass selected 且执行成功：second result 替换同 job 的 first result；
- 未 selected 且 first result 存在：保留 first result；
- original plan 中没有 evidence 的 job 不伪造一条空结果；
- output 顺序遵循 original P3 planner mix-ASR job 顺序。

统计：

```text
first_pass_input_job_count
first_pass_retained_job_count
second_pass_selected_job_count
second_pass_executed_job_count
job_count
model_loaded_second_pass
```

## 6. Privacy

Composite privacy 由 **P6 本次执行配置**决定，而不是继承第一遍的隐私等级。

默认 `include_private_text=false` 时，即使 first-pass evidence 以前是 private-text opt-in，P6 也必须移除 retained rows 中：

```text
observed_text
segments[].text
segments[].words[].text
```

Second-pass executor 同样不输出 raw text。

只有 P6 本次显式 `--include-private-text` 才允许 composite 保存 raw ASR text。

Canonical lyric raw text从不写入 composite。

## 7. Artifact / lineage

CLI：

```text
scripts/v4_execute_asr_second_pass.py
```

Output artifact 为了兼容 P4，继续使用：

```text
stage = asr_evidence_local
role  = asr_evidence
```

但 normalized config / evidence 明确标记：

```text
mode = composite_second_pass_evidence
```

Upstream 必须包含：

```text
original alignment plan artifact
first-pass ASR artifact
P5 second-pass plan artifact
source run artifact
exact canonical timeline artifacts
```

Payload 绑定：

```text
source_plan_artifact_id
source_first_pass_artifact_id
source_second_pass_plan_artifact_id
source_run_artifact_id
mix_audio_sha256
selected_job_ids
```

P6 还验证 P5 artifact 必须绑定 exact original plan + first-pass artifact。

## 8. P4 compatibility

P4 当前 ASR family 读取：

```text
backend == faster_whisper
jobs[]
canonical_line_index
segments
canonical_text_support_score
language_probability
```

P6 composite 保留这些字段，因此可以直接替代第一遍 ASR evidence 作为 `v4_fuse_evidence.py --asr-evidence` 输入，无需另建一套 fusion stage。

`evidence_pass/evidence_model_id` 作为额外审计字段保留。

## 9. CI 能验证什么

Synthetic/fake-model CI 可以真实验证：

- selected second-pass job 只执行 original exact clip；
- unselected first-pass result retained；
- selected first-pass result replaced；
- empty selected list = zero execution + no model loading；
- changed P5 window fail-closed；
- executor model mismatch fail；
- selected IDs/jobs mismatch fail；
- retained private text默认被剥离；
- artifact/run/plan/first-pass/P5 lineage；
- empty-selection CLI E2E 不需要真实 Whisper model。

## 10. CI 不能证明什么

当前公共 GitHub Actions 仍不会下载并运行真实 accuracy-model，也没有用户授权 real-song/reference truth，因此不能声称：

- second-pass large model 已在 Actions 上真实执行成功；
- large-v3 相对 turbo 的真实歌声提升；
- P5 routing threshold 最优；
- P6 composite 可以自动改变 final timing 或 release decision。

真正 selected-job 的模型执行必须在有模型/网络/缓存/硬件条件的 private/local runtime 中运行，并保留 artifact lineage；真实收益仍需 private calibration + blind_test。
