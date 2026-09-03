# 音乐混剪歌词字幕：当前任务模板

更新：2026-09-03
当前产品路径：`Standard -> Smart -> Pro -> Max`
当前 Max 主线版本：`4.0.0a14`

本文件只描述当前生产入口。历史 v3.9/`redo_karaoke_pipeline.py` 仍保留用于回归与历史兼容，但不再是新任务默认生产路径。

## 1. 先选择模式

- **Standard**：现有剪映/Jianying timing 可信，只修 canonical 歌词文字；不读 audio，不改 cue timing。
- **Smart**：普通生产默认主力。大部分 timing 可信，只有少量可疑 cue；优先 0-audio。
- **Pro**：只处理 Smart unresolved 的局部区域；使用 bounded acoustic / ASR / forced evidence，不做无证据的自动 timing write-back。
- **Max**：整体 timeline 不可信、复杂 cut/overlap/reorder、重复段身份困难，或 Smart/Pro 无法安全收敛时使用 Full V4。

外文、韩文、日文不是自动进入 Max 的条件。

## 2. 新任务需要准备的输入

至少准备：

1. 编辑器导出的 source SRT；
2. 最终混剪音频；
3. 歌曲清单：`分:秒 歌手 - 歌名`；
4. canonical lyrics 目录；
5. Max/Pro 需要 acoustic evidence 时提供对应原曲/source audio。

可选但有价值：

- BPM / exact stretch ratio；
- Enhanced LRC / QRC word timing；
- 已知语言信息；
- 已知 middle cut、特殊版本、旁白、口播；
- QA 已证明的 detached export tail `mix_content_extent.json`；
- 已确认的语言、middle-cut、同时间戳 lyric-role 等任务级语义配置。

不要预先生成 ASR JSON、timeline artifact、audit 或 QA；这些由流程产生。

## 3. 初始化任务

建议目录：

```text
private/<任务>/
├─ input/
│  ├─ source.srt
│  ├─ mix.wav
│  ├─ songs.txt
│  ├─ bpm.txt                 # optional
│  ├─ lyrics/
│  ├─ source-audio/           # Pro/Max acoustic path
│  └─ mix_content_extent.json # optional, QA-proven only
└─ qa/
```

初始化：

```powershell
python scripts/init_task.py `
  --task "<任务>" `
  --source-srt "private/<任务>/input/source.srt" `
  --audio "private/<任务>/input/mix.wav" `
  --song-list "private/<任务>/input/songs.txt" `
  --lyrics-dir "private/<任务>/input/lyrics" `
  --bpm-changes "private/<任务>/input/bpm.txt" `
  --source-audio-dir "private/<任务>/input/source-audio"
```

不存在的可选参数不要传。

`init_task.py` 会创建并绑定：

```text
private/<任务>/qa/task_manifest.json
private/<任务>/qa/v4_run_config.json
private/<任务>/qa/<任务>_manual_overrides.json
private/<任务>/qa/<任务>_regression_cases.json
```

`task_manifest.json` 绑定原始任务输入；`v4_run_config.json` 单独绑定会改变 Full V4 语义但可能后补的配置文件。两者职责不能混淆。

## 4. Max 语义配置：不要再靠命令行记忆

`4.0.0a14` 起，任务级：

```text
private/<任务>/qa/v4_run_config.json
```

绑定以下可选语义输入：

```text
profile
language_map
middle_cut_map
lyric_role_map
```

新任务可在 `init_task.py` 初始化时直接提供：

```powershell
python scripts/init_task.py ... `
  --language-map "private/<任务>/qa/language_map.json" `
  --middle-cut-map "private/<任务>/qa/middle_cut_map.json" `
  --lyric-role-map "private/<任务>/qa/lyric_role_map.json"
```

旧任务或后续新增/调整语义配置时，使用：

```powershell
python scripts/init_v4_run_config.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --language-map "private/<任务>/qa/language_map.json" `
  --lyric-role-map "private/<任务>/qa/lyric_role_map.json" `
  --replace
```

