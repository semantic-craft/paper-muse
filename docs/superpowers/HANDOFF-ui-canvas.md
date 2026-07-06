# 交接：muse UI 画布（两幕剧子计划）——状态与待办

本文件是新会话的完整上下文。上一轮把**构思幕**做完了（盲区扫描卡片墙 + 点卡深挖 web 圆桌，全在 web 画布），已推 GitHub。本文件列**已做 / 没做 / 怎么接着做**。
（旧版本「先做 4 个 mock」的交接见 git 历史 99ad781。）

## 一句话现状

构思幕端到端接线完成，视觉方向选定 **① 文稿台**（单栏手稿 + 朱批旁注）；**但至今没跑过一次真扫描 / 真圆桌**——付费边界内全绿，真实验收是本轮欠的核心。对抗幕（slice 3/4）还没动。

## 已做（已 commit + push，main）

- 4 方向设计 mock（`docs/superpowers/mocks/`）→ 用户选 **① 文稿台**。
- **卡片墙**：`webui/index.html` 接 `/scan`（POST /scan → @1.5s 轮询 `/scan/status` 增量出卡 → 三键 `/scan/feedback`）；防御式渲染（own_hits/zh_hits 可 null、gold/outlier/feasibility 可缺）。
- **圆桌**：同画布 web 视图 `#rtView`，点卡「深挖圆桌」→ `enterRoundtable` → `/session`→轮询热身→`/step`→`/report`；「在访达打开」经 museBridge 回原生 Finder。文稿台手稿语言（对谈条目=席位色点，你=朱批楷体缩进，主持=金）。
- **app 壳**：SwiftUI + WKWebView（`PaperMuseApp`→`MuseCanvasView`→`CanvasWebView`；`MuseServer` 管 python 进程）。内容区整块就是一个 WebView。**已退役 v0.1 原生圆桌**（删 `RoundtableView.swift` / `MuseClient.swift`）。
- 提交：`12428a8` mocks、`09b6670` 卡片墙接线、`f21df3e` 圆桌重建。

## 关键架构 / 文件

- `blindspot.py` — 扫描引擎（三家合议 + 新颖性三面判据 + 落盘）。
- `muse_server.py`（FastAPI :8765，单会话 + 一把锁）— `/scan` 系 + `/session /status /step /report`（圆桌）+ `/ui` 静态托管 `webui/`。
- `webui/index.html` — 单页画布：`#wallView`（卡片墙）+ `#rtView`（圆桌），JS 切视图；内联 `DEMO_CARDS/DEMO_TURNS`，`?demo=1`（墙）/ `?demo=1&view=rt`（圆桌）不接后端预览；真接线全走 fetch 同源。
- `app/Sources/` — 4 个 swift：`PaperMuseApp`（入口）、`MuseCanvasView`（loading/ready/failed + WebView）、`CanvasWebView`（WKWebView + `museBridge{action:reveal}`）、`MuseServer`（拉起/复用/退出清理 python）。
- spec：`docs/superpowers/specs/2026-07-05-muse-two-act-design.md`（§4 卡片墙 §5 圆桌 §6 对抗幕 §12 验收）。

## 卡片字段（webui 渲染契约，照 blindspot 真输出）

`type`(学科视角|理论框架|研究方法) / `name` / `mechanism` / `why_nonobvious` / `steelman` / `feasibility`(方法卡) / `questions[]` / `novelty`(主流|边缘有人做|交叉空白|中文面未检) / `gold`(🥇英热中冷) / `outlier`(🔸离群) / `own_hits`(📚>0且交叉/边缘) / `en_hits` / `zh_hits`(可 null) / `anchors[{title,url}]` / `source_models[]`。**服务端卡无 id**，webui ingest 时补 i+1。

## 没做 / 待办

### ★ 门槛：真实端到端冒烟 + 按 §12 验收（先做，几乎必炸 bug）

