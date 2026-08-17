# Lyric Aligner v4 实施记录与关键代码说明

> 本文记录**已经实际实现的 v4 代码、契约、算法、兼容性和验证结果**。愿景/评估见单独架构文档；任何关键生产变更不得只留在 commit message 或对话中。

## 1. 实施原则

v4 采用 Accuracy-First、可回滚里程碑，不一次性重写现有 CLI。

优先级：

1. 消灭 false-ready：错误终稿不能通过发布门禁；
2. 消灭输入错绑：错误/歧义 LRC、原曲不能静默进入生产；
3. 指标真实反映乱序、漏行、额外行、边界和 cut/overlap；
4. 再投入 Affine-first TimeWarp、多特征音频检索、跨曲时间轴和 Forced Alignment。

旧 `scripts/redo_karaoke_pipeline.py` 暂时保留为兼容生产入口；新的可信性、领域模型和算法进入 `lyric_aligner/` 包，待基线、盲测和接线完成后再由旧 CLI 转发。

## 2. 基线状态与迁移约束

GitHub 远端当前 legacy 主脚本仍是已提交 v3.8；此前实际使用并验收的 v3.9 工作树没有形成 Git commit。已经从历史任务材料恢复 v3.9 脚本和测试到本地验证目录，确认包含：

- `review-audio-edits` 中段剪切复核链；
- 多个 confirmed cut；
- 连续分段变速；
- Enhanced LRC/QRC 词级时间；
- 跨曲叠唱候选/确认区间；
- v3.9 end-to-end 发布门禁。

当前 GitHub 连接器适合小文件/模块写入，但整文件替换 4988 行 legacy 脚本风险较高。因此 v4 新核心先以独立 package 落地；**恢复完整 v3.9 legacy 文件并把 legacy CLI 接到 v4 package** 应在可直接操作 Git 工作树的本地 Codex 环境作为独立 commit 完成。

---

# 3. Milestone 0-A：发布完整性与 false-ready 修复

## 3.1 `lyric_aligner/srt.py`

生产级 fail-closed SRT parser：

- 非空坏 block 立即 `SRTParseError`，不静默跳过；
- cue number 必须为正且唯一；
- `end_ms > start_ms`；
- 空正文拒绝；
- 时间轴结束统一为 `max(cue.end_ms)`，修复 overlay SRT 文件顺序乱序导致 `cues[-1]` 截断最后一曲的问题；
- `cue_id` 绑定 position、cue number、start/end、正文 SHA-256。

关键接口：

```python
parse_srt_strict(path) -> list[Cue]
timeline_end_ms(cues) -> int
text_sha256(text) -> str
cue_id(position, cue) -> str
```

## 3.2 `lyric_aligner/qa/final_integrity.py`

修复“最终 SRT 已被改错，但旧审计 CSV/QA 仍可能 `publish_ready=true`”的 false-ready 路径。

`validate_srt_report_binding()` 强制：

- SRT cue 数量 = CSV 行数量；
- 顺序一致；
- `start_ms/end_ms` 逐行一致；
- 最终正文逐行一致；
- 若 CSV 含 `cue_id/text_sha256`，再次验证；
- report 所有 `task_fingerprint_sha256` 必须等于当前任务。

以下任一变化必定 BLOCK：歌词正文、cue 时间、cue/CSV 行数、顺序或任务指纹。

`validate_qa_payload()` 还要求：

```text
passed=true
structurally_valid=true
fully_reviewed=true
publish_ready=true
review_candidate_count=0
QA.task_fingerprint == current task
QA.algorithm_version == requested algorithm version
```

## 3.3 `lyric_aligner/contracts/artifacts.py`

新增阶段产物 lineage。Artifact Manifest 绑定：

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

`artifact_id` 为 canonical JSON SHA-256；任何受保护字段被改动都会使校验失败。

`atomic_write_json()` 使用同目录临时文件 + `os.replace()`，避免半写入 manifest。

## 3.4 `scripts/v4_validate_release.py`

Milestone 0 期间作为 legacy QA 之后的额外 release guard：

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

未来 legacy CLI 接线后，应由正式 release flow 自动执行。

---

# 4. Foundation：可信评估器与 Editor Evidence

## 4.1 Sequence-aware evaluator

`evaluate_dataset.py` 已增加：

- sequence-aware unit error rate；
- line exact precision/recall/F1；
- missing/extra line；
- 原 legacy 指标保留用于历史对比，但不再单独代表准确率。

对抗回归包括：10 条 reference 只输出 1 条时 exact recall=0.1；相同 token 集合但顺序反转不能拿 sequence 满分。

