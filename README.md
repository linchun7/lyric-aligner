# 视频歌词字幕 / Lyric Aligner

已有 editor/剪映 SRT 是生产底稿，canonical 是默认文字真源。目标是正确歌词、可靠归属和不破坏现有正确时间轴，而不是让一个 ASR 生成最终字幕。

## 当前生产入口

当前仓库主线：`main`；算法版本：`4.0.0a20`。正常生产只走 **Safe Final / 最低下限最终版**，时间轴事故才进入 **Max Recovery**。这两个名称描述交付流程，不新增产品模式或 `--mode` 参数。

```text
Safe Final
  Editor SRT + canonical
    -> Standard / Smart stable floor
    -> 语言感知候选扫描 -> GPT 文字 / 发音 / occurrence 审定
    -> 必要时局部音频 -> reviewed canonical splices
    -> Gap Rescue review-only -> QA
    -> GPT 全量语义敏感词审查 -> mask-only finalizer -> Safe Final

Max Recovery（仅在 editor timeline 不可信时）
  cut / reorder / overlap / 大范围 occurrence 损坏
    -> Max 事故恢复 -> 原有结构、语义和发布门禁
```

固定优先级：**文字正确 > 结构/归属正确 > 时间不退化 > 时间改善**。文字审定不授予时间权限；canonical 换行也不等于 cue 边界。已有已审定韩文修复应从当前 floor 继承，而不是回到原始 Smart 覆盖它。

内部继续保留 Standard、Smart v1.2.11、Pro v1.2.7、Max 4.0.0a20 和 Best-Safe 1.1。Standard 只修文字、冻结编号/时间/分段；Smart 是稳定 no-audio baseline；Pro 仅提供 unresolved 局部证据；Best-Safe 保持既有低风险选择和独立边界真值门禁。**不是 Standard → Smart → Pro → Max 的必跑升级链，也不默认整首运行 Max。** Max 整体 BLOCK 不妨碍保留已经可信的 floor，Best-Safe `publish_ready` 不等于 Max `release_ready`。

未知韩语歌曲中出现 Latin token 只触发候选，不能自动替换为韩文。GPT 必须区分真实英文、混唱、衬词、谐音、hallucination 和 occurrence 不确定；确认多少字符就改多少字符。中文同音错字、漏字/多字也走同一字符片段入口；严格简繁比较不能改写 canonical display。

候选扫描与 Gap Rescue 契约见 [Safe Final review](references/safe-final-review.md)，文字物化沿用 [reviewed canonical splices](references/reviewed-canonical-splices.md)。Gap 当前只有 detector + review ledger + verifier，**没有插入新 cue 的 production authority**；缺少时间真值时保留 floor，不假装补全完成。

### 默认语言路径

- **一般中文**：优先 `Standard stable floor -> Safe Final`。editor 时间可信时只修文字；出现重复副歌、拆句/合句、occurrence 或 ownership 不确定时再使用 Smart 作为稳定 floor。
- **一般韩文 / K-Pop**：优先 `Smart stable floor -> Safe Final`。重点处理 pseudo-English、韩英混唱、真实英文保留和重复 occurrence；Latin token 只触发 review，不自动改成 Hangul。
- **Pro**：只为少量高风险 cue 提供局部音频/声学证据，不是必须经过的成品版本。
- **Max Recovery**：仅整体 timeline 明显损坏时使用，不因语种自动进入。

Balanced / Fluent 已撤出当前产品层；不存在需要在“最低下限版”和“平衡版”之间二选一的正常生产流程。

历史五批 `2026-09-13.5` 冻结交付和其任务审定仍保留；GPT 审定不冒充人工 Gold。历史回放只能证明兼容/不退化，不能作为新 blind Gold 或未知歌曲正确率；逐条证据见 [安全增量交付](references/smart-safe-incremental-delivery.md)。

## 输入与输出

```text
private/<task>/
├─ input/
│  ├─ source.srt
│  ├─ mix.wav                 # 局部音频或 Max Recovery 按需
│  ├─ songs.txt
│  ├─ lyrics/
│  ├─ source-audio/           # Max/声学路径按需
│  └─ bpm.txt                 # 可选
└─ qa/
   ├─ task_manifest.json
   ├─ v4_run_config.json
   ├─ *_manual_overrides.json
   ├─ best_safe_timing_truth.json   # 可选，task-bound 独立边界真值
   └─ *_regression_cases.json
```

运行输出统一放在 `output/<task>/`。原始输入、人工结果和既有正式产物不得被新运行覆盖。Safe Final 默认不整首重跑 ASR；仅高风险文字、伪英文、gap 和 occurrence 冲突按需使用局部 `faster-whisper large-v3-turbo`。同模型多窗口、多 hint、多 decode 属于相关观察，不计为独立真值。不自动恢复其他 ASR/forced-aligner backend。

## 如何运行

试听和人工复核唯一默认 **3.2**，双击根目录 **`试听复核.cmd`**。Safe/R5、Max、Gold、转场/ownership 全部复用原 `v4_human_boundary_anchor_audit_ui.py`，保留原有试听操作；字幕对照直接显示文字。首次选择复核包用 `python scripts/v4_listen.py --pack-dir "<pack>" --set-default --check`，旧页面仅 legacy 兼容，不再新造。冻结点、入口和废弃清单见 [统一 3.2](references/listening-ui.md)。

初始化任务与完整参数见 [当前生产工作流](references/workflow.md)。正常流程：

