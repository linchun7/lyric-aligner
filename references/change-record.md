# 变更记录与任务级 QA 文件规范

## 目录

- 全局代码或算法改动记录模板
- 任务级 QA 文件统一元数据
- 当前仓库级变更记录

本文件是仓库级模板。任务的具体歌词、cue 编号、毫秒时间、原曲和人工听音结论仍必须留在 `private/<任务名>/qa/`，不得写入 Skill 资源。

## 全局代码或算法改动记录

当任务级处理升级为全局脚本、算法、QA 规则或阈值改动时，记录以下内容：

```markdown
# <变更标题>

- 日期：YYYY-MM-DD
- 范围：task-level / experimental / global
- 算法版本：<例如 3.7>
- 提交：<commit 或 PR>

## 问题证据与改动原因

- 可复现输入或回归案例：
- 原有行为：
- 改动原因：

## 影响范围与兼容性

- 影响的脚本、命令或 QA 字段：
- 受影响的既有任务：
- 兼容性风险：
- 回滚方式：

## 验证结果

- 单元测试：
- 既有任务回归：
- 新任务 QA：
- 前后指标对比：

## 升级依据

- 为什么该改动具备通用性：
- 为什么不能继续作为任务级或实验性改动：
```

全局改动必须先完成记录，再合并或推送。若通用性尚未确认，应停留在任务级或实验性范围。

## 任务级 QA 文件统一元数据

`*_manual_overrides.json` 和 `*_regression_cases.json` 均使用以下顶层元数据：

```json
{
  "schema_version": "1.0",
  "project": "<任务名>",
  "source_srt_sha256": "<源 SRT 的 64 位小写 SHA-256>",
  "scope": "Only this exact Jianying SRT. Never load for another mix."
}
```

- `source_srt_sha256` 是必填作用域 guard，必须对应当前任务的源 SRT。
- 缺少、格式错误或不匹配时，`finalize` 必须拒绝使用 override，`qa` 必须报告失败。
- 旧文件中的 `_source_srt_sha256` 只作为迁移兼容字段；新文件不得继续使用下划线字段。
- `manual_overrides` 的具体覆盖区段包括 `_insertions`、`_cue_splits`、`_timing_overrides`、`_lrc_indices_overrides`、`_confirmed_omitted_lrc_events`、`_confirmed_boundary_pairs` 和 `_review_notes`；每项人工结论都要带有可追溯的 `evidence` 或 `reason`。
- 音频剪辑候选只能标记为 `confirmed` 或 `rejected`；`review` 不得进入可发布结果。
- `regression_cases` 的具体案例放在 `cases` 数组中，每个案例必须有稳定的 `id`、`kind`、时间范围/文本和必要的容差。

任务结束后保留 override、regression case、QA JSON 和审计 CSV，确保每个结果都能回溯到精确的源 SRT。

## 2026-08-12 Skill 源码仓库成熟化

- 日期：2026-08-12
- 范围：global（仓库结构、文档契约和辅助工具）
- 算法版本：3.7，未变更
- 提交：本次历史重建后的根提交

### 问题证据与改动原因

- 输出目录在文档中存在两套约定，韩文 ASR 到终稿的命令链不完整。
- 仓库缺少依赖声明、环境自检、任务级 QA 骨架初始化和对应测试。
- 根目录歌曲清单/BPM 文件的忽略规则过窄，公开文档和注释仍含任务化措辞。

### 影响范围与兼容性

- 影响 `.gitignore`、Skill/工作流文档、依赖文件，以及环境检查和任务初始化辅助脚本。
- 生产入口仍为 `scripts/redo_karaoke_pipeline.py`，核心对齐算法和 QA 阈值未改动。
- 输出统一为 `output/<任务名>/`；旧输出仍可保留在本机，但新命令不再使用 `output/transcribe/`。
- 回滚方式：恢复本次文档、忽略规则和辅助脚本改动；算法 3.7 无需回滚。

### 验证结果

- 单元测试：`python -m unittest discover -s scripts -p "test_*.py"`，28 项通过。
- CLI 帮助：生产入口 6 个子命令、诊断入口 5 个子命令，以及环境检查、任务初始化和韩文 ASR 入口均通过。
- 环境检查：基础模式和 `--asr` 模式均通过；Python、NumPy、SciPy、librosa、faster-whisper 与 ffprobe 可用。
- Skill 校验：`quick_validate.py` 通过；`git diff --check` 通过。
- 隐私与上传边界：当前公开树未发现本机绝对路径、账号标识、固定输入哈希或受忽略的媒体/字幕/QA 产物。
- 既有任务回归：本次未读取或修改 `private/`、`output/`，未运行含私有素材的任务回归。
- 新任务 QA：本次为源码仓库成熟化，不生成实际歌曲终稿。

### 升级依据

- 改动解决所有任务共同面对的环境、目录、隐私边界和初始化问题，不依赖某一首歌曲或某一时间点。
- 这些约束属于仓库级契约，留在单个任务中会继续造成文档分叉和误上传风险。

### 已知边界

- 当前工作树已按公开/私有边界清理；早期 Git 历史仍可能保留旧的个性化文本。未经仓库所有者明确授权，不重写历史。
