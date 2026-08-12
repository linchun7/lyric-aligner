---
name: lyric-aligner
description: Align multilingual lyrics to edited-song subtitle timelines using Jianying SRT anchors, canonical LRC text, source-audio waveform and BPM mapping, ASR evidence, audited manual overrides, and final QA. Use when processing music mixes, karaoke subtitles, lyric timing correction, multilingual SRT/LRC alignment, missing lyric lines, split or merged subtitle cues, or audio-cut detection.
---

# Lyric Aligner

将这个 Skill 用于把多语言歌曲歌词对齐到经过剪辑的混剪音频和字幕时间轴，并输出可审计的 SRT、审计报告与 QA 结果。

## 先遵守这些不变量

1. 将剪映或其他编辑器导出的 SRT 时间格作为主要时间锚点；不要只用 BPM 推算歌词时间。
2. 将规范 LRC 作为歌词文字和歌词顺序的来源；不要把 ASR 猜测直接当作规范歌词。
3. 将原曲音频用于波形对齐、变速比例和剪切点检测；不要复制、重编码或修改原曲文件。
4. 将 BPM 作为速度比例先验和异常检查；让实际波形和歌词锚点决定映射。
5. 只有在没有可靠原字幕格的空档中新增字幕；只有在波形或人工证据足够强时修改原字幕边界。
6. 将剪掉的原曲区间登记为确认的音频剪切；不要把被剪掉的 LRC 行静默补回终稿。
7. 只保留规范 LRC 中存在的短促衬词；不要因为剪映、ASR 或音频检测到未收录的 `Uh`、`Oh`、`Yeah`、`Ah`、`Ooh`、`Huh`、`Na-na` 而新增歌词。
8. 对重复副歌保持出现次数；不要仅凭相同文字去重。
9. 将每次人工确认写入当前任务专属覆盖和回归文件，并用源 SRT SHA-256 限定作用域。
10. 只有 `publish_ready=true` 且高风险候选已确认或拒绝时，才把结果称为可发布。

## 变更隔离与全局优化原则

- 单个任务的歌词、时间轴、音频剪辑候选、QA 豁免和人工确认，优先放在 `private/<任务名>/qa/` 或任务输出目录中，避免污染全局逻辑。
- 不得仅为适配单个任务修改全局脚本、算法、QA 阈值或白名单，也不得仅为让某个任务通过 QA 而放宽全局规则。
- 如果发现的是通用 bug，或经过验证、具备可复用性并能提升整体质量的优化，可以修改全局代码，但必须先说明改动原因和影响范围。
- 全局改动必须按照 `references/change-record.md` 明确记录：问题证据和改动原因、影响范围与兼容性、单元测试结果、既有任务回归结果、新任务 QA 结果、算法版本或变更摘要，以及从任务级/实验性改动升级为全局改动的依据。
- 无法确认通用性的改动，应先作为任务级或实验性改动；完成验证并记录依据后，才能升级为全局版本。

## 先划分 Skill 资源和本地项目数据

### 可上传的 Skill 层

- `SKILL.md`：触发条件、核心不变量和执行导航。
- `agents/openai.yaml`：Skill 列表显示信息。
- `scripts/redo_karaoke_pipeline.py`：生产工作流和最终 QA 的主入口。
- `scripts/karaoke_subtitle_pipeline.py`：可选的诊断、探索性 ASR 和比较草稿工具，不替代主入口或最终 QA。
- `scripts/validate_korean_asr.py`：配置驱动的韩文 ASR 验证入口。
- `scripts/check_environment.py`：检查 Python、Python 依赖和 `ffprobe`。
- `scripts/init_task.py`：创建被忽略的本地任务目录和带源 SRT 哈希的 QA 文件骨架。
- `references/workflow.md`：完整工作流和 QA 门槛。
- `references/change-record.md`：全局改动记录模板、升级门槛和任务级 QA 文件格式。
- `references/task-template.md`、`references/prompt-template.txt`：新任务输入契约和调用模板。
- `references/multilingual-roadmap.md`：多语言能力边界与升级路线。
- `.gitignore`：上传边界的机器可执行规则。
- `requirements.txt`、`requirements-asr.txt`：基础依赖和可选 ASR 依赖声明。

### 明确不上传的文件

以下内容只用于本机项目或单次任务，不得作为 Skill 资源上传或提交：