全程只验到「付费边界」（demo 渲染、模拟流式、xcodebuild、真机起 app 空态）。**没跑过一次真的。** 起 server + app，真跑一个中文法学主题：

- **扫描**：首批卡 ≤20s？三类卡齐？≥1 离群标亮？每卡最强反驳 + **真实**文献锚点？中英密度非空（或明示中文面未检）？标「已知」→再扫该角度真不出（抑制表）？
- **深挖**：真圆桌热身（1–3min）→出转录→插话/让继续/出报告→7 件产物齐？「在访达打开」真定位产物目录？
- `provider` 三选一（deepseek/openai/gemini）都能跑通两处？
- 真跑大概率炸：字段错位、轮询时序、热身失败处理、报告路径、增量 stagger 真流式没看过。triage + 修。

### 功能 slice（验收过了再上）

- **对抗幕引擎 + UI（slice 3/4，完全没做）**：§6 有稿模式（扫 `$PAPER_MUSE_OUTPUT_DIR` 下 *.md 选草稿→抽中心主张→每条 3–5 最可能崩的失败点→检索证伪/佐证→无证据标「未决」→`failure-points.md`）+ 无稿模式（攻击一句主线，卡片一键送入）。轻量引擎，不套 Co-STORM。web 里对抗幕现在只是占位 tab。persona 取「对抗式审查」原文。
- **圆桌钉死席位（§5，引擎侧没接）**：真 `CoStormRunner` 没强制 ① 第一性原理专家 ② 跨学科猎人（web demo 演了这俩角色，真引擎只是拿种子起普通 session）。要在引擎接。

### 待用户 / 待接

- **persona 原文**：`blindspot.py:FIRST_PRINCIPLES_PERSONA` 是要旨转述，待用户给「第一性原理」「对抗式审查」原文替换。
- **CNKI 走通**：中文学界面（`opencli cnki search`）需活的 Chrome 会话才出真 zh_hits，当前降级「中文面未检」。用户 Chrome + opencli 在时验一次，确认 zh_hits 非 None。
- **研究者画像输入 UI**（本轮 defer）：现画像栏是占位提示、扫描发 `profile:""`。§4 要 5 行输入（领域/立场/熟悉/困惑）→落 `profile.md`→复用；离群/新颖性以画像为参照系，**缺它发现力打折**。
- **主题预填**（本轮 defer）：§2 有 `PAPER_MUSE_OUTPUT_DIR` 时读该目录近期 md 标题预填主题框。

### 小债

- app 自起 server 路径没验（只验了「复用已有 server」；关掉手动 server 再起 app 走 `MuseServer.launch()`）。
- `app/build/`（旧会话 17:38 产物）会误导 `open` 启动老二进制——加 `.gitignore` 或删；正常从 Xcode ⌘R / 默认 DerivedData 跑。
- 圆桌错误态（`/session` 忙 409、热身失败）只是 toast，可能要更稳。

## 环境 / 怎么跑

- venv：`.venv/bin/python`（uv 建，无 pip）。key 在 `secrets.toml`（gitignore，DEEPSEEK/OPENAI/GOOGLE/PERPLEXITY/JINA/TAVILY 全有）。
- 起 server：`.venv/bin/python muse_server.py --port 8765`；UI = `http://127.0.0.1:8765/ui/`。
- app：`cd app && xcodegen generate` 后 Xcode ⌘R；或 `xcodebuild -project app/PaperMuse.xcodeproj -scheme PaperMuse -configuration Debug -destination 'platform=macOS' build`（产物在**默认 DerivedData**，别用 `app/build/` 旧的）。app 会自起/复用 :8765。
- 不接后端看 UI：`/ui/?demo=1`（墙）、`/ui/?demo=1&view=rt`（圆桌）。
- 纪律：**真跑验证不靠猜、带证据**；提交/删改前 `git status` + 看近期 log（多会话并行同 repo）；子 agent 逐任务 + 双阶段评审。视觉稿走「4 方向反应法」，用户对审美有要求、要自己挑。
