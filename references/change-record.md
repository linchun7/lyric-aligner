# 变更记录与任务级 QA 文件规范

任务的真实歌词、cue、毫秒时间、原曲和人工结论必须留在 `private/<任务名>/qa/`，不得写入公开 Skill 资源。

## 全局改动记录模板

```markdown
# <变更标题>

- 日期：YYYY-MM-DD
- 范围：experimental / global
- 算法版本：<例如 3.8>
- 提交：<commit 或 PR>

## 问题证据与原因
- 可复现输入或回归案例：
- 原有行为：
- 改动原因：

## 影响与兼容性
- 影响的脚本、命令或 QA 字段：
- 受影响的既有任务：
- 迁移方式：
- 回滚方式：

## 验证
- 单元与端到端测试：
- 既有任务非破坏性回归：
- 私有盲测指标：
- 隐私扫描：

## 升级依据
- 为什么具备通用性：
- 为什么不能只保留为任务级规则：
```

## QA schema 2.0

`manual_overrides` 与 `regression_cases` 使用完整元数据：

```json
{
  "schema_version": "2.0",
  "artifact_type": "manual_overrides",
  "project": "<任务目录名>",
  "source_srt_sha256": "<64 位小写 SHA-256>",
  "task_fingerprint_sha256": "<64 位小写 SHA-256>",
  "scope": "Bound to this exact task manifest fingerprint; never reuse for another audio, SRT, song list, BPM list, lyric set, or source-audio set."
}
```

全部字段均必填并由代码逐项验证：

- `schema_version` 必须为 `2.0`。
- `artifact_type` 必须与文件用途相符。
- `project` 必须与 manifest 和 `private/<任务名>/` 一致。
- `source_srt_sha256` 必须与 manifest 中源 SRT 一致。
- `task_fingerprint_sha256` 必须绑定音频、SRT、歌单、LRC、BPM 和原曲集合。
- `scope` 必须使用统一作用域声明。

legacy `_source_srt_sha256` 不再由生产命令接受。使用 `scripts/migrate_task.py`，并保留 `.schema1.bak`。

`manual_overrides` 可包含 `_insertions`、`_cue_splits`、`_timing_overrides`、`_lrc_indices_overrides`、`_confirmed_omitted_lrc_events`、`_confirmed_boundary_pairs`、`_audio_edit_reviews` 和 `_review_notes`。每项需有 `evidence` 或 `reason`。

`regression_cases` 的案例放在 `cases` 数组，必须有稳定匿名 `id`、`kind`、时间/文本条件和必要容差。

## 2026-08-12：源码仓库脱敏根提交

- 范围：global，仓库发布边界与 Skill 源码结构。
- 算法版本：3.7。
- 根提交：`4ce42eb Publish sanitized lyric-aligner skill source`。
- 当前 `main` 的普通 Git 历史从该提交开始。
- 仓库所有者已授权重写并清理更早历史；pre-sanitization 的旧 3.7 历史已不在当前仓库可见范围，不能通过普通 Git log 或 checkout 回滚。
- 真实素材继续由 `.gitignore` 隔离在 `private/` 与 `output/`。

## 2026-08-12：P0 契约修复与算法 v3.8

- 日期：2026-08-12
- 范围：global
- 算法版本：3.8
- 提交：待本轮提交

### 问题证据与原因

- 旧 `publish_ready` 允许中低风险候选残留，与模板要求 `review_candidate_count=0` 不一致。
- 旧人工 QA 只绑定源 SRT；音频、LRC、歌单、BPM 或原曲变化后仍可能误复用。
- 文档宣称多个 QA 字段必填，代码却只验证哈希。
- 旧任务没有显式 schema 迁移和恢复备份。
- 验证记录混淆了不同 Python 解释器的依赖状态。
- 生产代码含固定前 8 cue、前 10 秒重叠和具体歌词拼写修补等任务特例。

### 改动

