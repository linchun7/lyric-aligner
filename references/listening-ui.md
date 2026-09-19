# 唯一默认试听 / 人工复核 UI 3.2

2026-09-13 起，Safe/R5、Max、Gold、转场和 ownership 统一复用已确认的 **3.2**。不再创建独立试听页面或分支 UI；新任务只增加复核数据，必要字段在同一页面最小扩展。UI 版本不改变任何产品策略或修改 authority。

## 冻结点与启动

- 唯一页面核心：`scripts/v4_human_boundary_anchor_audit_ui.py` 的 `HTML`。保留原布局、播放条、局部时间轴、粗调/细调、独立 A/B、重复试听、倍速及键盘操作。
- 核心 SHA-256：`956d1a63d737551abbef5cc66386622a12ba3208aa136dc415068b58cf5607b1`，锁在 `references/ui32-core-lock.json`，运行和测试均校验。
- UX 版本固定 **3.2**；原 anchor response 协议版本 **3.0** 保留兼容，不能因此把页面改称 3.0。
- 统一入口：`scripts/v4_listen.py`。Windows 双击根目录 **`试听复核.cmd`**；调用隐藏启动器 `scripts/start_listening_ui.ps1`，打开 `http://127.0.0.1:8765/`。
- 默认复核包记录在不入库的 `private/listening/current.json`，绑定绝对路径、pack SHA 和 UI 3.2。未配置或内容漂移会明确失败，不能猜一个旧页面代替。

```powershell
# 初次选择/切换复核包（只更新本机入口，不改字幕）
python scripts/v4_listen.py --pack-dir "<复核包目录>" --set-default --check
# 日常一键启动：双击 试听复核.cmd；终端等价命令
python scripts/v4_listen.py
# 原 anchor 包及其已有候选兼容
python scripts/v4_listen.py --pack-dir "<anchor-pack>" --candidate-consensus "<consensus.json>"
# 仅核对输入；不启动浏览器
python scripts/v4_listen.py --check
# 已作答对照包的描述性统计；输出必须是新文件
python scripts/v4_listen.py --report "<新 metrics.json>"
```

端口不静默漂移。重复启动只复用 **同一 pack + 当前 UI/扩展/存储/启动代码 SHA** 的服务；占用或旧代码实例拒绝启动。Windows 独占绑定防止同一端口同时返回不同复核包。切换包前应结束原项目复核服务，保留所有原始作答；不按进程名批量杀 Python。后台启动错误显示提示，日志位于 `private/listening/logs/`。

## 三种数据，只有一个页面

| 数据 | 默认展示 / 保存 | 权限边界 |
|---|---|---|
| 原 anchor pack（`outer/human_audit.csv`） | 3.2 原有边界审听和保存；原命令继续兼容 | 原 human anchor 校验不变 |
| P1 timing manifest + materialization | 同一 3.2，起/止/内部点，保留 A/B 确认与不确定度；导出原 `timing-decision-review-response-1.0` | 隐藏机器候选位置；仍走既有 ingest / response→Gold 重算；shadow PASS 无生产权限 |
| 字幕对照 `review.pack.json` | 同一 3.2 增加完整文字、差异标记、逐版歌词/归属/明显时间错误判断 | 仅保存原始作答，不能直接改 SRT，也不自动授予 Gold 或 Safe 修复 authority |

P1 `v4_build_timing_decision_review.py` 新生成的 `index.html` 也由同一核心渲染，不维护另一套模板。无效项填写原因即可，不要求强行判定；导出的原 JSON 必须留存后才可关闭页面。

对照字段仅在 `scripts/ui32_review.js` 扩展；存储位于 `lyric_aligner/review/listening.py`。复核 pack schema 为 `listening-review-pack-1.0`：冻结 partition、cases、音频 SHA、共同判定窗口、两版完整相交字幕和输入 SHA。新包的私有比较角色统一为 `baseline` / `candidate`；旧 hash-bound 包中的 `safe` / `balanced` 仅作兼容别名并在统计时归一化，不恢复旧产品路线或 authority。文字可见，**只隐藏版本来源，不能称为 candidate-text-blind**。风险、来源及 shuffle 映射在 `selection.private.json`，不进入作答页面。差异高亮只比较显示字符，不宣称韩英语义或词段对齐已经成立。

听审者可直接选择“吃力 / 不理解 / 无法判断”，不用手打外语。仅已试听、逐项回答、输入未漂移才写入 `responses/` 下新的原始 JSON；重标追加记录，不覆盖历史。没有熟练语言理解的作答保留但不当作准确率 Gold；熟练自述也不是外部资格认证。正式验收还需核对真实审核者与独立性。

统计以两版相同的冻结时间区域为单位，不能用 cue 数不同制造收益：修复率分母为已判 Safe 错误区域，新增错误率分母为已判 Safe 正确区域；歌词、归属、明显时间错误分别配对，任一方 unknown 不填成正确。重复保存按听审者/片段取最新，审核者冲突排除而非制造多数票。无有效作答时比率及净新增正确量是 `null`。该工具只做描述统计，正式资格沿用 `v4_calibration_workflow.py`，不新造通过门。

## 废弃入口与历史兼容

| 旧 UI / 入口 | 当前状态 |
|---|---|
| `v4_build_gap_ab_review.py`、`gap_review_ui.html/js` | legacy；CLI 默认拒绝生成，只有显式 `--legacy-ui` 可历史复现 |
| `timing_decision_review.render_legacy_review_html` | legacy 只保留显式兼容函数；默认 renderer 已用 3.2 |
| `output/selective_audio_rescue_20260913/review_app.py`、`review.html` | legacy，禁止新任务调用 |
| 同目录 `review_ux32.py/js/html`、`review_assisted.py/js`、`assisted_review/index.html` | legacy 任务适配副本；即使曾标注 3.2/3.3，也不再作为默认入口 |
| `output/blind_audio_trial_20260913/listen_blind.html`、`build_listening_page.py` | legacy；禁止新任务生成/启动 |

以上历史页面、原始音频、selection 和已作答记录保留，不能为“废弃”改写被 SHA 引用的证据。旧后台会话退出；统一入口拒绝把任意 legacy HTML 当作当前复核包。历史 task-local HTML 不继续维护。其他未列出的历史试听副本同样是 legacy，新增任务只能用上面三种数据入口。

## 当前验证边界

统一入口已覆盖真实 WAV 播放/倍速、保存与刷新、P1 原格式导出/ingest、未保存修改阻止导出、同包实例复用、异包端口拒绝和 legacy 默认拒绝。工程测试只证明 UI 与协议兼容，不证明字幕语义正确率。

新的韩/日文字判断仍需任务级证据；缺独立 Gold 时不能把人工辅助选择、相关 ASR 解码或工程测试当作准确率。对已确认的具体文字收益继续走已审定字符片段入口；缺证据期间不调 DP/ownership 阈值、不加模型、不扩大功能面。