## 4.2 Editor Evidence / 多语言基础

新增 `scripts/editor_evidence.py` 并扩展语言 profile：

- `zh/en`：允许 direct editor text；
- `ko/ja`：剪映文本只作 phonetic hint；
- `yue`：默认 timing hint；
- `auto/generic`：ASR 使用语言检测，剪映文字没有 canonical 决策权。

**状态：策略层已实现/测试，尚未全面接入 legacy 生产打分。**

---

# 5. Milestone 1-A：TrackAsset / TrackOccurrence 与 fail-closed resolver

## 5.1 领域对象

新增 `lyric_aligner/domain.py`：

```text
TrackAsset
  track_id
  artist/title/version_id
  source_audio_path + SHA-256
  canonical_lyric_path + SHA-256
  language

TrackOccurrence
  occurrence_id
  track_id
  ordinal
  nominal_start_ms
  middle_cut=false|true|unknown
  language_profile
  active_intervals
```

`track_id` 表示具体录音+歌词资产；`occurrence_id` 表示该资产在混音中的一次出现。

因此：同一首歌重复出现 -> 共用 TrackAsset、不同 occurrence；不同艺人/版本/录音 -> 不得因为标题相同而串证据。

## 5.2 `lyric_aligner/assets/resolver.py`

旧行为“没有明确命中就取最相似文件”改成 fail-closed：

- top1 必须达到 `min_score`；
- top1-top2 margin 必须达到 `min_margin`；
- Artist+Title 精确命中最强；
- 标题相同但艺人不同主动降权；
- 同一个 LRC/原曲文件不能被两个不同 TrackAsset 静默复用；
- manifest 记录 top1/top2/margin 供审计。

当前 bootstrap 默认：

```text
min_score = 0.76
min_margin = 0.08
```

**这些不是已校准阈值。** 必须在 calibration/blind-test 后重新确定，不能长期作为经验真理。

新增 CLI：`scripts/v4_resolve_assets.py`，输出显式 `track_assets.json` 风格 JSON。

## 5.3 middle_cut 语义

`middle_cut` 在 TrackOccurrence 只表示**搜索策略先验**：

- `false`：默认不主动寻找/确认中间 cut；若后续出现强 source discontinuity，必须 BLOCK；
- `true`：允许重点寻找 cut，但不能自动 confirmed；
- `unknown`：可生成候选，但必须 review。

trim-start/trim-end 不属于 middle cut。

## 5.4 `lyric_aligner/assets/lyric_roles.py`

目标不是强行识别 translation/romanization，而是保证 canonical original 唯一。

规则：

- 每个 timestamp group 必须恰好有一个 `original`；
- 单行 group 直接 original；
- 已知 `ko/ja/zh/yue/en` 时，可用脚本类型确认唯一 native original；
- 其他同行保留 `unknown`，例如韩文旁边的拉丁行不会擅自判为“罗马音”或“英文翻译”；
- 两条都可能是 native original（例如两条汉字版本）或 `auto` 下多候选无法判断时直接 BLOCK；
- 后续通过显式 role mapping 解决必要人工选择，不加歌曲级硬编码。

这一步消除旧逻辑“同时间戳第一行就是原文”的隐患。

---

# 6. Milestone 2-A：Affine-first TimeWarp 核心

新增 `lyric_aligner/audio/timewarp.py`。该层只消费已经得到的 Source↔Mix anchor，不负责音频 feature extraction；这样可先独立验证模型选择，再接 waveform/chroma/MFCC backend。

## 6.1 Anchor

```python
AlignmentAnchor(
    mix_time,
    source_time,
    confidence,
    feature_scores={"waveform": ..., "chroma": ...}
)
```

## 6.2 AFFINE 快路径

```text
source_time = intercept + slope * mix_time
```

所有 occurrence 必须先拟合 AFFINE，并输出：

- anchor/inlier count；
- coverage；
- median/P95 residual；
- early/middle/late drift；
- independent feature family count/agreement；
- BPM prior 与 fitted slope 差值。

AFFINE 能解释数据时，禁止升级复杂模型。

## 6.3 BPM 只是软先验

BPM prior 只通过弱 slope regularization 进入拟合；不能固定最终 slope，也不要求先对整首原曲固定 `time_stretch`。

回归测试故意设置：真实 anchor slope=1.30，BPM prior=1.05；最终 slope 仍必须接近 1.30。

## 6.4 PIECEWISE_RATE

当前实现使用连续 hinge basis：

```text
source_time = intercept + base_slope * mix_time
            + Σ delta_slope_i * max(0, mix_time - breakpoint_i)
```

