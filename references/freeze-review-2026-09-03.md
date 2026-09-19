# Freeze Review — 2026-09-03

## 审计范围

入口/legacy、版本身份与 selector 真源、异常/断言、path ownership、task manifest / run-config / fingerprint、artifact lineage 与 release authority、依赖/环境、随机性/确定性、正式输出与 identity JSON 写入原子性、privacy、tests hollow-green、docs drift、artifact retention、structural/ablation closeout。

## 实际修复

- 补齐 runtime base 的 `soundfile` direct dependency，并让环境预检与 repository contract 明确覆盖它；requirements / environment 变化纳入 documentation contract。
- TrackAssets manifest 改用已有 shared `atomic_write_json()`；payload/schema/asset selection 不变。
- task manifest / QA JSON 与 task-local `v4_run_config.json` 原先虽然名为 atomic writer，但使用固定 `<name>.tmp`；现统一复用唯一同目录 tempfile + flush/fsync + `os.replace` 的 shared writer，消除并发写临时名竞争，不改变 JSON payload、task fingerprint 或 semantic fingerprint。
- canonical evaluation render 的 SRT 与 audit CSV 原先直接覆盖目标文件；现按同一 release 链后置 materializer 的既有模式使用同目录 tempfile + flush/fsync + `os.replace`，避免中断留下截断正式输出。SRT/CSV 字节语义、QA、artifact lineage 与 `publish_ready=false` authority 不变。
- 修正 directory-input path-safety 过期注释；澄清旧 Smart/Pro freeze tag 仅是历史 baseline，当前 selector 仍为 Smart v1.2.10 / Pro v1.2.6；明确旧 `change-record.md` 与当前 V4 change record 的职责边界。

## 深审结论

- `smart_current.py` 仍是 current Smart 唯一生产真源，绑定 v1.2.10；Pro policy 仍为 v1.2.6；Max/package/docs 当前版本一致为 `4.0.0a14`。没有发现 production consumer 绕开 current facade 或自建 selector。
- release validator 仍逐文件 hash 绑定 final SRT/audit/QA，并要求 production final-render 三层同时满足 `editor_reconciled`、`publish_ready=true`、无 `release_blocked_reason`；canonical evaluation render 继续 fail closed 为 `publish_ready=false`。
- 生产 Python 未发现 `shell=True` / `os.system`；算法结果未发现未固定随机性。安全随机 token 仅用于 verification session / run lock。
- public tests 未发现 `assertTrue(True)`、明显空集合真通过或大面积 conditional skip；唯一极窄 SkipTest 是 LibreOffice Python 环境缺少 system launcher 时的兼容分支。
- legacy pre-v4 pipeline 保持 fail-closed migration wrapper；artifact retention 不按版本盲删；2026-09-03 calibration-only ablation 结论仍成立：保留 Chroma+MFCC、Fine、Piecewise capability，不新增 `--skip-fine` / 新 heuristic gate。

## 明确未改

Fine/MFCC/Piecewise TimeWarp 算法、Smart immutable version chain、Pro/Max timing/text mutation authority、`_v4_*_impl` wrappers、redo compatibility、production timing/text/release semantics、private blind/truth；不增加新算法能力，不消费未观察 blind truth 调参。

## 封板原则

没有新的真实 production failure 或独立 structural truth，不重开已否决 heuristic；diagnostic 不升级 mutation/release authority。封板维护只接受能明确降低错误、漂移、并发/中断损坏或发布误判风险的窄改动。

## 封板验证证据

- 上一版 clean 隔离基线完整 suite 为 814/814 OK；它早于本轮新增 durability 修复，只作为历史对照。
- 最终实现候选 `220bf9e7935a6c8be5eebf8920bd149bb25f05b0` 在 clean detached worktree 上完成完整 suite：817/817 OK（88.194s）。相较 814 基线新增的 3 项正是本轮 durability 故障注入回归：render SRT/CSV replace-failure 两项，以及 task/run identity JSON writer replace-failure 一项。
- 同一实现候选的 targeted 证据：docs-contract 10/10、repository/environment contract 18/18、TrackAsset resolver 6/6、render 3/3、run-config 6/6、compileall、environment preflight、Skill validation、documentation validator、`git diff --check` 全部通过；privacy 为 `ok=true, issues=[]`。
- 该候选合入并通过 HTTPS 发布后，又在真实 clean `main` 上重跑完整 suite：817/817 OK（82.102s）；运行前后 `HEAD == origin/main == 220bf9e7935a6c8be5eebf8920bd149bb25f05b0`，工作树均为空。真实 main 上 documentation validator、privacy、Skill、environment 也再次通过。
- 发布后使用 GitHub API、HTTPS `ls-remote`、本地 `HEAD`、本地 `origin/main` 四重确认远端/本地实现封板点一致。SSH 自定义 host 的 22 端口当时不可用，因此发布使用一次性 HTTPS URL；仓库 `origin` 配置未被修改。
- 本记录之后如仅发生 documentation-only closeout，不得改变上述实现、测试或 authority 结论；最终 HEAD 仍必须重新满足完整 suite、documentation validator、privacy/Skill、`git diff --check`、`HEAD == origin/main` 与 clean worktree，不能以本段历史绿灯替代最终验收。

