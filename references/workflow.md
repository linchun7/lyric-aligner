# 多语言混剪歌词字幕：当前生产工作流

更新：2026-09-03
当前路径：`Standard -> Smart -> Pro -> Max`
当前 Max：`4.0.0a14`

本文件是当前生产工作流总览。历史 `v3.9` / `redo_karaoke_pipeline.py` 仅保留为兼容、回归与历史实现，不再是新任务默认入口。更细的 Max authority 与 CLI 约束分别见 `v4-runtime-guide.md`、`v4-cli-contract.md`。

## 1. 生产原则

1. canonical lyric 决定最终文字与顺序；ASR 不能改写 canonical truth。
2. canonical LRC line break 不等于 final subtitle cue boundary。
3. Jianying/editor timing 与 segmentation 是强但可推翻的先验。
4. text certainty 与 timing certainty 分开；timing unresolved 不代表要保留已知错误 ASR 文本。
5. 使用最便宜且足够的证据：`Standard -> Smart -> Pro -> Max`。
6. 证据不足时 fail closed 到 review，不降低阈值、不静默回退 legacy。
7. 输入、配置、artifact、review、release 都必须可追溯，不覆盖原件。

## 2. 任务身份：raw inputs 与 semantic config 分层

任务目录建议：

```text
private/<task>/
├─ input/
│  ├─ source.srt
│  ├─ mix.wav
│  ├─ songs.txt
│  ├─ bpm.txt                    # optional
│  ├─ lyrics/
│  ├─ source-audio/              # optional for lower modes; required by Max acoustic path
│  └─ mix_content_extent.json    # optional, QA-proven detached tail only
└─ qa/
   ├─ task_manifest.json
   ├─ v4_run_config.json
   ├─ <task>_manual_overrides.json
   └─ <task>_regression_cases.json
```

公开示例中的统一输出根目录是 `output/任务名/`；文中的 `output/<task>/...` 只是把“任务名”参数化，不代表第二套 output root。

`task_manifest.json` schema 2.0 fingerprint 绑定原始任务输入：source SRT、mix audio、song list、lyrics、可选 BPM/source-audio/content-extent。

`v4_run_config.json` schema `v4-run-config-1.0` 单独绑定 Full V4 可能后补、但会改变 asset resolution 语义的配置：

```text
profile
language_map
middle_cut_map
lyric_role_map
```

两者分层的原因：raw task 可以先稳定下来，语言/同 timestamp lyric-role 等人工语义结论可以稍后生成，但之后必须被显式绑定，不能只靠命令行记忆。

## 3. 初始化

```powershell
python scripts/init_task.py `
  --task "<task>" `
  --source-srt "private/<task>/input/source.srt" `
  --audio "private/<task>/input/mix.wav" `
  --song-list "private/<task>/input/songs.txt" `
  --lyrics-dir "private/<task>/input/lyrics" `
  --source-audio-dir "private/<task>/input/source-audio"
```

如初始化时已有语义配置，可直接加：

```powershell
--profile <profile.json> `
--language-map <language_map.json> `
--middle-cut-map <middle_cut_map.json> `
--lyric-role-map <lyric_role_map.json>
```

旧任务或之后新增配置：

```powershell
python scripts/init_v4_run_config.py `
  --task-manifest "private/<task>/qa/task_manifest.json" `
  --language-map "private/<task>/qa/language_map.json" `
  --lyric-role-map "private/<task>/qa/lyric_role_map.json" `
  --replace
```

已有 config 语义不同但没有 `--replace` 时必须失败。`--replace` 是整份配置替换：未再次指定的语义项会变为 `null`，因此仍需保留的项必须在同一命令中全部给出。

## 4. 模式选择

### 4.1 Standard：只修文字

适合 timing 明确可信、只需要按 canonical 修文本的任务。

```powershell
python scripts/v4_text_repair.py ...
```

契约：

```text
audio_read = false
cue count / number / start / end = frozen
canonical lyric = final text/order truth
```

### 4.2 Smart：普通生产默认

适合大部分 editor timing 正确，少量 cue 可疑，有 timed LRC/QRC/Enhanced LRC 的普通任务。

```powershell
python scripts/v4_smart_repair.py ...
```

当前 Smart v1.2.10。使用 canonical sequence、A-anchor majority、word/token timing、exact DAW 或 soft BPM plausibility。Smart 不因外语自动升级 Max，也不让自身恢复出的 text 反过来制造 primary timing anchor。

### 4.3 Pro：局部音频证据

Smart unresolved 后：

```powershell
python scripts/v4_pro_selective.py ...
```

当前 Pro v1.2.6。只读取 bounded suspicious regions：timing review 优先 local source-to-mix acoustic；text/identity review 再用 bounded ASR/forced evidence。当前仍不自动写 timing。

### 4.4 Max：Full V4 heavy fallback

进入条件：整体 timeline broadly untrusted、复杂 cut/overlap/reorder、重复 source occurrence、或 Smart/Pro 无法安全收敛。

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<task>/qa/task_manifest.json" `
  --out-dir "output/<task>/v4" `
  --git-commit "<current-clean-HEAD>"
```

