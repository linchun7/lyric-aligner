# 多语言混剪歌词字幕工作流

## 默认版本与结果语义

- 当前生产算法为 `v3.8`。
- `passed=true` / `structurally_valid=true`：结构检查没有发现确定性错误。
- `fully_reviewed=true`：高、中、低风险候选均为零。
- `publish_ready=true`：同时满足结构通过和候选清零。
- `release_status=ready`：允许把结果称为正式终稿；否则必须是 `blocked`。

这些字段表示“没有已知未解决问题”，不等于未经测量的绝对 100% 正确率。

## 任务目录与完整指纹

建议目录：

```text
private/<任务名>/
├─ input/
│  ├─ source.srt
│  ├─ mix.wav
│  ├─ songs.txt
│  ├─ bpm.txt                  # 可选
│  ├─ lyrics/
│  └─ source-audio/            # 完整波形流程需要
└─ qa/
   ├─ task_manifest.json
   ├─ <任务名>_manual_overrides.json
   └─ <任务名>_regression_cases.json

output/<任务名>/
```

`task_manifest.json` 使用 schema 2.0，记录以下输入的路径、大小、哈希或目录递归哈希：

- `source_srt`
- `audio`
- `song_list`
- `lyrics_dir`
- `bpm_changes`（可空）
- `source_audio_dir`（可空）

任务指纹由项目名和这些内容记录共同计算。任一输入内容变化后，旧 manifest、中间产物、人工覆盖和回归文件都必须拒绝复用。

## 阶段 0：环境检查

基础流程：

```powershell
python -m pip install -r requirements.txt
python scripts/check_environment.py
```

ASR：

```powershell
python -m pip install -r requirements-asr.txt
python scripts/check_environment.py --asr
```

语言读音层：

```powershell
python -m pip install -r requirements-language.txt
```

开发与完整 Skill YAML 校验：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/validate_skill.py .
```

不同 Python 解释器拥有不同依赖环境。必须使用准备运行生产命令的同一个解释器执行环境检查。

## 阶段 1：初始化或迁移任务

新任务：

```powershell
python scripts/init_task.py `
  --root "." `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --source-audio-dir "private/任务名/input/source-audio"
```

可选输入不存在时不要传对应参数。完整生产波形对齐仍需要原曲目录；没有原曲时只能先运行文本基线或使用已经验证并迁移的对齐证据，不得伪造波形结果。

旧 QA 文件必须显式迁移：

```powershell
python scripts/migrate_task.py `
  --root "." `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --source-audio-dir "private/任务名/input/source-audio" `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --regression-cases "private/任务名/qa/任务名_regression_cases.json" `
  --manifest "private/任务名/qa/task_manifest.json"
```

可重复传入 `--regression-cases`。迁移会先完整读取所有文件，再为每个被改写的旧文件创建 `.schema1.bak`，最后写入 manifest；生产命令不接受 legacy `_source_srt_sha256`。

## 阶段 2：保守文本基线

```powershell
python scripts/redo_karaoke_pipeline.py prepare `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio "private/任务名/input/mix.wav" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --out-dir "output/任务名/01_prepare"
```

只自动采用高相似、单一 LRC 行和单调顺序一致的候选。准备阶段不改变输入，输出文本草稿、审计 CSV 和带任务指纹的 manifest。

## 阶段 3：原曲与混剪波形对齐

```powershell
python scripts/redo_karaoke_pipeline.py audio-align `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio "private/任务名/input/mix.wav" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-dir "private/任务名/input/source-audio" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --out "output/任务名/02_audio_alignment.json"
```

BPM 是速度比例先验，不是时间戳。实际波形路径和高可信文本锚点决定映射。检测到源时间向前跳跃时只生成剪切候选；状态为 `review` 的剪切会阻止发布，只有明确改为 `confirmed` 或 `rejected` 后才能继续作为正式证据。

## 阶段 4：投影规范歌词

```powershell
python scripts/redo_karaoke_pipeline.py build `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --out-srt "output/任务名/03_build.srt" `
  --out-report "output/任务名/03_build.csv" `
  --out-mapping "output/任务名/03_mapping.json"
```

默认不保留任何固定 cue 白名单。若任务确有旁白等必须冻结的格，可显式传 `--preserve-cues`，并将理由记录到任务 QA，而不是修改全局默认值。

构建阶段执行全局单调序列对齐、重复段约束、跨格文本 span、缺格插入和长格拆分。所有 CSV 行和映射 JSON 都携带任务指纹。

## 阶段 5：多语言 ASR 证据