## 真实 production 回归封板

本轮在最终实现 `4.0.0a14` 上补做真实项目回归，全部 fresh 输出写入独立回归目录，不覆盖既有正式成品；比较基准使用各项目已验收的历史正式输出。以下 PASS 均由当前实现重新执行并与历史真源比较，不以旧 regression test 代替真实项目运行。

- 走路带风 120：fresh Max 对历史 a13 `pass_exact_semantics=true`，timeline 0 行变化、0 ms 漂移；最终 882/882 cue exact，SHA-256 均为 `f5c81f3f8eeff70f080e1b3fb0cac3cceaabcdcc37811820febae2a584287261`，final audit 与 release validation 通过。
- 快乐健走 140：旧基线为 a8，因后来正式引入 `content_end` plan metadata，run-semantics 全对象不要求字节等同；但 936 条真实 timeline 为 0 行变化、0 text 变化、最大 start/end 漂移均 0 ms。canonical 与 display 两份正式 SRT 均 936/936 exact，SHA-256 分别为 `83e5c652040bc4283dd56db1484bcd5a4c90fea2265bebf7776f4615b8f36880`、`480a0af5f58a7d3a73e331620c01e7af70f18128e9118f69977abd0b591ed3e2`，双 audit / release 通过。
- KPOP 110 newmix：17 occurrences 的 Max 与历史基线 exact semantics，timeline 0 行/0 ms；Gee reference 由 a14 fresh timeline 重新生成并验证。最终 1158/1158 cue exact，SHA-256 `c51867f35dfc50264b30d352e6f3bc07aafb9a0ace1feee3113a54d238adc573`，audit / release 通过。
- KPOP 130：Max exact semantics，timeline 0 行/0 ms；canonical 与 display 均 786/786 exact，SHA-256 分别为 `500d7aea2d25399d275cec6709e854719b600c8caebbd6e324d841bbc67a66c7`、`ce5e07413ab49d536d855ea46fb7fc09c3e6ba624a44a2b89fbb31e2e2c0a2d2`。
- 华语男声 190：Max exact semantics，timeline 0 行/0 ms；production 与 display 均 672/672 exact，SHA-256 分别为 `2265e805a3b65cc5918f72f7d0fc5cdc3c3decd04c3d88143e5aa6578c2cf9f0`、`c497c9a5e5ed623c497504921975f99ce77c0747916fc261cea56020ea8141fc`，双 audit 通过且 release ready。
- KPOP 200：第一次 fresh run 在第 6 个 coarse 后被 Windows 外部中断（`0xC000013A`），无 Max traceback，不计产品失败，也未复用其半成品。随后从全新 `k200_r2` 目录重新 cold-run：14/14 primary 为 `AFFINE_ACCEPTED`、14/14 Fine 为 `refined`、13/13 transition decision 与历史形态一致，`resume_hits=0`，Max exit 0（913.121s）。a13→a14 comparator 为 exact semantics，timeline 0 行/0 text/0 ms。人工确认的 `10 Minutes` source cut `212.778074–222.811687s` 使用 fresh a14 Fine affine mapping 独立复算为 `778507–788172ms`，逐毫秒保持历史真值。production 与 display 均 826/826 exact，SHA-256 分别为 `f3d715ebbeb95f0e8bc8764596250ebaa8864173ac7e6bd7a251758796c2d5e2`、`c7792d729a9262b05e663cdc78aeae0cd0d0a9177c3c0c3f306cc96b2e412d6f`，双 audit 通过且 release ready。
- 华语青春 180：N/A。现存材料缺少足以重建同一 production task 的完整总混音、source SRT 与 V4 task manifest；未人为拼造替代任务，也未将不完整样本记为 PASS/FAIL。

真实 production 矩阵未暴露新的算法 failure、timing/text regression、release authority 漂移或需要重开 heuristic 的独立 structural truth。因此本轮没有修改 Fine/MFCC/Piecewise、Smart/Pro/Max policy 或生产输出语义。