`4.0.0a14` 起，如果 sibling `qa/v4_run_config.json` 存在，`v4_run.py`、direct optimized 和 direct legacy entrypoint 都会在任何 output mutation 前自动发现、验证并展开其中语义配置。调用者漏掉 `language_map` / `lyric_role_map` 不再悄悄改变运行语义。

配置文件内容被修改、task fingerprint 不匹配、显式 CLI 参数与 config 不一致时全部 fail closed。不存在 run config 的 legacy task 仍兼容原显式 flags。

## 5. Max raw run 的含义

Max raw chain：

```text
TrackAsset resolution
-> production plan
-> primary coarse
-> selective Fine
-> canonical projection
-> shared-boundary transition activity/probe
-> v4_run.json
```

`review_required` 是正常产品状态，不是脚本失败。当前 ordinary lyric timing 已经很强，主要剩余 review 常来自 transition/cut/overlap/structural ambiguity。

`mix_duration` 是物理容器长度；`content_end` 是保守内容边界。a13 可对 QA 已证明 detached export tail 使用 fingerprint-bound `mix_content_extent`，只允许 shorten-only。

## 6. Review 与结构 materialization

Raw issues 必须通过正式 review identity 闭合。根据问题类型：

```text
transition ambiguity -> review decision
confirmed cut        -> cut rebuild / retained-segment evidence
confirmed overlap    -> overlap recomposition
both                 -> combined materialization
```

不能通过手工删除 issue、修改 artifact JSON 或降低 transition/acoustic threshold 获得 ready。

若独立 reference audio 明确证明同曲内部 mapping 需要 retime，可在 review 已全部闭合后使用 `v4_retime_reference.py`。reference-retime 是窄证据路径，不是通用“看着差一点就平移”。

## 7. Evaluation render 与 production authority

`v4_render.py` 的 canonical-line 输出首先是 evaluation：

```text
publish_ready = false
segmentation_authority = canonical_line_evaluation_only
```

原因：canonical line break 不是 final display segmentation authority。

然后运行 Editor-Cue Reconciliation。若 editor topology 可保持或被独立证据证明不完整，再由受支持 production materializer 获得：

```text
segmentation_authority = editor_reconciled
publish_ready = true
```

生产 authority 必须在 final-render normalized config、artifact evidence、exact QA 三层一致。

## 8. Viewer-facing display layer

已获得 production authority 后，如需平台文字显示修订或极端未知 end 的 shorten-only presentation trim，再运行：

```text
v4_apply_display_policy.py
```

display layer 不回写 canonical lyric，不移动 cue start，不改变 occurrence/track/canonical identity。模型 override 必须 task-bound 且绑定 expected text；强脏词自动规则保持窄定义。

## 9. Release 前 QA

推荐顺序：

```text
production/display materialization
-> v4_audit_final.py
-> v4_validate_release.py
```

`v4_audit_final.py` 是 diagnostic-only；检查 cue duration、file order、occurrence/content-end containment、same/cross occurrence overlap 等，不授予 release authority。

只有 release validator 对唯一 final-render 验证通过，才称正式生产 FINAL。

典型最终 QA：

```text
passed = true
structurally_valid = true
fully_reviewed = true
publish_ready = true
review_candidate_count = 0
release_blocked_reason = ""
```

## 10. Resume / workers / 性能

Max public run 支持安全 resume 与 bounded workers：

```text
--workers 1..4   # default 2
--no-resume      # force recomputation
```

workers/no-resume/out-dir/git metadata 属于执行策略，不进入 `v4_run_config.json`。a12 起 coarse/Fine 只解码当前请求 mix interval + 2 秒上下文，不改变候选、TimeWarp、review 或 release semantics。

Resume 只有 task、algorithm、clean current Git identity、runtime、upstream artifact 等全部匹配时才允许跨 run 复用；否则只是 cache miss，重新执行。

## 11. 多语言与同时间戳 LRC

同 timestamp 多行只有唯一 canonical original 时才自动继续。无法唯一判断：

1. 优先清洗 canonical LRC；
2. 必须保留多行时使用 `lyric_role_map.json`；
3. 将它绑定进 `v4_run_config.json`；
4. 不降低角色判断阈值。

详见 `v4-lyric-role-overrides.md`。

## 12. 开发/回归原则

- 真实任务失败可驱动设计，但 public test 只使用 generic synthetic fixture；
- 不把歌曲名、歌词、真实时间戳、真实音频写进通用源码/tests；
- 新算法候选先 calibration，再 fresh blind；blind 失败后不看结果继续调 threshold 挽救同 candidate；
- false repair / false ready 风险优先于减少 review；
- 普通 timing 已接近当前输入条件下的高位，下一阶段重点是运行可复现性、结构事件和生产自动闭环。

当前正式 calibration 与 roadmap 见 `v4-status.md`、`multilingual-roadmap.md`。

## 13. 当前权威文档

```text
production-requirements.md  -> 真实 workload / normative product baseline
v4-status.md                -> 当前能力、版本、benchmark、限制
v4-runtime-guide.md         -> 具体生产运行
v4-cli-contract.md          -> CLI/path/release safety
v4-change-record.md         -> 主线变更历史
task-template.md            -> 新任务最短模板
workflow.md                 -> 本文件，总体当前流程
```

`references/archive/` 与历史 `change-record.md` 可保留旧版本事实，但不能作为当前生产入口。