`--replace` 只用于有意识的整份配置迁移。已有配置与新输入不同但未显式 `--replace` 时必须失败；使用它时，未再次指定的语义项会变为 `null`，所以需要把仍要保留的语义项一并写出。

只要 `v4_run_config.json` 存在，三个 public Max run entrypoint 会自动发现、校验并使用它。调用者不需要、也不应该继续手抄四个语义 CLI 参数。若仍显式传入，路径必须与配置完全一致；配置记录 `null` 时临时塞入新 map 也会 fail closed。

该 config 记录自己的 `run_config_fingerprint_sha256`；正式 asset artifact 仍记录实际 `profile/language/middle-cut/lyric-role` SHA，所以生产 lineage 继续由真实语义文件身份而非调用者记忆决定。

## 5. 日常生产入口

### Standard

```powershell
python scripts/v4_text_repair.py ...
```

冻结 cue count / number / start / end，只修 canonical text/order。

### Smart

```powershell
python scripts/v4_smart_repair.py ...
```

当前 Smart 为 v1.2.10。普通“大部分时间轴正确”的任务应先走这里。

### Pro

```powershell
python scripts/v4_pro_selective.py ...
```

当前 Pro 为 v1.2.6，只处理 Smart unresolved 的 bounded regions。

### Max

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<当前 clean HEAD>"
```

默认 workers=2；`--workers 1..4` 只影响执行调度，不属于语义配置。`--no-resume` 也只是执行策略，不改变 production truth。

若任务有 `v4_run_config.json`，上述最简命令会自动携带正确 semantic config。

## 6. Max 后处理与正式发布

Raw Max 的 `review_required` 是正常的 fail-closed 状态。需要按实际问题依次闭合：

```text
v4_run
-> review
-> cut / overlap materialization（如有）
-> reference-retime（仅有独立证据时）
-> canonical evaluation render
-> editor-cue reconciliation
-> production materialization
-> optional display policy
-> final candidate audit
-> release validation
```

`ready_for_render` 不等于 `publish_ready`。

正式 FINAL 至少要求：

```text
passed = true
structurally_valid = true
fully_reviewed = true
publish_ready = true
review_candidate_count = 0
segmentation_authority = editor_reconciled
release_blocked_reason = ""
```

并由 `v4_validate_release.py` 验证唯一 hash-bound final-render artifact 的 config/evidence/QA authority 三层一致。

## 7. 多语言与 lyric-role

Canonical lyric 永远是最终文字/顺序 truth；同 timestamp 多行不能靠“第一行”猜 original。

无法唯一确认时：

1. 优先换成干净 canonical LRC；
2. 必须保留多行时使用任务级 `lyric_role_map.json`；
3. 把该 map 写入 `v4_run_config.json`；
4. 不降低 role threshold，不把真实歌曲特例写入通用代码。

详见 `references/v4-lyric-role-overrides.md`。

## 8. 重跑与复用边界

- 新 mix 即使歌单相同，也必须重新建立任务指纹与 acoustic mapping；
- 原始任务输入变化后，旧 manifest/QA 不得继续复用；
- semantic config 文件内容变化后，旧 `v4_run_config.json` 必须拒绝运行，需有意识迁移；
- cache/resume 只能在 task、algorithm、git、runtime、upstream identity 都满足契约时复用；
- 真实人工/模型结论必须写回 task-local QA/config，不得只留在聊天记录；
- 不允许为了“通过”直接编辑 artifact 或降低 fail-closed threshold。

## 9. 交付判断

生产结果的目标不是声称数学意义 100%，而是：自动部分有证据、不可证明部分明确 review、最终 release 不存在已知 silent blocker。

当前权威说明：

- `references/production-requirements.md`
- `references/v4-status.md`
- `references/v4-runtime-guide.md`
- `references/v4-cli-contract.md`
- `references/v4-change-record.md`