```powershell
python scripts/v4_text_repair.py ...      # Standard：可信 editor 时间，只修文字
python scripts/v4_smart_repair.py ...     # Smart：需要既有 no-audio 稳定基线时
python scripts/v4_review_safe_final.py scan --plan PLAN.json --out CANDIDATES.json
python scripts/v4_apply_reviewed_text_splices.py --plan REVIEWED_PLAN.json --review REVIEW.json --out-dir NEW_FINAL
python scripts/v4_review_safe_final.py verify-gaps --pack CANDIDATES.json --review GAP_REVIEW.json --out GAP_QA.json
python scripts/v4_review_safe_final.py sensitive-pack --source-srt PRE_FINAL.srt --song-list private/<task>/input/songs.txt --out SENSITIVE_PACK.json
# GPT 必须完整阅读 SENSITIVE_PACK.json，生成 task-bound SENSITIVE_REVIEW.json
python scripts/v4_review_safe_final.py sensitive-finalize --pack SENSITIVE_PACK.json --review SENSITIVE_REVIEW.json --out-srt FINAL.srt --out-audit FINAL.sensitive.json
```

`scan` 接收同一 splice plan，可令 `proposals=[]`；如需 gap 时间审定，在扫描时加 `--final-mix mix.wav` 绑定最终混音，但不解码。GPT 在审定后另存新的 plan/review；程序不自由生成歌词。候选/QA JSON 均拒绝覆盖已有文件。

所有真实任务在文字与 QA 收敛后都必须执行 `sensitive-pack -> GPT 全量语义审查 -> sensitive-finalize`。规则词表只提供候选提示，不能直接改字幕；GPT 必须结合完整 cue、前后语境、歌曲/歌词语义决定 `KEEP / MASK / REVIEW`，并可主动发现规则未提示的问题。存在任何 `REVIEW`、候选漏审、stale SRT/hash 或自由改写歌词时 finalizer fail closed，不生成正式 Safe Final。只有 `sensitive-finalize` 的 mask-only 输出才是正常流程的最终发布字幕。

`v4_pro_selective.py`、`v4_run.py`、`v4_build_best_safe.py` 是按需局部证据/事故恢复/既有 selector 入口，不是正常生产必跑步骤。legacy `scripts/redo_karaoke_pipeline.py` 仅保留 QA/兼容/历史恢复用途；`scripts/karaoke_subtitle_pipeline.py` 是 fail-closed 迁移提示。

## 测试与重跑

```powershell
python scripts/validate_skill.py .
python -m unittest discover -s scripts -p "test_*.py"
git diff --check
```

验收数量对应具体源码快照，以 [当前状态](references/v4-status.md) 和原始日志为准；不把测试数、review 数下降、Hangul 字数增加或字符距离当作字幕 Gold。2026-09-12 source freeze `prod-v4.0.0a20-best-safe-v1.1.0-20260912` 不移动。P1 boundary promotion 继续 shadow/evaluation-only，详见 [P1 契约](references/boundary-promotion-p1.md)。

真实任务重跑保持原 manifest/config/input identity；比较最终 SRT SHA、cue timing signature、canonical coverage、review/release 状态。不删 review、不降阈值、不手改 artifact 以制造通过。

## 能力边界与资产保护

仍未证明任意歌曲/语种无人审核即可稳定交付，也未证明对所有未见素材自动 timing 修复优于 editor。source-ASR+harmonic retrieval 和 English final-mix HuBERTFA timing family 已停止调参；没有新的独立 final-mix truth 和明确 production failure，不恢复这些方向。Balanced/Fluent 不恢复；不新建模式、selector、ASR backend 或试听 UI。

清理以 [模块生命周期](references/module-lifecycle.md) 和 [资产保留规则](references/local-artifact-retention.md) 为准。保护生产输入、manifest/config、authoritative final/QA/release、human Gold/review、blind/truth/selection lock、regression baseline、不可重建人工证据和必要 release/hash lineage。仅在没有 current consumer、历史复现不依赖且无 lineage 需要时删除源码。不要因日期、文件大小或实验名称删除 `output/`、`private/`。

仓库隐私边界：通用仓库只提交代码、通用文档和脱敏 synthetic fixtures。真实歌曲/歌词、SRT、音频、任务级 Gold/review/QA、`private/`、`output/`、`.ai-bridge/`、绝对本地路径、用户名和凭据必须保持 ignored/untracked；提交前运行 `python scripts/privacy_scan.py`。第三方依赖自带的作者/许可证归属信息不属于用户隐私，不应误删。

## 文档入口

- 生产流程：[workflow.md](references/workflow.md)；能力边界：[capabilities-and-limits.md](references/capabilities-and-limits.md)。
- Safe Final 审定：[safe-final-review.md](references/safe-final-review.md)；文字物化：[reviewed-canonical-splices.md](references/reviewed-canonical-splices.md)。
- 内部实现：[Smart / Pro](references/smart-pro-v1-1.md)、[Max runtime](references/v4-runtime-guide.md)、[CLI 契约](references/v4-cli-contract.md)。
- 状态与追溯：[v4-status.md](references/v4-status.md)、[v4-change-record.md](references/v4-change-record.md)、[实验台账](references/accuracy-experiment-register-2026-09-08.md)。
- 源码冻结：[Best-Safe 1.1 release](references/releases/prod-v4.0.0a20-best-safe-v1.1.0-20260912.json)；[P1 shadow](references/boundary-promotion-p1.md)。

历史 handoff/实验文档用于复现和追溯，不是当前待办。后续模型从本 README、`SKILL.md` 和 `references/workflow.md` 开始。
