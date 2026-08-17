---
name: lyric-aligner
description: Align multilingual lyrics to edited-song subtitle timelines using editor SRT anchors, canonical line- or word-timed lyrics, source-audio waveform and variable-speed mapping, multilingual ASR evidence, task-scoped overrides, regression cases, and release-blocking QA. Use for music mixes, karaoke subtitles, lyric timing correction, missing lyric lines, split or merged cues, repeated choruses, middle cuts, speed ramps, and overlapping song transitions.
---

# Lyric Aligner

使用本 Skill 将中、英、韩、日及混合语言歌词对齐到经过剪辑的混音与字幕时间轴，并生成可审计的 SRT、CSV、映射 JSON 和 QA 结果。

## 不可违反的原则

1. 编辑器 SRT 是主要时间锚点，但其文字和边界都可能错误；不得只按 BPM 推算字幕时间。
2. 规范 LRC 是最终歌词文字与顺序来源；ASR 只能作为发音、顺序和边界证据，不能替代规范歌词。
3. 原曲音频用于波形对齐、变速和剪切检测；不得修改原始输入。
4. 被确认剪掉的原曲区间不得补回终稿。
5. 只有没有可靠字幕格的空档才新增 cue；修改既有边界必须有更强音频证据或任务级人工证据。
6. 重复副歌按实际出现次数保留，不能仅因文本相同而去重。
7. LRC 未收录的 `Uh/Oh/Yeah/Ah/Ooh/Huh/Na-na` 等发声不得自动加入字幕。
8. 通用算法不得包含某首歌、某个 cue、某个时间点或具体错词的硬编码修补。
9. 所有任务 QA 必须绑定完整任务指纹；音频、SRT、歌单、LRC、BPM 或原曲任一变化都不得静默复用旧结论。
10. 只有 `passed=true`、`fully_reviewed=true`、`publish_ready=true` 且 `review_candidate_count=0` 时才可称为可发布。
11. 逐字/逐词时间只作整行歌词的起止、边界和映射证据；正式 SRT 始终输出完整逐行歌词，不生成逐字卡拉 OK cue。
12. 两首歌曲同时演唱时不得把两路歌词猜拼成一行；候选必须由任务级 `_cross_track_overlap_reviews` 确认或拒绝，确认后还需用 `_confirmed_overlap_intervals` 限定两条同期逐行 cue 的精确范围。

不能保证所有物理不可辨片段达到绝对 100%。正确策略是自动发现不确定性并阻止发布，而不是猜测。

## 源码与私有数据边界

可提交的 Skill 源码包括：

- `SKILL.md` 与 `agents/openai.yaml`
- `scripts/` 中的算法、环境检查、任务契约、迁移、ASR、评估和测试代码
- `references/` 中的工作流、模板、变更记录和数据协议
- `.github/workflows/validate.yml`
- `.gitignore` 与依赖声明

默认不得提交：

- `private/`：原始字幕、音频、LRC、歌单、BPM、任务 manifest、人工覆盖和回归案例
- `output/`：草稿、终稿、ASR、映射、审计、QA、缓存和回滚包
- 根目录散落的 `*.srt`、`*.lrc`、音视频、任务 QA JSON 与任务清单
- 本机绝对路径、账号、凭据、访问令牌和个人配置

已获授权的真实素材可以在私有环境中用于训练、校准和回归。数据量不是唯一目标；优先收集音频、规范歌词、准确边界、版本、语言和错误类型相互对应的高质量标注。读取 `references/dataset-protocol.md` 了解拆分、盲测和聚合评估方式。未经用户明确要求，不上传 `private/`。

仓库根目录是 Skill 源码，不等于已经安装。只有用户明确要求安装时，才安装到支持的 Skill 发现位置。

## 资源导航

- 完整命令链与 QA 字段：`references/workflow.md`
- 全局变更和 schema 2.0：`references/change-record.md`
- 新任务输入模板：`references/task-template.md`
- 可复制提示词：`references/prompt-template.txt`
- 多语言状态与下一阶段：`references/multilingual-roadmap.md`
- 私有训练/回归数据：`references/dataset-protocol.md`

## 标准执行顺序

### 1. 检查环境并初始化任务

```powershell
python scripts/check_environment.py
python scripts/check_environment.py --asr

python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --bpm-changes "private/任务名/input/bpm.txt" `
  --source-audio-dir "private/任务名/input/source-audio"
```

不存在的可选输入不要传。初始化会创建 `private/<任务名>/qa/task_manifest.json`、schema 2.0 的 QA 骨架和 `output/<任务名>/`。

旧任务不得由生产命令静默兼容；使用 `scripts/migrate_task.py` 显式迁移。迁移会先生成 `.schema1.bak`。

### 2. 按完整工作流处理

依次运行：

1. `prepare`
2. `audio-align`（有原曲时）
3. 检查剪切候选；将精确决定写入 `_audio_edit_reviews` 后运行 `review-audio-edits`
4. `build`，并始终使用已复核的 alignment JSON
5. `validate_multilingual_asr.py` 与 `refine-asr`（有低证据片段时；`refine-korean` 是兼容别名）
6. `finalize`
7. `qa`

所有阶段都必须传入同一个 `--task-manifest`。中间 JSON 和 CSV 也携带同一 `task_fingerprint_sha256`；不匹配时立即停止。

韩文旧作业可继续通过 `validate_korean_asr.py` 兼容入口运行。新任务优先使用 `validate_multilingual_asr.py`，语言可设为 `zh`、`en`、`ko`、`ja` 或 `mixed`。

### 3. 交付

只交付一个名称明确的 `<任务名>_FINAL.srt`，同时保留 `<任务名>_FINAL_QA.json` 与审计 CSV。任何低、中、高风险候选都会令 `fully_reviewed=false` 和 `publish_ready=false`。

## 版本与回归

- 当前生产算法版本：`v3.9`。
- `v3.7` 是迁移前基线，不再作为新任务默认入口。
- 当前公开 Git 历史从脱敏根提交 `4ce42eb` 开始；更早历史已清理，不能通过普通 Git 历史回滚。
- 每次全局改动必须更新 `references/change-record.md`，并运行：

```powershell
python -m compileall -q scripts
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/check_environment.py
git diff --check
```

需要完整 YAML 校验时先安装 `requirements-dev.txt`；需要 ASR 时安装 `requirements-asr.txt`；需要中文拼音和日文读音层时安装 `requirements-language.txt`。
