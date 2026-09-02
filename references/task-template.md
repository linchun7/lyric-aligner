# 音乐混剪精校字幕：任务调用模板

## 版本与 QA

新任务默认使用算法 `v3.9`。运行前确认 `scripts/redo_karaoke_pipeline.py` 的 `ALGORITHM_VERSION` 为 `3.9`，并为任务创建 schema 2.0 的 `task_manifest.json`。

`publish_ready=true` 表示所有已知结构错误和高、中、低风险候选均已清零；它不是未经测量的绝对正确率。物理不可辨、歌词源冲突或版本不一致时，系统必须阻止发布，而不是猜测。

## 每次需要准备的文件

必须提供：

1. 混剪音频：WAV、FLAC、MP3 或 M4A。
2. 编辑器导出的 SRT；不要先手工清洗。
3. 歌曲清单：`分:秒 歌手 - 歌名`。
4. 规范原语言 LRC 目录。

若有 Enhanced LRC 或 QRC 逐字/逐词时间，可一并提供。它只用于辅助校正整行起止和映射，正式 SRT 仍是逐行字幕。

完整波形流程还需要：

5. 每首原曲音频，版本尽量与混剪一致。

强烈建议：

6. BPM 变化清单。
7. 每首歌或片段的语言：`zh`、`en`、`ko`、`ja`、`mixed`。
8. 已知剪切、旁白、口播、版本差异和项目帧率。
9. 同一输入版本的既有覆盖、回归案例或终稿。

不需要预先准备 ASR JSON、对齐 JSON、审计 CSV 或 QA JSON；这些由流程生成。

## 初始化

先把输入放入 `private/<任务名>/input/`，然后运行：

```powershell
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --source-audio-dir "private/任务名/input/source-audio"
```

可选项不存在时不要传。若 QA 已证明主节目结束后存在 detached export tail，可另外提供 fingerprint-bound 的 `--mix-content-extent "private/任务名/input/mix_content_extent.json"`；该 JSON 只允许缩短自动 `content_end`，必须绑定同一 mix audio SHA，并保留原始音频文件/物理时长。

之后所有生产命令都传同一个：

```text
--task-manifest private/<任务名>/qa/task_manifest.json
```

旧 QA 必须运行 `scripts/migrate_task.py`；不要手工添加几个字段冒充迁移，也不要让生产命令接受 legacy 文件。

## 输入格式

歌曲清单：

```text
00:00 Artist A - Song A
02:56 Artist B - Song B
05:52 Artist C - Song C
```

BPM 清单：

```text
Artist A - Song A 126-130 -
Artist B - Song B 120-130 -
```

第一个数字为原曲 BPM，第二个为混剪 BPM。不确定时留空，不要猜。

ASR 作业：

```json
{
  "schema_version": "1.0",
  "jobs": [
    {
      "id": "opaque-window-id",
      "track": "Song A",
      "start": 12.0,
      "end": 25.0,
      "language": "mixed",
      "language_mode": "detect"
    }
  ]
}
```

## 复用边界

- 可跨任务复用：v3.9 通用程序、语言 profile、QA 规则、同版本规范 LRC 和原曲。
- 中段剪切候选必须经 `review-audio-edits` 写入 reviewed alignment 后才能继续。
- 两首歌交接候选先用 `_cross_track_overlap_reviews` 确认或拒绝；确有叠唱时分别保留两条逐行字幕，并用 `_confirmed_overlap_intervals` 限定允许的重叠范围，不得自动拼成一行。
- 只能在完整任务指纹一致时复用：cue 覆盖、毫秒边界、确认剪切、确认遗漏和回归案例。
- 新混剪即使歌单相同，也必须重新计算波形映射。
- 用户或模型确认的新结论必须写回任务级 QA，不得只留在对话记录。
- 重跑一首歌不得改变其他歌曲；应以回归案例和数据集指标验证。

## 多语言边界

- 英文按词、缩写和连字符单位检查。
- 中文按汉字，拼音只作为可选辅助证据。
- 韩文按音节或短词，并可结合罗马音与 ASR。
- 日文统一假名；含汉字时需要读音层。缺少 `pykakasi` 时不得自动标高置信。
- 混合语言用 `mixed`，并保守提高自动阈值。

## 交付标准

- `<任务名>_FINAL.srt`：唯一正式字幕。
- `<任务名>_FINAL_QA.json`：机器验收结果。
- `<任务名>_FINAL_审计.csv`：逐 cue 来源和证据。
- `issues=[]`、`passed=true`、`fully_reviewed=true`、`publish_ready=true`、`review_candidate_count=0`。

授权真实数据可用于私有训练、阈值校准和盲测。按 `references/dataset-protocol.md` 划分数据，不把大量未配对歌词文本误当成边界训练集，也不把原始素材提交到普通源码树。
