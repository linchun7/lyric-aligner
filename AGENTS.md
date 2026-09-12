# 字幕项目开发入口

- 执行真实字幕任务先读 [SKILL.md](SKILL.md)，按 Standard / Smart / Pro / Max 选择证据与能力。时间轴冻结要求优先 Standard，不因语种直接升级 Max。Best-Safe 是证据模式之后的产品 selector，不是第五种取证模式；Best-Safe 1.1 默认以 Smart cue topology/start/end 为不可静默退化的 timing floor，Max/semantic PASS 只产生候选，未经 boundary-level 独立 authority 不得改写 timing/topology，也不得为 Best-Safe 改写 Smart/Max authority。
- 当前开发入口以 [README.md](README.md)、[SKILL.md](SKILL.md) 和 [references/workflow.md](references/workflow.md) 为准。2026-09-11 已停止继续调当前 source-ASR+harmonic retrieval 与 English final-mix HuBERTFA timing family；没有新的独立 final-mix truth 或明确 production failure 时，不重新开启同类 accuracy heuristic。旧 [next-stage Max handoff](references/next-stage-max-expected-loss-handoff-2026-09-07.md) 仅作历史研究记录。
- 开发前检查 `git status --short --branch -uall` 和 `git worktree list`。已修改及未跟踪的 boundary 工作属于现有任务，不覆盖或清除。
- canonical lyric 是文字与顺序的默认真源，但不是不可反驳：只有独立的 `canonical-semantic-rebuttal` 证据链可以改变 normalized lexical truth；普通 display override 只能做空格/标点/大小写等 presentation-equivalent 变化。line break 不能直接等同 cue boundary。无法证明安全的结果保留 review/BLOCK，不手工改 artifact 绕过 lineage。
- 产品版本、策略身份、artifact schema 是不同概念。新产物身份从对应实现的版本定义生成，保留旧产物读取兼容；不得批量改写历史版本标识。
- 产物清理遵循 [references/local-artifact-retention.md](references/local-artifact-retention.md)。生产 final、人工确认、QA/release、blind/truth 和被引用的证据默认保留。
- 基础检查：`python scripts/validate_skill.py .`、`python -m unittest discover -s scripts -p "test_*.py"`。文档契约按 CI 的实际 base/head 运行；未提交修改不能由只比较提交的检查证明通过。
- 使用 CI 定义的 Python/依赖环境。纯版本或文档修改不触发真实媒体处理、模型下载或生产产物重建；按改动运行相关测试并记录未覆盖部分。
