# Lyric Aligner v4 实施记录与关键代码说明

> 本文是 v4 的**实际实施文档**，不是愿景文档。每次进入生产分支的关键算法、契约、兼容性变化和验证结果都必须在这里记录。

## 1. 实施原则

v4 采用 Accuracy-First、可回滚的里程碑方式推进，不一次性重写现有 CLI。当前优先级：

1. 消灭 false-ready：错误终稿不能通过发布门禁。
2. 消灭输入错绑：错误/歧义 LRC、原曲不能静默进入生产。
3. 让评估指标真实反映乱序、漏行、额外行和边界误差。
4. 再投入 Affine-first TimeWarp、多特征音频映射、跨曲时间轴和 Forced Alignment。

旧 `scripts/redo_karaoke_pipeline.py` 暂时作为兼容生产入口；新的可信性、领域模型和算法逐步进入 `lyric_aligner/` 包，待覆盖率和 blind-test 达标后再由旧 CLI 转发。

## 2. 基线状态与迁移说明

GitHub 当前远端 HEAD 仍是 v3.8 基线；此前已经完成并验收的 v3.9 工作树没有形成 Git commit。已从任务材料中恢复 v3.9 生产脚本与测试到本地验证目录，确认其包含：

- `review-audio-edits` 中段剪切复核链；
- 多个 confirmed cut 的投影；
- 连续分段变速；
- Enhanced LRC/QRC 词级时间；
- 跨歌曲叠唱候选与精确允许区间；
- v3.9 端到端发布门禁测试。

由于当前 GitHub 连接器仅支持整文件替换，而 v3.9 主脚本约 4988 行，本轮不冒险通过连接器手工搬运大文件。v4 新核心先以独立包落地；最终把 v3.9 legacy 主脚本恢复/接线到 v4 package 时，应在可直接操作 Git 工作树的本地 Codex 环境完成，并保留独立 commit。

## 3. Milestone 0-A：发布完整性与 false-ready 修复

### 3.1 新增 `lyric_aligner/srt.py`

用途：提供生产级 fail-closed SRT 解析与稳定 cue 身份。

关键行为：

- 非空坏 block 立即 `SRTParseError`，不再静默跳过；
- cue number 必须为正数且唯一；
- `end_ms > start_ms`；
- 空正文直接拒绝；
- 时间轴结束统一使用 `max(cue.end_ms)`，避免 overlay SRT 文件顺序乱序时被 `cues[-1]` 截断；
- `cue_id` 由 position、cue number、start/end、正文 SHA-256 共同生成。

关键接口：

```python
parse_srt_strict(path) -> list[Cue]
timeline_end_ms(cues) -> int
text_sha256(text) -> str
cue_id(position, cue) -> str
```

### 3.2 新增 `lyric_aligner/qa/final_integrity.py`

用途：修复“最终 SRT 被篡改但旧审计 CSV/QA 仍可 `publish_ready=true`”的 false-ready 路径。

`validate_srt_report_binding()` 会对最终 SRT 与审计 CSV 做**双向严格集合绑定**：

- cue 数量必须一致；
- CSV 行顺序必须与最终 SRT 一致；
- `start_ms/end_ms` 必须逐行一致；
- 最终正文必须逐行一致；
- 若 CSV 已包含 `cue_id`/`text_sha256`，必须再次验证；
- report 中 `task_fingerprint_sha256` 必须全部等于当前任务指纹。

因此以下任一变化都会 BLOCK：

- 改一个歌词字；
- 改一个 cue 的起止时间；
- 删除/新增一条 CSV 或 SRT cue；
- 把别的任务 report 与本任务 SRT 拼接。

`validate_qa_payload()` 额外要求：

```text
passed=true
structurally_valid=true
fully_reviewed=true
publish_ready=true
review_candidate_count=0
QA.task_fingerprint == task fingerprint
QA.algorithm_version == requested algorithm version
```

### 3.3 新增 `lyric_aligner/contracts/artifacts.py`

用途：建立阶段产物 lineage，而不仅是 task fingerprint。

release artifact manifest 绑定：

```text
task_fingerprint
stage
algorithm_version
normalized_config
producer/git commit
dependency revisions
model revisions
upstream_artifact_ids
output path + size + SHA-256
integrity evidence
```

`artifact_id` 是上述 canonical JSON 的 SHA-256。修改 manifest 内任何受保护字段都会使 `artifact_id` 校验失败。

关键接口：

```python
build_artifact_manifest(...)
validate_upstream_artifact(...)
atomic_write_json(...)
```

`atomic_write_json()` 使用同目录临时文件 + `os.replace()`，避免直接写一半留下“看似存在”的新 manifest。

