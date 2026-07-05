# 交接：muse UI 画布（两幕剧子计划 2/4）

本文件是新会话的完整上下文。目标：给已经跑通的盲区扫描引擎做一个**好看、现代**的图形界面。

## 一句话现状

后端引擎全部做好并已推 GitHub（`semantic-craft/paper-muse` main）；**界面还没做**——这就是本次要做的事。

## 已经存在什么（别重做）

- **盲区扫描引擎** `blindspot.py`：三家模型（DeepSeek/OpenAI/Gemini）合议 → 三类卡（学科视角/理论框架/研究方法）→ 新颖性三面判据 → 落盘。已测、已真实冒烟。
- **muse_server.py**（FastAPI :8765）已有接口：
  - 盲区扫描：`POST /scan {topic, profile?, output_dir?}` → `GET /scan/status`（增量轮询，返回 `cards[]`）→ `POST /scan/feedback {name, verdict}`
  - 互动圆桌：`POST /session` → `GET /status` → `POST /step` → `POST /report`
- **v0.1 圆桌 App** `app/`（SwiftUI + XcodeGen）：**只有圆桌聊天**，原生气泡，只调 session/status/step/report，**没接 scan**。这是旧设计，本次要么扩展它、要么按新画布方案重写内容区。
- 测试：`tests/` 20 项全绿（`.venv/bin/python -m pytest tests/ -q`）。
- 检索层：Perplexity/Jina/Mixed 三检索器已在 `knowledge_storm/rm.py`。

## 卡片数据结构（mock 要照这个真实形状）

`GET /scan/status` 的 `cards[]` 每张卡字段：
```
type          学科视角 | 理论框架 | 研究方法
name          理论/方法名
mechanism     一句话机制
why_nonobvious 为什么对该研究者非显而易见
steelman      最强反驳（哪类审稿人会怎么打）
feasibility   方法卡才有：数据从哪来
questions     ["拷问句1", "拷问句2"]
novelty       主流 | 边缘有人做 | 交叉空白 | 中文面未检
gold          true=🥇英热中冷（英文成熟×中文法学空白，引入型创新机会）
outlier       true=🔸离群（仅一家模型提出）
own_hits      自有库命中数；>0 且 novelty∈{交叉空白,边缘} → 📚已藏未用徽标
en_hits/zh_hits 英文/中文学界命中数
anchors       [{title, url}] 真实文献锚点
source_models ["deepseek","gemini"]
```
真实卡名示例（够跨学科）：「热力学熵增与信息不对称的负熵逻辑」「组织社会学的制度同构」「裁判文书量化」。

## 关键设计决定（已定，别推翻）

1. **UI 路线：原生 SwiftUI 壳 + WKWebView web 画布**（用户拍板）。壳保留 v0.1 的窗口/进程管理/退出清理；内容区换成 web，视觉上限高、迭代快。
2. **两幕剧**：顶部 `[构思幕 | 对抗幕]` 切换。构思幕=盲区扫描卡片墙（本次重点）+ 可深挖到圆桌；对抗幕=红队审稿（引擎是子计划 3/4，本次先留位）。
3. **首屏是卡片墙**，不是聊天。流式出卡（首批 ≤20s）。
4. **联动靠文件**：产物落 `docs/agents/muse/`（perspectives/questions/sources/angle-feedback.json），喂 grill-with-docs 等技能。

## 本次第一步（务必先做这个，别直接写 app）

**生成 4 个截然不同的 HTML 设计方向**给用户挑——这是 Thariq《Finding Your Unknowns》的方法，也是用户明确要求（他对审美有要求，界面不该 agent 独断）。要求：
- 每个方向是一个**独立自包含 HTML 文件**（内联 CSS，假卡片数据照上面结构，6-8 张卡含各种徽标组合）；
- 4 个方向审美上要**真的不同**（不是配色变体）——比如：编辑器/文档流、卡片瀑布、仪表盘、杂志排版；
- 都要体现：主题输入、模式切换、三类卡、新颖性徽标（🥇🔸📚）、最强反驳、三键反馈按钮（已知/新但不适用/新且值得深挖）；
- 现代感、编辑器级排印、明暗双主题至少留接口。
- 用 `artifact-design` skill 校准投入度；可用 Artifact 工具或直接写 HTML 文件让用户在浏览器看。

用户挑定方向后，再实现选中方向、用 WKWebView 接管 app 内容区、接 `/scan` 增量轮询。

## 待用户提供 / 待验证（不阻塞 mock）

- 用户两组提示词原文：「第一性原理」「对抗式审查」——到手后替换 `blindspot.py:FIRST_PRINCIPLES_PERSONA` 常量（现为要旨转述版）。
- CNKI 走通验证：用户 Chrome + opencli 会话在时跑一次 `blindspot.py`，确认卡片中文学界命中数非 None（当前降级路径已测）。

## 环境

- venv：`.venv/bin/python`（uv 建，无 pip）。装包：`VIRTUAL_ENV=.venv uv pip install <pkg>`。
- key 在 `secrets.toml`（gitignore，DEEPSEEK/OPENAI/GOOGLE/PERPLEXITY/JINA 全有）。
- 起 server：`.venv/bin/python muse_server.py --port 8765`。
- 完整 spec：`docs/superpowers/specs/2026-07-05-muse-two-act-design.md`（§9 UI 路线、§4 卡片墙、§12 验收）。
- 工作纪律：子 agent 逐任务 + 双阶段评审（superpowers:subagent-driven-development），真实验证不靠猜。