- 新增 schema 2.0 完整任务 manifest、递归目录哈希和任务指纹。
- 所有生产命令要求 `--task-manifest`；中间 JSON 与每一行 CSV 都绑定指纹。
- QA 完整验证 `schema_version`、`artifact_type`、`project`、`source_srt_sha256`、`task_fingerprint_sha256`、`scope`。
- `publish_ready` 改为结构通过且全部候选归零，新增 `structurally_valid`、`fully_reviewed`、`release_status`。
- 新增事务式 `migrate_task.py` 与 `.schema1.bak`。
- 移除任务特例；默认不保留固定 cue，不放行固定开头重叠，不在全局代码修补具体歌词。
- 新增多语言 ASR、语言 profile、可选中日读音层、合成端到端测试、Skill 自校验、CI、私有数据协议和聚合评估器。

### 验证结果

- 2026-08-12，本地 `python -m unittest discover -s scripts -p "test_*.py"`：42 项通过，其中包含完全合成的 `prepare → audio-align → build → finalize → qa` CLI 链路。
- `python -m compileall -q scripts` 通过。
- `python scripts/validate_skill.py .` 在默认 Python 3.12 基础环境通过；该解释器未安装 PyYAML，因此执行基础 frontmatter/YAML 结构检查。
- `py -3.14 scripts/validate_skill.py .` 在 Python 3.14.6、PyYAML 6.0.3 环境通过完整 YAML 解析。
- `skill-creator/scripts/quick_validate.py` 官方快速校验通过。
- 基础环境检查与 ASR 环境必须分别在目标解释器运行；不同解释器不能共享“已安装”结论。
- 两个本地真实任务已非破坏性迁移到 schema 2.0；五份 QA 文件均生成 `.schema1.bak`，manifest 与磁盘输入内容验证一致。这些文件位于 `private/`，未提交。
- `git diff --check` 通过；公开树隐私扫描在最终提交前再次执行。

### 兼容性与回滚

- `refine-asr` 是新主命令，`refine-korean` 保留为别名。
- `validate_korean_asr.py` 保留为旧韩文作业兼容 wrapper。
- 旧 QA 必须迁移，生产命令不静默兼容。
- v3.7 公开根提交仍可检出用于源码级比较；旧私有任务使用 `.schema1.bak` 恢复 QA 文件。更早已清理历史无法通过普通 Git 恢复。

### 升级依据

这些修复影响所有任务的输入身份、发布语义、多语言证据和隐私边界，不能由单个任务 override 安全解决。合成回归避免将任何真实歌词或歌曲硬编码进源码。

## 2026-08-17：v4 Milestone 0-A 发布完整性基础

- 范围：experimental / v4 foundation。
- 分支：`agent/v4-accuracy-foundation`。
- 目标：先消灭 false-ready 与不可追溯产物，再进入 TimeWarp/ASR/Forced Alignment。

### 关键代码变更

- 新增 `lyric_aligner/srt.py`：fail-closed SRT parser、稳定 cue ID、`max(cue.end_ms)` 时间轴终点。
- 新增 `lyric_aligner/qa/final_integrity.py`：最终 SRT 与审计 CSV 逐行严格绑定；QA task fingerprint / algorithm version / release flags 严格校验。
- 新增 `lyric_aligner/contracts/artifacts.py`：stage artifact manifest、output SHA-256、upstream lineage、canonical artifact ID、原子写出。
- 新增 `scripts/v4_validate_release.py`：在 legacy QA 后执行的 v4 release guard。
- 新增 `scripts/test_v4_release_integrity.py`：正文篡改、时间篡改、漏行、跨算法版本、manifest 篡改、坏 SRT、overlay 时间轴负向回归。
- v4 评估器和 Editor Evidence foundation 保留在同一分支，详见 `references/v4-implementation.md`。

### 验证

- `compileall` 通过。
- 相关核心/端到端测试：62 项通过。
- 临时完整仓库测试中的 2 个错误来自缺少根层 `.gitignore` / `references/prompt-template.txt` 的重建环境，不属于算法失败。

### 兼容性与回滚

- 本轮新增 v4 package 与 release guard，不删除 legacy CLI。
- legacy 生产算法尚未强制依赖新 package；因此可单独回滚本轮新增文件而不破坏 v3.x 输入格式。
- 远端尚未包含此前未提交的完整 v3.9 工作树；恢复 legacy v3.9 与 v4 package 接线必须作为独立、可审查 commit 完成。

### 升级依据

- 已复现“最终 SRT 正文变化而旧 report/QA 仍可能显示 ready”的风险；release 产物必须形成不可拆分证据链。
- 该修复与具体歌曲、语言、cue 无关，属于全局生产安全契约。
