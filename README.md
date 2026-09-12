# 视频歌词字幕 / Lyric Aligner

本项目解决“已有 editor/剪映 SRT + 规范歌词 + 最终混音/原曲素材”条件下的歌词字幕修复、对齐、审核与发布门禁问题。目标不是让单一 ASR 直接生成最终字幕，而是把文字真源、时间证据、结构重建、人工确认和发布资格分开，并在证据不足时 fail closed 到 review/BLOCK。

## 当前生产入口

当前仓库主线：`main`；算法版本：`4.0.0a20`。

证据模式按成本和风险递增：

```text
Standard -> Smart -> Pro -> Max
```

产品选择层独立于证据模式：

```text
Smart baseline -> Best-Safe -> Max Release
```

- **Standard**：只修 canonical 文字，冻结 cue 数量、编号、start/end。
- **Smart v1.2.11**：当前 no-audio 默认 selector；沿用 v1.2.10 timing authority，只增加 final canonical ownership / connected lexical-floor hardening。Smart 保持稳定基线职责，不吸收 Best-Safe 的大模型/Max 回灌逻辑。
- **Pro v1.2.7**：只处理 Smart unresolved 的 bounded region；证据仍需 review，不自动把局部声学结果写成最终时间轴。
- **Max 4.0.0a20**：整体 timeline 不可信、cut/overlap/reorder/重复 occurrence 等复杂任务的重路径。
- **Best-Safe 1.1**：最低风险产品 selector，不是新的声学模式。Smart 的 cue 数、顺序、start/end 是默认 topology/timing floor；Max/Pro/ASR/forced-alignment 只提供候选，track-level semantic PASS 不授予整首 timing 或 split/merge/add/delete authority。文字/display 可在不破坏 Smart cue ownership 的前提下经确定性 verifier 安全增强；任何 timing 变化都必须绑定具体 Smart boundary 与独立 authority，`unsupported_timing_change_count` 必须为 0。当前自动 timing promotion 仅开放 human-truth-bound 单边界。
- **Max Release**：只有整份 Max 通过正式 lexical/structural/semantic/release gate 后才取代 Best-Safe。

仓库当前版本与“某个具体任务已可发布”是两件事。Max 可以整体 BLOCK，而 Best-Safe 仍可在不越过证据权限的前提下产出当前条件下风险最低的交付版本；Best-Safe 的 `publish_ready` 也不等于 Max 的 `release_ready`。

## 输入与输出

推荐任务目录：

```text
private/<task>/
├─ input/
│  ├─ source.srt
│  ├─ mix.wav
│  ├─ songs.txt
│  ├─ lyrics/
│  ├─ source-audio/        # Max/声学路径按需
│  └─ bpm.txt              # 可选
└─ qa/
   ├─ task_manifest.json
   ├─ v4_run_config.json
   ├─ *_manual_overrides.json
   ├─ best_safe_timing_truth.json       # 可选，task-bound 独立边界真值
   └─ *_regression_cases.json
```

运行输出统一放在 `output/<task>/`。原始输入、人工结果和既有正式产物不得被新运行覆盖。

## 如何运行

初始化任务与完整参数见 [当前生产工作流](references/workflow.md)。常用入口：

```powershell
python scripts/v4_text_repair.py ...      # Standard
python scripts/v4_smart_repair.py ...     # Smart
python scripts/v4_pro_selective.py ...    # Pro
python scripts/v4_run.py ...              # Max
python scripts/v4_build_best_safe.py ...  # Best-Safe product selector
```

legacy `scripts/redo_karaoke_pipeline.py` 只保留 QA/兼容/历史恢复用途，不是新任务默认入口；`scripts/karaoke_subtitle_pipeline.py` 已是 fail-closed 迁移提示。

## 测试与重跑

基础工程验收：

```powershell
python scripts/validate_skill.py .
python -m unittest discover -s scripts -p "test_*.py"
git diff --check
```

2026-09-12 Best-Safe 1.1 封板时当前主线完整 suite 为 `1882 tests / OK`；Best-Safe focused suite `20/20`、`validate_skill` 与 `git diff --check` 同轮通过。这个数字只描述当时快照，后续以实际重跑结果为准。

真实任务重跑必须继续使用原 manifest/config/input identity；不要为了“跑过”而删除 review、降低阈值或手改 artifact。重跑前后应比较最终 SRT SHA、cue timing signature、canonical coverage、review/release 状态。

## 已验证能力与限制

当前已经可靠具备：

- canonical 文字/顺序约束与 lexical floor；
- Standard 的 timing immutability；
- Smart 的 sequence/BPM text recovery 与 final ownership hardening；
- editor-preservation、cut/overlap/recomposition 等结构链；
- source-clock、ASR、forced-alignment、local acoustic 等证据的版本化 lineage；
- human Gold/Review、calibration/blind、release gate 的可追溯闭环。

仍未证明：任意歌曲/语种无人审核即可稳定交付；对所有未见素材自动 timing 修复优于 editor。2026-09-11 已停止继续调当前 source-ASR+harmonic retrieval 和 English final-mix HuBERTFA timing family；没有新的独立 final-mix truth 或明确 production failure 时，不重新开启同类 heuristic 调参。

详细能力边界见 [capabilities-and-limits.md](references/capabilities-and-limits.md)。

## 不能随意删除的资产

清理规则以 [local-artifact-retention.md](references/local-artifact-retention.md) 为准。默认保护：

- 当前生产输入、task manifest、run config、language/lyric-role/middle-cut 配置；
- authoritative final、QA、release、audit 与其必要 lineage；
- Human Gold、Review、blind/truth、selection lock；
- calibration、regression baseline、评估结果；
- 无法从现有输入和代码确定性重建的人工确认、editor reconciliation、结构证据；
- 被当前文档、测试或发布记录明确引用的本地 evidence。

可优先删除的是 `__pycache__`、`.pytest_cache`、`*.pyc`、明确 disposable cache、一次性 tmp/debug/Playwright 产物。不要仅凭日期、版本号或目录大就删除 `output/` / `private/`。

## 文档入口

- 当前生产流程：[references/workflow.md](references/workflow.md)
- 当前能力与限制：[references/capabilities-and-limits.md](references/capabilities-and-limits.md)
- Smart / Pro 契约：[references/smart-pro-v1-1.md](references/smart-pro-v1-1.md)
- Max runtime：[references/v4-runtime-guide.md](references/v4-runtime-guide.md)
- CLI 契约：[references/v4-cli-contract.md](references/v4-cli-contract.md)
- 当前状态：[references/v4-status.md](references/v4-status.md)
- Best-Safe 1.1 源码冻结：[references/releases/prod-v4.0.0a20-best-safe-v1.1.0-20260912.json](references/releases/prod-v4.0.0a20-best-safe-v1.1.0-20260912.json)
- 历史变更：[references/v4-change-record.md](references/v4-change-record.md)
- 实验台账：[references/accuracy-experiment-register-2026-09-08.md](references/accuracy-experiment-register-2026-09-08.md)

历史 handoff/实验文档用于复现和追溯，不应被解释为当前待办。后续模型应从本 README、`SKILL.md` 和 `references/workflow.md` 开始。