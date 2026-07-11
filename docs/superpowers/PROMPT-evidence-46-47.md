# 提示词：证据流水线续做（#46 对抗幕双源证据 · #47 圆桌复用卡片证据）

本文件是「统一证据契约」(PRD #40) 消费端续做的完整开工提示词。#41–#45 已合并进 main（EvidenceRef 数据模型、CNKI/zsearch 归一化、Zotero 稳定身份、PaperQA 卡片证据、GPTR 公开 seam）。本轮做 **#46**，并把 **#47** 排好待接。

## 一句话现状

`evidence.py` 是唯一的证据身份地基（`EvidenceRef`/`EvidenceGateway`，按 `evr_<sha256[:24]>` 稳定 id 去重）。构思幕、PaperQA 桥、GPTR sidecar 都已收敛到它。**但对抗幕只吃 GPTR 单源、圆桌只拿一个主题字符串起会话**——两处都没复用已有的统一证据身份。#46/#47 就是把这两处接上。

## 地基（两 issue 共用，勿另造字典）

- `EvidenceRef` TypedDict — [`evidence.py:87`](../../evidence.py) （`id/source/locator/retrieval/relation/verification`）。`relation ∈ discovery|supports|refutes|context`。
- `evidence_ref_from_record(record, provider, query, *, provider_version="", index_version="")` — [`evidence.py:304`](../../evidence.py)：任意 provider 命中 → EvidenceRef 的公共接缝。id 由 `_evidence_id(identity)`（`evidence.py:217`）算，**同 identity/url/doi 跨源自动合并；同附件不同页得不同 id**。
- 去重范式 = `EvidenceGateway.search` 的 `evidence_by_id.setdefault(ref["id"], ref)`（`evidence.py:402`）。**不要**用 `blindspot._merge_unique`（按整块 JSON dump 去重，粒度不对）。

---

## #46　让对抗幕同时复用 PaperQA 自有库证据 + GPTR 外部证伪证据

**验收标准（issue #46）**
1. 对抗幕可请求/复用与受审主张相关的 PaperQA 证据，而不是重解析 sources 产物。
2. 自有库与外部来源按稳定 evidence id 去重，各自保留 provider provenance 与 locator。
3. 每个失败点能同时展示不同来源的 支持/证伪/上下文 证据。
4. PaperQA 降级不拖垮 GPTR 路径，反之亦然；无直接证据仍强制未决。
5. failure-points 产物与状态 API 保存同一证据身份与关系。
6. API 级测试覆盖：双源成功、单源降级、重复来源、相反立场。

**接缝（精确落点）**
- 主改点：`_apply_falsify_pool(claim, pool, ...)` — [`adversary.py:428`](../../adversary.py)。现在只读 `pool["sources"]`（全来自 GPTR）。
- 依赖注入口：`run_review(...)` [`adversary.py:511`] / `_falsify_claim(...)` [`adversary.py:451`]，仿现有 `falsify_search` 注入风格再加一个「自有库取证」注入口，保持离线可测。
- 分类器 `classify_evidence(claim, failure_statement, hits, llm)` [`adversary.py:276`]：命中 → EvidenceRef，LLM 只判序号+立场、代码回填（杜绝 URL 幻觉）。**PaperQA 证据也过它逐失败点重判立场**（否则默认全算「佐证」，绕过证伪识别）。
- 裁决内核 `decide_verdict(evidence)` [`adversary.py:189`]（无 refutes/supports → 未决，代码强制、抗注入）——**不改**。
- 落盘 `_write_failure_points` [`adversary.py:466`]、状态端点 `adversary_bg`/`adversary_status` [`muse_server.py:1129`/`:1197`]：EvidenceRef 即普通 dict，直接可序列化，**序列化层不用改**。
- PaperQA 取证 = `paperqa_bridge.ask_self_library(question, *, pdf_dir, output_dir, ...)` [`paperqa_bridge.py:607`]，返回含 `evidence: list[EvidenceRef]` 的 bundle 超集 dict；降级恒返回结构化 payload、evidence=[]、绝不伪造空答案。落盘去重可复用 `persist_evidence_bundle`/`read_evidence` [`paperqa_bridge.py:484`/`:531`]。

**两处形状差异 = 核心工作量**
1. GPTR `sources[]` 是扁平 hit dict；PaperQA `evidence[]` 是完整 EvidenceRef。`classify_evidence` 只吃 hit dict → 写 EvidenceRef→hit 展平器（**保留 identity+locator**，使 `evidence_ref_from_record` 重算出的 id 与 PaperQA 原 id 一致，去重才成立）。
2. `classify_evidence` 会丢弃无 `url` 的命中（`adversary.py:280`）；PaperQA 自有库文档可能无 web url、只有 zotero 锚 → 展平/过滤时需容纳「以 locator/identity 为可点锚」，别把自有库证据误杀。
3. 去重与冲突：合并后按 `ref["id"]` setdefault 去重；同 id 两源立场相反时的取舍沿用「先到先得」并保留 provenance（在测试里钉死这个语义）。

**降级隔离**：GPTR 挂了仍拿 PaperQA、PaperQA 挂了仍拿 GPTR；各自 degradation 分别记录（claim 级，仿现有 `claim["sidecar_degradation"]`）。失败点 `f` 的键**只在种子块 `adversary.py:538-540` 预置、异步阶段只换值不加键**（快照序列化安全）——不新增 `f` 键。

---

## #47　让 Co-STORM 圆桌复用卡片证据（本轮排好，紧接下一步）

**验收标准（issue #47）**：卡片启动圆桌时携带 EvidenceBundle；Co-STORM `Information` ↔ EvidenceRef 双向映射；同源同查复用身份不产生重复对象；报告/知识库指回 evidence id+locator；动态知识结构写回保留证据关系；API 级测试覆盖复用/新增/降级/报告写回。

**接缝**
- `SessionReq` [`muse_server.py:556`] 无 card/evidence 字段；`create_session` [`:841`] 只读 `req.topic`。前端 `rtStartSession` [`webui/index.html:2338`] 只发 `{topic}`，**丢弃 `card.evidence`**。
- 卡片其实已握统一 EvidenceRef（`card["evidence"]`，`blindspot.py` 经 `EvidenceGateway.search` 产），但 `SCAN`↔`SESSION` 无传递路径。
- 证据原子对象 `Information` [`knowledge_storm/interface.py:41`]，身份靠 `__hash__(url,...)`（正是 PRD 要修的「URL 承担了本该由稳定身份承担的职责」）。外部证据→Information 唯一接缝 `Retriever.retrieve` [`interface.py:288`（`Information.from_dict` `:306`）]。知识库身份表/序列化 `KnowledgeBase` [`dataclass.py:291`/`update_from_conv_turn` `:784`/`to_dict` `:362`]。
- **关键载体**：把 `ref["id"]` 塞进 `Information.meta["evidence_id"]` — meta 随 `Information.to_dict`→`ConversationTurn.to_dict`→`KnowledgeBase.to_dict` 全程往返，让身份贯穿专家发言/知识库/报告/`instance_dump.json`。
- 注入时机：`build_runner` [`muse_server.py:695`/`:743`] 建出 `CoStormRunner` 后、`warm_start` 前，用一条合成 `ConversationTurn` + `knowledge_base.update_from_conv_turn` 把卡片证据 seed 进去；或包一层 RM 让热身检索含卡片证据。

**要改**：`SessionReq` 加 `card_id`/`evidence` → 前端 `rtStartSession` 带上 `card.evidence` → `create_session`→`warm_start_bg`→`build_runner` 线程化透传 → 新增 `EvidenceRef↔Information` 转换器（约定 `meta["evidence_id"]`）。

---

## 可做（我自主实现，离线可验、不花钱）

- **#46 全量**：展平器 + 双源合并去重 + 依赖注入（贯穿 `run_review` 流式/批量两条路径）+ 服务端接线 + API 级测试（用内存 fake search / monkeypatch，仿 `tests/test_evidence.py` 的 `FunctionEvidenceProvider` 与 `tests/test_paperqa_bridge.py` 的 subprocess monkeypatch 风格）。
- **#47 实现**（紧接 #46；动 knowledge_storm 内部，风险更高，单独一 PR）。

## 可教（要你出手 / 提供，我只能预备与说明）

- **CNKI 中文面 live 验证**：需你的活 Chrome 会话 + `opencli cnki`，才出真 `zh_hits`（当前降级「中文面未检」）。我能把命令与验收点准备好，跑要你在开着 Chrome 时点一次。
- **persona 原文**：`blindspot.py:FIRST_PRINCIPLES_PERSONA` / 对抗式审查 persona 现为要旨转述，待你给原文替换。
- **真机付费冒烟**：#46 端到端在 app 里跑一次要真花 API（DeepSeek/OpenAI/Gemini）。§12 冒烟 2026-07-09 已过一轮，本轮改动的真机复跑要你首肯是否花这笔。
- **视觉签收**：任何 UI 呈现变化按「4 方向反应法」由你定稿。

## 怎么跑 / 纪律

- venv `.venv/bin/python`；测试 `.venv/bin/python -m pytest tests/test_adversary*.py tests/test_evidence.py -q`。
- 起 server `.venv/bin/python muse_server.py --port 8765`，UI `http://127.0.0.1:8765/ui/`；不接后端 `/ui/?demo=1`。
- 多会话并行同 repo：提交/删改前 `git status` + 看近期 log；每 issue 一分支一 PR；改动追溯到验收标准。
