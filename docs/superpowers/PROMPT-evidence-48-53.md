# 提示词：证据流水线收尾（#48–#53 · PRD #40 后半）

本文件是「统一证据契约」(PRD #40) 剩余 6 个 issue 的开工/提醒提示词。前半 #41–#47 全部合并进 main（EvidenceRef 数据模型、CNKI/zsearch 归一化、Zotero 稳定身份、PaperQA 卡片证据、GPTR 公开 seam、对抗幕双源证据、圆桌复用卡片证据）。

## 一句话现状

证据身份地基 (`evidence.py` 的 `EvidenceRef`/`EvidenceGateway`) 已贯穿构思幕/PaperQA/对抗幕/圆桌四处消费端。剩下的是**把这套身份沉淀成可交接、可回放、可校准、可投影的研究对象**，并**机械收口删掉迁移期私有形状**。

## 依赖 DAG（已合=✓，决定做的次序）

```
#41✓ #42✓ #43✓ #44✓ #45✓ #46✓ #47✓
         ├── #48 批注交接包      ← #44,#46            ← 现可做（叶子）
         ├── #49 run manifest    ← #42,#44,#45,#46,#47 ← 现可做（叶子）
         │      └── #50 反馈事件+离线 replay ← #49
         │             └── #51 quality/Proximity/真 Elo ← #50
         ├── #52 移除 provider 私有形状 ← …#48,#49
         └── #53 evidence graph 投影 ← #48,#50,#52   ← 最后
```
建议次序：**#48 与 #49 先做（互不依赖）→ #50 → #51 → #52 → #53**。

## 各 issue：做什么 + 验收要点 + 接缝

### #48 输出可重附着的批注交接包（叶子，先做）
把草稿/PDF/web 证据的 locator 统一成可重附着 selector，经 `AnnotationSink` 交给 paper-annotator 等下游；小改稿能重定位，不能则明确 `unresolved`，绝不猜。
- 验收：locator 同支持 exact/prefix/suffix + start/end + page + source id + version/checksum；草稿字符 span 扩成 quote+context+position 复合 selector；PaperQA/web 用同一 selector 语言，缺字段影响 verified/unresolved；`AnnotationSink` 输出稳定交接包（不要求内建 PDF viewer）；下游 fixture 验精确命中/轻改重附着/歧义/彻底失配；失配只产 unresolved/re-anchor，不产模糊成功。
- 接缝：`evidence.py` 的 `EvidenceLocator`（已有 kind/value/exact/prefix/suffix/page/start/end/source_identity/source_version）→ 抽「重附着」算法（用 exact+prefix+suffix 在改后文本里再定位，仿 `adversary.locate_span` 的逐字精神但带上下文容错）；新 `AnnotationSink`（可放 `annotation.py`）产交接包；对抗幕失败点 span（`adversary.locate_span`）与 PaperQA locator 归一到同一 selector。

### #49 为研究流程写版本化 run manifest（叶子，先做）
为扫描/卡片证据问答/圆桌/对抗幕生成统一、无秘密的 run manifest，关联代码/模型/prompt/provider 能力/预算/缓存/耗时/降级/产物版本。
- 验收：每次运行有稳定 run id + schema version + 起止时间 + 父子关系；记代码版本、prompt/方法论版本、模型、provider capability、检索/index 版本、预算、缓存、perf 读数；指向产物与 evidence ids，不复制/取代七件文件；**密钥/token/密码/完整私密画像/未授权原文不得进 manifest**；perf smoke 输出携带 manifest+代码版本（旧 smoke 不再能证明新代码）；测试覆盖序列化稳定、秘密清洗、降级、跨流程关联。
- 接缝：新 `run_manifest.py`（无秘密 dataclass + 落 `run-manifest.json`，与七件文件并列的观察投影，别塞进契约）；`blindspot.run_scan` / `paperqa_bridge.ask_self_library` / 圆桌 `/report` / 对抗幕 `run_review` 各挂一次 manifest 写入；代码版本取 `git rev-parse HEAD`（无 git 回退空）；秘密清洗白名单式序列化。

### #50 记录反馈事件 + 离线 replay（← #49）
三键反馈/修正/后续深挖-送审-采用记为不可变事件，从历史 manifest + provider 快照离线回放扫描，用可解释规则证明反馈改变下一轮。
- 接缝：现 `/scan/feedback` 写 `angle-feedback.md`（抑制表）→ 追加不可变事件流（`feedback-events.jsonl`），旧「已知」抑制由事件投影得到；离线 replay 读固定 provider 快照（不调付费 API），产指标（首个有价值卡位、重复率、gold/outlier 选择性、验证 locator 比例、降级率、证据复用、成本/时延）。

### #51 校准 quality/Proximity/真 Elo（← #50）
首屏续用便宜 `quality_score`（**改名，不再叫 Elo**）；Proximity 用完整卡片语义；卡片上墙后预算内跑真 pairwise judge tournament，只有真比赛才有 `elo_score`。
- 接缝：`blindspot.py` 现「Elo」是质量分公式换算（改名 quality_score）；Proximity 现按名称 hash n-gram → 改用完整语义（机制/why_nonobvious/steelman/可行性）；新增异步有界 pairwise tournament（每场 match/judge/理由入 manifest）；固定 replay 含应/不应 gold/outlier 样例，防「标签全满测试仍绿」。

### #52 移除 provider 私有证据形状（← …#48,#49）
expand–contract 收口：确认所有消费端只依赖统一契约后，删旧 provider 私有字典与迁移兼容路径，外部行为与七件文件稳定。**只做机械收口，不加新功能。**
- 接缝：审计所有 `anchors`/私有 `source` 字典与 GPT/PaperQA/Co-STORM 特有字段的跨 seam 使用 → 删除；测试不再 patch provider 私有注册或依赖内部字典布局。

### #53 构建可重建 evidence graph 投影（最后，← #48,#50,#52）
从七件产物 + EvidenceRef + manifest + 反馈事件构建可随时重建的 evidence graph；按卡片/主张查支持/证伪/上下文/来源/批注/后续判断，不引入第二事实源。
- 接缝：新投影器（幂等重建、删了能从权威文件+事件重建）；节点 topic/card/claim/failure/evidence/annotation/feedback + 关系 supports/refutes/context/derived-from/annotates/deepens；UI 至少一条按卡片、一条按主张的查看路径；unresolved locator/degraded provider/被修正反馈在图中可见、不当 verified。

## 可做（我自主实现，离线可测、不花钱）
- #48–#53 的引擎/契约/投影逻辑与 API 级测试（仿 `tests/test_evidence.py` 内存 provider、`tests/test_paperqa_bridge.py` monkeypatch、`tests/test_roundtable_evidence.py` 真实对象 + stub 的风格）。每 issue 一分支一 PR，按 DAG 次序。

## 可教（要你出手 / 提供）
- **CNKI 中文面 live 验证**（活 Chrome + opencli）、**persona 原文替换**、**真机付费冒烟**、**UI 呈现（#53 图/查看路径）按「4 方向反应法」签收**。

## 怎么跑 / 纪律
- 测试 `.venv/bin/python -m pytest -q`（现 210 全绿基线）。起 server `.venv/bin/python muse_server.py --port 8765`，UI `/ui/`，不接后端 `/ui/?demo=1`。
- 多会话并行同 repo：提交/删改前 `git status` + 看近期 log；改动追溯到验收标准；`.agents/` 是机器本地软链、**永不提交**（`git add` 用显式文件名，别 `-A`）。