- `private/` 整个目录，包括本地项目的混剪 SRT、混剪 WAV、歌曲清单、BPM 清单、原语言 LRC、原曲音频和人工 QA 结论。
- `output/` 整个目录，包括 ASR JSON、映射 JSON、审计 CSV、草稿/终稿 SRT、QA JSON、回滚目录和 ZIP 包。
- 所有原始或生成音频：`*.wav`、`*.mp3`、`*.flac`、`*.m4a`、`*.aac`、`*.ogg`、`*.mp4`、`*.mov`、`*.mkv`。
- 当前任务的覆盖和回归证据：`*_manual_overrides.json`、`*_regression_cases.json`；除非已经脱敏、泛化并明确决定作为测试 fixture 上传。
- Python 缓存和临时目录：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.skill-scaffold/`。
- 任何任务的原始歌词、输入字幕、原曲目录和历史回滚包；统一放入 `private/` 或 `output/`，不要放在 Skill 根目录。

将新的任务输入放在 `private/<任务名>/input/`，将人工结论放在 `private/<任务名>/qa/`，将生成结果放在 `output/<任务名>/`。开始新任务前不要复用其他任务的覆盖、回归文件或全轨偏移。

如果仓库中存在 `private/lyric-aligner.local.md` 或 `private/lyric-aligner.local.json`，只在用户明确要求处理本机项目时读取；不要把它们当作通用 Skill 资源。

仓库根目录是 Skill 源码包，不等于已经安装或可被 `$lyric-aligner` 自动发现。只有用户明确要求安装时，才把该目录安装到受支持的 Skill 发现位置；不要在本仓库内复制第二套源码。

## 资源导航

- 需要完整阶段、证据优先级、输出文件和 QA 条件时，读取 `references/workflow.md`。
- 需要记录全局变更、建立任务级 QA override 或确认源 SRT 作用域时，读取 `references/change-record.md`。
- 需要准备输入或生成新任务提示词时，读取 `references/task-template.md` 或 `references/prompt-template.txt`。
- 需要判断中文、英文、韩文、日文和混合语言能力边界时，读取 `references/multilingual-roadmap.md`。
- 需要运行确定性处理时，直接执行 `scripts/` 中的脚本，不要把大段算法重新改写到对话中。

## 标准处理顺序

### 1. 检查输入和环境

确认存在以下文件：混剪音频、编辑器 SRT、歌曲清单、规范 LRC 目录；如果有变速或剪切，提供 BPM 清单和原曲目录。运行 `python scripts/check_environment.py`；需要 ASR 时追加 `--asr`。

新任务开始时，先把输入放入 `private/<任务名>/input/`，再创建本地目录和带源 SRT 哈希的 QA 文件：

```powershell
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/剪映.srt"
```

该命令只创建被忽略的任务目录和 QA JSON，不复制或修改输入。

检查源 SRT、音频和歌曲版本。记录路径与 SHA-256；不要把旧任务的人工结论套用到不同哈希的输入。

### 2. 生成保守文本基线

```powershell
python scripts/redo_karaoke_pipeline.py prepare `
  --audio "混剪.wav" `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --out-dir "output/任务名/01_prepare"
```

只接受高相似度、单行且顺序一致的自动替换；把其余候选放入审计，不要强行覆盖。

### 3. 对齐原曲和混剪音频

```powershell
python scripts/redo_karaoke_pipeline.py audio-align `
  --audio "混剪.wav" `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --source-dir "原曲音频目录" `
  --bpm-changes "BPM变化.txt" `
  --out "output/任务名/02_audio_alignment.json"
```

核对每首歌的波形比例、起始偏移、残差和可能的编辑跳点。波形两侧和文本锚点支持同一跳点时才将其送入确认；仍为 `review` 的剪切候选不得进入发布结果。

### 4. 投影规范歌词并补全空档

运行 `build`，输出草稿 SRT、审计 CSV 和轨道映射 JSON。让歌词索引单调递增；对一个格包含多句、相邻格跨句或 LRC 时间过密的情况使用全局序列对齐，不要逐格贪心匹配。

### 5. 对低证据语言做专项复核

需要韩文独立 ASR 时运行：

```powershell
python scripts/validate_korean_asr.py `
  --audio "混剪.wav" `
  --jobs "private/<任务名>/qa/korean_asr_jobs.json" `
  --out "output/任务名/04_korean_asr.json"
```

将 ASR 作为发音、顺序和边界证据。日文含汉字且缺少读音层时，降低自动化置信度并把少量歧义交给听音复核，不要声称与韩文能力等同。

### 6. 合成终稿并运行 QA

有韩文低证据片段时，先运行 `validate_korean_asr.py`，再运行 `refine-korean`。没有韩文 ASR 任务时跳过这两个阶段，并将 `build` 生成的报告作为 `finalize` 的输入。随后运行 `finalize` 和 `qa`；具体参数和报告字段以 `references/workflow.md` 为准。

只有同时满足结构检查和发布检查时，才交付唯一的 `<任务名>_FINAL.srt`，并一并保留 QA JSON 和审计 CSV 供追溯。

## 旧版和项目专属数据的处理

- 新任务默认使用 `scripts/redo_karaoke_pipeline.py` 中的 `ALGORITHM_VERSION = "3.7"`。
- `v3.6` 及更早结果只用于回滚和差异诊断，不作为新任务起点。
- `private/<任务名>/qa/` 中的文件只属于对应输入；代码可以读取它们，但新任务必须传入自己的覆盖和回归文件。
- 发现源 SRT 哈希不匹配时，停止加载旧人工结论并报告原因。
- 不要修改输入 SRT、LRC 或原曲音频；把所有派生结果写到 `output/`。
- 任何新的通用代码、算法、阈值或 QA 规则改动，都必须先读取 `references/change-record.md`，完成记录和验证后再提交。

## 回归测试

在仓库根目录运行：

```powershell
python -m unittest discover -s scripts -p "test_*.py"
python -X utf8 "<skill-creator-path>/scripts/quick_validate.py" .
python scripts/check_environment.py
```

测试失败时先修复脚本或输入契约，再继续处理实际任务；不要用修改测试预期的方式掩盖算法回归。