这保证 change point 前后 **source position 连续，只改变 slope**。

升级条件同时要求：

1. AFFINE 存在系统 residual/drift/coverage 问题；
2. piecewise 在复杂度惩罚后仍显著优于 AFFINE；
3. 至少两个独立 feature family 支持；
4. 所有 segment slope > 0；
5. 没有被识别为 source discontinuity 的跳跃。

当前最多两个 breakpoint（最多三段局部倍率），足以覆盖例如 `1.08 -> 1.17 -> 1.43` 的基础场景；后续是否扩展由 calibration 决定，不以更多分段换表面低 residual。

## 6.5 Rate change 与 Cut 严格分离

- slope 改变但 source position 连续：`PIECEWISE_RATE`；
- 连续 mix 时间上 source position 明显向前跳：discontinuity candidate；
- `middle_cut=false`：`unexpected_middle_discontinuity` + BLOCK；
- `middle_cut=true/unknown`：review candidate + BLOCK，**永不自动 confirmed**；
- source 向后跳默认 BLOCK，除非未来显式支持 source reorder。

当前 bootstrap 阈值包括 `max_continuous_rate=2.0`、`min_excess_source_jump=1.5s` 以及 residual/drift/improvement threshold；全部必须在真实 calibration 数据上重定。

---

# 7. 测试状态

2026-08-17：

### Milestone 0 核心 + v3.9 恢复工作树 end-to-end 组合

```text
62 tests passed
```

覆盖 release integrity、Evaluator、legacy v3.9 middle-cut/variable-speed/overlap、端到端链路。

### Milestone 1-A resolver + lyric role foundation

```text
23 tests passed
```

覆盖：

- 完全无关单一文件不能被自动绑定；
- 近似 live/studio 候选 margin 不足必须 BLOCK；
- 同歌重复 occurrence 共用 asset 但 occurrence_id 不同；
- 同标题不同艺人不能串文件；
- generic 同名文件不能被两个不同资产身份静默复用；
- 韩文原文+拉丁同行只确认 native original；
- 两条都可能是中文原文时 BLOCK；
- auto 多候选时 BLOCK。

### 加入 TimeWarp 2-A 后的 foundation 组合

```text
30 tests passed
```

新增验证：

- 普通固定倍速保持 AFFINE；
- 错 BPM prior 不锁死真实 slope；
- `1.08 -> 1.17 -> 1.43` 可升级连续 piecewise、无 cut；
- abrupt rate change 不等于 cut；
- 未声明 middle jump -> BLOCK；
- 声明 middle cut -> 仍只是 review/BLOCK；
- 没有两个独立 feature family 时不接受 piecewise。

此前完整临时仓库测试出现的 2 个 FileNotFoundError 是重建目录未复制 `.gitignore` 与 `references/prompt-template.txt`，不是算法失败。

---

# 8. 当前尚未完成 / 下一步

## Milestone 0-B

- 恢复完整 v3.9 legacy 工作树到 Git；
- legacy release flow 自动调用 strict SRT/final integrity/artifact lineage；
- 禁止跨算法版本 alignment/mapping/QA 静默混用；
- 补半写入/异常中断事务测试。

## Milestone 1-B

- 把 `track_assets.json` 纳入 task/artifact lineage；
- 增加显式 role mapping，处理“原文与翻译同脚本”的必要人工选择；
- 增加编码 preflight：默认 UTF-8，低置信兼容编码必须显式声明/BLOCK；
- Editor Evidence profile 绑定到 occurrence，而不是只依赖 ASR job。

## Milestone 2-B

- coarse audio retrieval：保留 waveform NCC，引入 HPSS harmonic + Chroma CENS/CQT；
- 候选保存 top1/top2 margin 和跨 feature agreement；
- 用真实 audio anchors 接入 `TimeWarp`，替代 legacy “全曲 BPM time_stretch + 单一 NCC”主路径；
- fine alignment 仅在低 margin、AFFINE drift、transition/cut boundary 运行；
- 用 calibration/blind-test 确定 TimeWarp/discontinuity 阈值。

之后才进入多轨 transition timeline、canonical fragment、source-side Forced Alignment 和 ASR evidence v2。

---

# 9. 强制文档规则

以下变更必须同步本文或对应专题文档：

- 新领域对象/JSON schema；
- 生产 CLI 行为；
- 关键公式、阈值和 fallback；
- release gate/QA 规则；
- 模型、依赖、backend；
- 兼容性、迁移、回滚；
- 新增/删除的关键测试；
- calibration/blind-test 结论。

禁止只在代码注释、commit message 或聊天中留下关键设计决定。