新 ASR 作业文件：

```json
{
  "schema_version": "1.0",
  "jobs": [
    {
      "id": "opaque-window-id",
      "track": "与歌曲清单一致的标题",
      "start": 100.0,
      "end": 130.0,
      "language": "mixed",
      "language_mode": "detect"
    }
  ]
}
```

运行：

```powershell
python scripts/validate_multilingual_asr.py `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --audio "private/任务名/input/mix.wav" `
  --jobs "private/任务名/qa/asr_jobs.json" `
  --out "output/任务名/04_asr.json"

python scripts/redo_karaoke_pipeline.py refine-asr `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --asr-json "output/任务名/04_asr.json" `
  --in-report "output/任务名/03_build.csv" `
  --out-srt "output/任务名/04_refined.srt" `
  --out-report "output/任务名/04_refined.csv"
```

`refine-korean` 是同一命令的兼容别名。默认按语言 profile 选择阈值；只有做过校准实验时才手工传 `--min-asr-score` 或 `--min-asr-coverage`。

日文含汉字且缺少 `pykakasi` 读音层时只能标为复核，不能自动标高置信。中文拼音和日文读音只作为辅助证据，终稿仍使用规范 LRC 原文。

## 阶段 6：合成终稿

```powershell
$report = "output/任务名/04_refined.csv" # 未运行 ASR 时改为 03_build.csv

python scripts/redo_karaoke_pipeline.py finalize `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --srt "private/任务名/input/source.srt" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --in-report $report `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --out-srt "output/任务名/任务名_FINAL.srt" `
  --out-report "output/任务名/任务名_FINAL_审计.csv"
```

人工覆盖只允许写入 schema 2.0 文件。覆盖文本、插入、拆分、时序、LRC 索引、确认遗漏、确认边界和复核备注都必须有 `evidence` 或 `reason`。

## 阶段 7：最终 QA

```powershell
python scripts/redo_karaoke_pipeline.py qa `
  --task-manifest "private/任务名/qa/task_manifest.json" `
  --source-srt "private/任务名/input/source.srt" `
  --final-srt "output/任务名/任务名_FINAL.srt" `
  --report "output/任务名/任务名_FINAL_审计.csv" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --regression-cases "private/任务名/qa/任务名_regression_cases.json" `
  --out "output/任务名/任务名_FINAL_QA.json" `
  --out-review "output/任务名/任务名_REVIEW.csv"
```

正式交付至少要求：

- `issues=[]`
- `passed=true`
- `structurally_valid=true`
- `fully_reviewed=true`
- `publish_ready=true`
- `review_candidate_count=0`
- `unexpected_overlap_count=0`
- `lyric_index_regression_count=0`
- `unresolved_lyric_gap_count=0`
- `unreviewed_audio_edit_candidate_count=0`
- `noncanonical_vocalization_count=0`

中低风险候选不再被视为可发布。命令在 `publish_ready=false` 时返回非零状态。

## QA schema 2.0

`manual_overrides` 与 `regression_cases` 顶层全部必填：

```json
{
  "schema_version": "2.0",
  "artifact_type": "manual_overrides",
  "project": "任务名",
  "source_srt_sha256": "<64 位小写 SHA-256>",
  "task_fingerprint_sha256": "<64 位小写 SHA-256>",
  "scope": "Bound to this exact task manifest fingerprint; never reuse for another audio, SRT, song list, BPM list, lyric set, or source-audio set."
}
```

代码会逐字段验证 `schema_version`、`artifact_type`、`project`、`source_srt_sha256`、`task_fingerprint_sha256` 和 `scope`，不再只检查 SRT 哈希。

## 测试与数据评估

```powershell
python -m compileall -q scripts
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/check_environment.py
git diff --check
```

测试包含合成音频的 `prepare → audio-align → build → finalize → qa` 完整链路，不使用真实歌词或歌曲。

授权私有数据集按 `references/dataset-protocol.md` 管理，并运行：

```powershell
python scripts/evaluate_dataset.py `
  --dataset "private/datasets/<名称>/dataset.json" `
  --out "output/evaluation/v3.8.json"
```

评估输出仅含聚合指标和匿名 case id，不输出歌词正文。

## 源码发布边界

可提交算法、测试、文档、依赖和脱敏合成 fixture。不得提交 `private/`、`output/`、真实音视频、任务歌词、任务 QA、账号、本机绝对路径或凭据。大型授权数据如需版本管理，使用 Git LFS 或独立私有 dataset 仓库，并仍保持 train/calibration/blind_test 隔离。