### 3.4 新增 `scripts/v4_validate_release.py`

这是 Milestone 0 期间的兼容 release guard。现有 v3.x `qa` 通过后，必须再执行：

```powershell
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/<任务>_FINAL.srt" `
  --report "output/<任务>/<任务>_FINAL_审计.csv" `
  --qa-json "output/<任务>/<任务>_FINAL_QA.json" `
  --algorithm-version "3.9" `
  --git-commit "<commit>" `
  --out-manifest "output/<任务>/<任务>_RELEASE_ARTIFACT.json"
```

只有该命令成功并生成 artifact manifest，才算 v4 意义上的可发布。

后续当 legacy CLI 接线完成后，这一步应由 `qa/release` 自动执行，不再要求人工单独调用。

## 4. 已完成的 v4 Foundation

### 4.1 评估器

`evaluate_dataset.py` 已增加顺序敏感指标，解决整份字幕 Counter 交集掩盖乱序/漏行的问题：

- sequence-aware unit error rate；
- line exact precision/recall/F1；
- missing/extra line；
- 旧指标保留用于历史比较，但不再单独代表正确率。

已加入对抗测试：10 条 reference 只输出 1 条，exact recall 必须为 0.1；相同词集合但顺序反转，不允许 sequence 指标满分。

### 4.2 Editor Evidence / 多语言基础

新增 `scripts/editor_evidence.py`，并扩展语言 profile：

- `zh/en`：允许 direct editor text；
- `ko/ja`：剪映文本只作 phonetic hint；
- `yue`：默认 timing hint，不把剪映普通话/英语误识直接当 canonical；
- `auto/generic`：ASR 使用语言检测，剪映文本不具备 canonical 决策权。

当前仅建立策略层；尚未把该权重全面接入 legacy `redo_karaoke_pipeline.py` 的生产打分。这一接线属于 Milestone 1/2，必须在 v3.9 基线恢复后完成。

## 5. 测试结果

2026-08-17 本地合并验证：

```text
python -m compileall -q lyric_aligner scripts
PYTHONPATH=.:scripts python -m unittest \
  scripts.test_v4_release_integrity \
  scripts.test_v4_accuracy_foundation \
  scripts.test_evaluate_dataset \
  scripts.test_redo_karaoke_pipeline \
  scripts.test_end_to_end
```

结果：**62 tests passed**。

新增负向测试覆盖：

1. 最终 SRT 正文篡改 -> BLOCK；
2. 最终 SRT/CSV 时间不一致 -> BLOCK；
3. SRT/CSV 行数不一致 -> BLOCK；
4. v3.9 QA 被 v4 algorithm-version release 请求复用 -> BLOCK；
5. artifact manifest 内容被篡改 -> BLOCK；
6. 坏 SRT block -> 立即失败；
7. overlay cue 文件顺序乱序 -> timeline end 使用 max end，而非最后 block。

完整临时仓库测试曾出现 2 个 FileNotFoundError，仅因为本地重建目录没有复制 `.gitignore` 与 `references/prompt-template.txt`，不是算法失败。

## 6. 下一步实施顺序

### Milestone 0-B

- 把 strict SRT / final integrity / artifact lineage 接入正式 legacy release flow；
- 禁止跨算法版本 audio alignment / mapping / QA 静默混用；
- 把 FINAL SRT、FINAL audit、FINAL QA 一次性绑定进 release manifest；
- 补半写入/异常中断测试。

### Milestone 1

- `TrackAsset + TrackOccurrence`；
- `track_assets.json`；
- LRC/source audio resolver 改为 threshold + top1/top2 margin + uniqueness 的 fail-closed；
- LRC 同时间戳 original/translation/romanization/pronunciation/metadata/unknown 角色分类；
- `unknown` canonical role 阻止生产。

### Milestone 2

- Affine-first `TimeWarp`；
- BPM 从硬 slope / 全曲 time-stretch 降为 soft prior；
- waveform NCC 保留，同时引入 harmonic/chroma coarse evidence；
- top1/top2 margin 与 feature agreement；
- 只有 AFFINE 出现系统 drift 且 piecewise 显著改善时才升级 `PIECEWISE_RATE`；
- middle-cut 声明只改变搜索策略，不等于自动确认 cut。

## 7. 文档要求

从本文件建立后，以下类型改动必须同步文档：

- 新领域对象/JSON schema；
- 生产 CLI 行为变化；
- 算法关键公式、阈值、fallback；
- release gate / QA 规则变化；
- 模型、依赖、backend 变化；
- 兼容性与迁移方式；
- 新增/删除的关键测试；
- blind-test / calibration 结论。

禁止只在 commit message 或对话里留下关键设计决定。
