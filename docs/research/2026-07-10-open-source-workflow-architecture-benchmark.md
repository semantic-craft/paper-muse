# paper-muse 开源应用工作流与架构吸收度评估（2026-07-10）

> 结论先行：paper-muse 已经充分吸收了 **AI 构思、圆桌研究、证伪检索和自有库问答** 这一层的开源优点，但尚未充分吸收成熟研究工具在 **文献/批注的稳定身份、证据溯源、反馈闭环、标准适配器和可重放验证** 上的长处。当前不是“功能太少”，而是若干功能仍以并列脚本或文件输出存在，尚未成为贯穿 `来源 → 证据 → 卡片/主张 → 人类判断 → 下游写作` 的统一研究对象。

本文补充而不重复 [2026-07-07 的 AI 研究/审稿引擎选型](./2026-07-07-github-mature-projects-for-paper-muse.md)：前一份报告主要回答“采用哪个 AI 引擎”，本文主要回答“成熟开源应用怎样把能力组织成可靠工作流，以及 paper-muse 是否真正吸收了这些组织方式”。

## 1. 范围与方法

- 仓库快照：2026-07-10，当前工作树 `HEAD 904ccbd`。只把已落代码与测试视为“已实现”，不把 PRD 中的计划视为现状。
- 核心本地证据：[`blindspot.py`](../../blindspot.py)、[`adversary.py`](../../adversary.py)、[`gptr_sidecar.py`](../../gptr_sidecar.py)、[`paperqa_bridge.py`](../../paperqa_bridge.py)、[`muse_server.py`](../../muse_server.py)、[`knowledge_storm/`](../../knowledge_storm/)、[`tests/`](../../tests/)。
- 外部样本：9 个开源应用/框架，覆盖文献管理、PDF/网页批注、知识与证据组织、系统综述反馈、AI 研究工作流、扩展与自动化。
- 来源纪律：只使用项目官方仓库、官方文档、W3C 规范或作者论文；不使用产品测评、博客转述或聚合榜单。
- 评估不按“有没有同名功能”打勾，而分四层：
  1. **功能存在**：仓库里是否有可调用实现；
  2. **核心工作流整合**：是否从用户动作贯穿到稳定产物和下一步；
  3. **模块 seam / adapter**：是否经公开、窄、可替换的契约接入，而不是私有 monkey patch 或临时命令；
  4. **验证与可扩展性**：是否有 contract test、回放语料、版本/能力探测和失败状态。

## 2. paper-muse 当前架构的真实优势与断点

### 2.1 已形成的强主干

1. **构思幕是一条真实流水线，不是 prompt 外壳。** `blindspot.py` 已有研究者画像与本次困惑分离、多模型并行、三类卡约束、增量上墙、英/中/自有库三面检索、缓存、去重/聚类、离群打分、降级明示和反馈落盘。这里应准确称“离群启发式”：当前 Elo 是手工质量分的公式换算，不是 pairwise LLM debate；未注入 embedding provider 时，聚类向量也是名称的 hash n-gram，而不是语义 embedding。
2. **圆桌层已真正继承并定制 Co-STORM。** 本仓库保留了可替换 LM/RM、动态知识库、主持人和人类注入，又增加固定“第一性原理专家/跨学科猎人”、provider 选择和七件文件契约写回。
3. **对抗幕形成了“红队 → 取证 → 作者答辩 → 仲裁”的回合机。** 主引擎保留最终裁决权，无证据强制“未决”；GPT Researcher 只作为隔离 sidecar 取证，边界方向正确。rebuttal/meta-review/prompt-injection 已有较强 live smoke 证据，是当前最接近“功能、整合、验证三者同时成立”的模块。
4. **自有库证据已从命中数升级到 PaperQA2 桥。** `/evidence/ask` 可从本地 PDF 目录获得带上下文的答案并追加 `sources.md`；缺运行时会结构化降级。但 `webui/index.html` 没有调用 `/evidence/ask`，因此这是后端能力，不是闭环用户工作流。
5. **运维和性能边界优于多数研究原型。** 外部检索有磁盘缓存，状态接口有版本号，证伪支持批处理，重依赖分 venv，另有 release/runtime bootstrap、单元测试和性能 smoke 读数。

### 2.2 仍然割裂的地方

1. **来源没有统一身份。** 扫描卡片、PaperQA 上下文、GPT Researcher source、Co-STORM `Information`、Zotero item/PDF annotation 各自使用不同字典；URL/标题仍承担过多身份职责。
2. **锚点不是一等对象。** 有稿审查能定位草稿字符跨度，但外部证据主要停留在 URL、标题和文本片段；尚无统一的页码、精确引文、前后文、Zotero item key、PDF checksum/版本等 locator。
3. **反馈不是闭环。** 三键判断能落盘，“已知”能抑制再出现，但“新但不适用/新且值得深挖”尚未系统性改变后续排序、检索预算或生成策略，也不能离线回放评估。
4. **适配器边界不稳定。** 检索函数可依赖注入是好基础，但真实接线仍散落在 `real_*`、CLI 子进程和环境变量；`gptr_sidecar.py` 通过 monkey patch GPT Researcher 的内部 retriever 名单/解析器接 CNKI 与 zsearch，升级风险高。
5. **知识结构被过早压平。** Co-STORM 内部已有动态知识树和 source 映射，但导入七件契约时，`mindmap.md` 主要由报告标题层级生成；卡片、证据、失败点、反驳和反馈没有进入同一可查询图。
6. **验证偏代码正确性，缺研究质量回放。** 单元/API/性能测试较强，但缺固定 PDF/检索快照、锚点重附着 corpus、历史真实扫描回放和“反馈后排序是否改善”的评估集。
7. **部分“已采用设计”只到了命名或 payload。** `_owl_academic_query()` 的规范化结果被放进返回 payload，但当前 S2/OpenAlex 实际请求仍收到原 query；“Owl 问式已接入”不能算成立。历史真实 smoke 还出现过 18/18 cards 全为 gold 且 18/18 全为 outlier 的退化信号，而 academic-count 改动之后没有同级真实烟测证明分布已恢复。

## 3. 九个开源项目的可吸收优点与边界

### 3.1 Zotero：文献、批注、引用和连接器形成同一条链

**官方一手来源**

- [Zotero 官方仓库](https://github.com/zotero/zotero)
- [PDF Reader and Note Editor](https://www.zotero.org/support/pdf_reader)
- [为什么批注存在数据库中](https://www.zotero.org/support/kb/annotations_in_database)
- [Local API](https://www.zotero.org/support/dev/web_api/v3/local_api) / [Web API v3 basics](https://www.zotero.org/support/dev/web_api/v3/basics)
- [Translators：Web / Import / Export / Search 四类连接器](https://www.zotero.org/support/dev/translators)
- [API syncing：版本号、增量与乐观并发](https://www.zotero.org/support/dev/web_api/v3/syncing)

**最值得吸收的不是“有个 Zotero 搜索框”，而是三件事：**

1. **批注从一开始就是可引用对象。** Zotero 把批注存在数据库，能同步单条变更；把批注拖进笔记时自动携带引用和回到原 PDF 页的链接。由此形成 `文献条目 → 附件 → 批注 → 笔记 → 写作引用` 的稳定链。
2. **本地和远端共用资源契约。** Local API 在 `localhost:23119/api/` 提供与 Web API 大体同形的读取面，本地离线、无远端 rate limit；版本头和 `since` 则支持增量读取。
3. **连接器有明确类型、元数据与测试。** Translator 是独立 JS，带稳定 GUID、target/priority、正文和 `testCases`；Scaffold 能模拟保存而不污染真实库。

**paper-muse 现状：部分吸收。** 已通过 `zsearch/zfulltext` 做自有库检索、用 `tools/zotero_to_storm_csv.py` 导出 STORM corpus、用 PaperQA2 读 PDF 目录，但这仍是“语料入口”；Zotero item/attachment/annotation 的稳定身份与上下文回跳没有进入卡片或失败点。

**建议吸收：** 做一个只读 `ZoteroLibraryAdapter`，优先走 Local API v3，输出统一 `SourceRef/EvidenceRef`；记录 `library/item/attachment/annotation key`、API/schema version 和 locator。CLI 仍可作全文检索实现，但不再成为领域模型本身。

**避免误抄的代价：** 不要复制完整文献管理、同步、冲突或 PDF 阅读器。Zotero 主体是 AGPLv3；本地 API 当前只支持 GET、无认证且 local/Web 行为并非完全一致。内部 JS API 文档也不完整，不适合作为 paper-muse 的稳定核心依赖。

### 3.2 Hypothesis + W3C Web Annotation：锚点应是可重附着的数据模型

**官方一手来源**

- [Hypothesis client](https://github.com/hypothesis/client) / [client 文档](https://h.readthedocs.io/projects/client/en/latest/)
- [Hypothesis 系统总览](https://web.hypothes.is/help/overview-of-the-hypothesis-system/)
- [h 服务/API](https://github.com/hypothesis/h) / [API 文档](https://h.readthedocs.io/en/latest/api/)
- [Fuzzy Anchoring 官方设计说明](https://web.hypothes.is/blog/fuzzy-anchoring/)
- [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) / [Selectors and States](https://www.w3.org/TR/selectors-states/)

**最值得吸收：**

1. **annotation = body + target + selector。** 评论/判断不是贴在某个 UI 组件上，而是指向一个目标；目标可同时携带 `TextQuoteSelector(exact/prefix/suffix)`、`TextPositionSelector(start/end)` 等多重定位信息。
2. **重附着是分层 fallback。** Hypothesis 会先试精确结构/范围，再以 quote 和上下文做模糊重附着；这比单一字符偏移更能抵抗文档小改。
3. **客户端、侧栏、服务分层。** host-page annotator 负责 selection/highlight，sidebar 负责编辑与查询，service 负责身份/权限/存储；同一 client 可作为浏览器扩展或嵌入页面。

**paper-muse 现状：未充分吸收。** 对抗幕的草稿 `span` 是良好起点，但 scan/own-library/web evidence 不共享统一 selector。`sources.md` 更像来源清单，不是可重附着 annotation store。

**建议吸收：** 定义轻量 `EvidenceLocator`：至少含 `document_id/version`、`exact/prefix/suffix`、`start/end`、`page`、`source_uri`；草稿锚点和 PDF/网页证据都映射到这一结构。与 paper-annotator 的交接也应传该结构，而不是仅传一句 failure statement。

**避免误抄的代价：** 不需要引入 Hypothesis 的账户、群组、Elasticsearch 或托管服务。网页/PDF 重附着还受 PDF 文本层、扫描件 OCR、文档版本和权限影响；标准 selector 是证据结构，不是“百分之百定位成功”的保证。

### 3.3 PDF.js：借渲染层次和回归语料，不要在 paper-muse 内重造阅读器

**官方一手来源**

- [PDF.js 官方仓库](https://github.com/mozilla/pdf.js)
- [Getting Started：Core / Display / Viewer 三层](https://mozilla.github.io/pdf.js/getting_started/)
- [Examples 与异步加载/渲染流程](https://mozilla.github.io/pdf.js/examples/)
- [Display API](https://mozilla.github.io/pdf.js/api/) / [`PDFPageProxy`](https://mozilla.github.io/pdf.js/api/draft/module-pdfjsLib-PDFPageProxy.html)
- [测试与 reference image 流程](https://github.com/mozilla/pdf.js/wiki/Contributing)

**最值得吸收：**

1. **Core、Display、Viewer 明确分层。** Core 是可能变化的解析内部层；Display 是版本化集成 API；Viewer 是成品 UI 起点。依赖方应停在 Display-like contract。
2. **异步任务边界清楚。** `getDocument → document → page → viewport/render task` 可观测、可等待，解析在 worker 中；文本、annotation layer 和画面各有独立测试。
3. **PDF 回归依靠 corpus 和 reference snapshots。** 测试不仅断言函数返回，还检查加载、像素、text-layer、annotation-layer 和翻页行为。

**paper-muse 现状：没有内建 PDF 画布；这不是缺陷。** 它当前是发现/研究/审查工具，真正的稿面批注应继续交给 paper-annotator。值得吸收的是 PDF 文本/locator 的 adapter contract 和小型回归 corpus，而不是把完整 viewer 塞进 WKWebView。

**建议吸收：** 让 paper-annotator 或未来独立 viewer 实现 `DocumentLocatorAdapter`；paper-muse 只消费/产生 selector。准备 10–20 份代表性 PDF（双栏、脚注、中文扫描/OCR、页码错位、字体异常）验证 quote/page 重附着。

**避免误抄的代价：** Viewer 官方明确只是自建 viewer 的起点；Core 未文档化且会变化，主脚本与 worker 必须同版本。直接嵌完整 Viewer 会引入 annotation editor、保存、打印、CORS、无障碍和视觉回归的大量维护面。

### 3.4 Logseq：知识图的价值在“关系仍可查询”，不是多一张图

**官方一手来源**

- [Logseq 官方仓库](https://github.com/logseq/logseq)
- [CODEBASE_OVERVIEW：状态与数据流](https://github.com/logseq/logseq/blob/master/CODEBASE_OVERVIEW.md)
- [插件 API](https://plugins-doc.logseq.com/) / [plugin samples](https://github.com/logseq/logseq-plugin-samples)
- [开发实践与架构/性能测试](https://github.com/logseq/logseq/blob/master/docs/dev-practices.md)
- [DB graph 说明与兼容边界](https://github.com/logseq/docs/blob/master/db-version.md)

**最值得吸收：**

1. **领域状态与界面瞬时状态分离。** graph 中的 block/page/property 是领域数据，当前编辑块等 UI 状态另存；输入事件通过明确事务更新领域数据。
2. **关系是可查询的一等数据。** block、page、tag/property 和引用共同形成可查询图，而不是报告生成后的一张静态图片。
3. **目录边界和插件 API 受架构规则保护。** worker、frontend、parser、plugin API 有明确依赖方向；测试覆盖 schema、query、性能和 e2e。

**paper-muse 现状：部分吸收。** Co-STORM 内部有动态 KnowledgeBase，七件文件契约也保留开放性；但报告完成后主要输出标题型 `mindmap.md`，扫描卡、证据、失败点、答辩和反馈并未形成同一可查询关系层。

**建议吸收：** 保持 Markdown/JSON 是权威产物，新增一个**可重建投影**（例如 SQLite 或单个 `evidence-graph.json`），节点只需 `topic/card/claim/failure/evidence/annotation/feedback`，边只需 `supports/refutes/derived-from/annotates/deepens`。先服务查询和联动，不做通用第二大脑。

**避免误抄的代价：** Logseq 正经历 file graph/DB graph 双轨迁移，官方对 DB 版本和插件兼容有明确警告。不要把 paper-muse 的开放文件契约替换为专有 graph DB，也不要复制页面、任务、同步、发布等整套知识管理功能。

### 3.5 Joplin：真正成熟的扩展性来自窄 API、进程边界和 contract tests

**官方一手来源**

- [Joplin 官方仓库](https://github.com/laurent22/joplin)
- [插件系统架构](https://joplinapp.org/help/dev/spec/plugins/)
- [Plugin API](https://joplinapp.org/help/api/references/plugin_api_index/) / [Data API](https://joplinapp.org/help/api/references/rest_api/)
- [同步架构](https://joplinapp.org/help/dev/spec/sync/)
- [Plugin manifest](https://joplinapp.org/help/api/references/plugin_manifest/)

**最值得吸收：**

1. **插件通过 proxy/host/service 接入，失败边界清楚。** 桌面端可把插件脚本放到独立进程；Plugin API 与平台 runner 隔开平台差异。
2. **数据面是资源 API。** note/notebook/tag 等统一走 GET/POST/PUT/DELETE；插件内访问同一数据面无需另造内部调用路径。
3. **可替换实现靠同契约测试。** Synchronizer 只依赖 SyncTarget/FileApi，测试可用内存 target，再切到文件系统或远端实现。
4. **manifest 明示版本和平台能力。** `app_min_version`、platforms、生命周期等让“不兼容”变成可诊断状态。

**paper-muse 现状：方向正确但还没到稳定扩展面。** 纯引擎函数大量依赖注入，sidecar 也隔离重依赖；但没有统一 provider registry、capability manifest、版本探测和每个 adapter 必跑的 contract suite。

**建议吸收：** 先定义三个窄协议而不是“插件市场”：`SearchProvider.search()`、`CorpusProvider.ask()`、`AnnotationSink.write()`；统一返回 `status/degraded/provenance/cost`，给每个实现运行同一组 contract tests。等有 3 个以上第三方实现再考虑插件发现/manifest。

**避免误抄的代价：** 不需要 Joplin 的完整跨端同步、锁、冲突和 E2EE。Joplin 主仓默认 AGPL-3.0-or-later，且插件/runner 能力存在平台差异；复制实现远重于借接口原则。

### 3.6 ASReview：三键反馈要变成可观察、可回放的人机循环

**官方一手来源**

- [ASReview 官方仓库](https://github.com/asreview/asreview)
- [研究者在环与总体工作流](https://asreview.readthedocs.io/en/latest/lab/about.html)
- [Screening：异步训练、自动保存、修改标签](https://asreview.readthedocs.io/en/latest/lab/screening.html)
- [Simulation：回放与指标](https://asreview.readthedocs.io/en/stable/lab/simulation_overview.html)
- [扩展模型、子命令和数据集](https://asreview.readthedocs.io/en/latest/technical/extensions.html)

**最值得吸收：**

1. **人类判断会改变下一条推荐。** 每个 relevant/irrelevant 标签进入下一轮训练和排序，训练异步进行，不阻塞继续筛选。
2. **判断是可更正、可追溯的项目状态。** 决策和笔记自动保存，可回看和改标签；人类被明确定位为 oracle。
3. **同一引擎有 simulation 模式。** 完整标注历史可用于比较排序/停止策略，并输出 recall、work saved、time-to-discovery 等指标。

**paper-muse 现状：只吸收了反馈采集的第一步。** `angle-feedback.json` 记录三键结果，“已知”抑制复现；其他两类反馈对下一次生成、排序和证据预算的影响很弱，且没有 replay/evaluation。

**建议吸收：** 先做无模型的事件日志与回放：记录 card 特征、生成/检索版本、三键判断、最终是否进入圆桌/对抗/写作；用历史会话比较 `top-k 新且值得深挖命中率`、首次有价值卡位置、重复角度率和证据降级率。只有数据证明规则不够时，再引入轻量学习排序。

**避免误抄的代价：** ASReview 优化的是固定候选集中的纳入筛选，paper-muse 优化的是开放式角度发现；不能直接复制二分类 active-learning 模型或“95% recall”停止条件。其流程原则可借，任务统计假设不可照搬。

### 3.7 PaperQA2：证据不是答案末尾的链接，而是检索、重排和回答的共同对象

**官方一手来源**

- [PaperQA2 官方仓库与 README](https://github.com/Future-House/paper-qa)
- [作者论文](https://storage.googleapis.com/fh-public/paperqa/Language_Agents_Science.pdf)

**最值得吸收：**

1. **索引生命周期显式。** 本地论文会经历 metadata/retraction check、解析、chunk/embed 和缓存；目录未变则复用，索引参数变化会创建新索引。
2. **Search Papers → Gather Evidence → Generate Answer 分层。** 先召回，再对 passage 做 contextual summary/评分/重排，最后只把最佳证据交给回答器。
3. **provenance 和“不足信息”是一等结果。** 上下文与页级引用跟随答案；`contracrow` 等配置把矛盾查找作为明确任务，而不是在通用 QA 后猜。
4. **模型、embedding、vector store 和成本配置均有 seam。** 支持 LiteLLM、本地 embedding、hybrid search、Numpy/Qdrant 和显式 rate limit。

**paper-muse 现状：已接线，但尚未进入用户与核心决策。** `paperqa_bridge.py` 有隔离运行时、健康探测、结构化降级、`/evidence/ask` 与 `sources.md` 写回，测试也覆盖桥契约；但 `webui/index.html` 没有调用该 endpoint。卡片点击/圆桌/失败点也尚未统一消费同一 PaperQA evidence object。

**建议吸收：** 将 PaperQA context 映射为统一 `EvidenceRef`，卡片深挖、对抗失败点和圆桌都复用；把 index/version/settings 写入 provenance；优先为“证伪/矛盾”提供显式 preset。

**避免误抄的代价：** PaperQA2 的高质量配置成本较高且模型敏感；作者内部系统有未完全开放的工具/全文访问条件。README 也提示版本兼容策略快速演进，必须 pin 版本并保留 bridge contract tests，不能把论文指标直接当作本地默认包保证。

### 3.8 GPT Researcher：已吸收主工作流，但适配器接法仍脆弱

**官方一手来源**

- [GPT Researcher 官方仓库](https://github.com/assafelovic/gpt-researcher)
- [官方架构介绍](https://docs.gptr.dev/docs/gpt-researcher/getting-started/introduction)
- [Search engines 与 custom retriever](https://docs.gptr.dev/docs/gpt-researcher/search-engines)
- [MCP 两阶段工具选择](https://docs.gptr.dev/docs/gpt-researcher/retrievers/mcp-configs)
- [过程日志](https://docs.gptr.dev/docs/gpt-researcher/handling-logs/all-about-logs) / [自动化测试](https://docs.gptr.dev/docs/gpt-researcher/gptr/automated-tests)

**最值得吸收：**

1. **planner 与 execution 分工。** planner 拆问题、过滤聚合，execution/crawler 并行找证据，最后报告阶段消费带来源上下文。
2. **retriever 是显式扩展点。** 官方支持 custom endpoint 与 MCP；MCP 先按 query 选择工具，再用 query-specific arguments 执行，也能和 web retriever 混跑。
3. **研究过程可观测。** JSON events、sources/context、retriever smoke harness 与测试说明为诊断成本、失败源和结果差异提供接口。

**paper-muse 现状：工作流吸收度高，seam 吸收度中等。** 对抗幕已把 GPT Researcher 放在隔离 sidecar，批量处理主张、混跑 Tavily/CNKI/zsearch，并明确“sidecar 只取证，主引擎裁决”。但 CNKI/zsearch 是 monkey patch 内部 `get_all_retriever_names/get_retriever` 注入的，未走官方 custom/MCP 公共面。

**建议吸收：** 保留 sidecar 隔离与批处理，把两套检索器迁到 documented custom endpoint 或 MCP；写 retriever contract tests，统一 source/cost/degraded event，再让 perf smoke 读取过程日志。

**避免误抄的代价：** 默认流程依赖外部 LLM/搜索 API，官方自动化测试也需要 key；provider“可配置”不等于不同模型行为等价。不要把它扩展成 paper-muse 总 harness，也不要让它接管证据裁决。

### 3.9 STORM / Co-STORM：吸收最充分，但动态知识结构没有贯穿全产品

**官方一手来源**

- [STORM/Co-STORM 官方仓库](https://github.com/stanford-oval/storm)
- [官方 examples 与自有 corpus 入口](https://github.com/stanford-oval/storm/blob/main/examples/storm_examples/README.md)
- [STORM 作者论文（NAACL 2024）](https://aclanthology.org/2024.naacl-long.347.pdf)
- [Co-STORM release：Agent interface、turn policy、dynamic mind map](https://github.com/stanford-oval/storm/releases)

**最值得吸收：**

1. **预写作与写作分离。** research/references/outline 是独立阶段，可通过 flags 运行或加载已有结果；Perspective-Guided Questions 与 grounded simulated conversation 提升问题深度。
2. **LM 与 RM 分离，角色可用不同模型。** curation/outline/article/polish 有独立接口，成本与质量可按角色配置。
3. **Co-STORM 的核心不是“多 agent”，而是 turn policy + 人类注入 + 动态 KnowledgeBase。** 主持人主动暴露未探索信息，知识图随对话更新并可重组。
4. **实验分支是固定快照。** 官方把论文复现保留在 NAACL/EMNLP backup branches，避免主线演进破坏实验可重复性。

**paper-muse 现状：吸收最充分。** 它直接以 STORM fork 为底座，保留接口/知识库/日志，扩展多源 RM、固定专家席位、provider 选择、增量 UI、恢复态和文件契约。短板不是“没用 Co-STORM”，而是内部 KnowledgeBase 没有与构思卡、证据和失败点共享统一身份；写回的 `mindmap.md` 也比运行时图贫乏。

**建议吸收：** 把 Co-STORM `Information` 与统一 `EvidenceRef` 对齐；将 knowledge tree 作为可重建投影的一种视图，并保留 `instance_dump`/prompt/model/retriever 版本作为 replay manifest。

**避免误抄的代价：** 官方明确把 STORM 定位为 Wikipedia-like pre-writing，不能产生 publication-ready article；引用存在也不等于逐 claim entailment 已验证。上游长期活跃度低于本 fork，继续按自养 fork 管理，不等待上游替 paper-muse 解决领域问题。

## 4. Capability matrix：paper-muse 到底吸收到了哪一层

图例：`强` = 已进核心用户路径并有验证；`中` = 有可用实现，但隔离/部分接线；`弱` = 仅脚本、文件或启发式；`无` = 未实现；`不应内建` = 应交给相邻产品或 adapter。

| 对标项目 / 能力 | 功能存在 | 核心工作流整合 | seam / adapter | 验证与扩展 | 判断 |
|---|---:|---:|---:|---:|---|
| Zotero：文献/附件/批注稳定身份 | 中 | 弱 | 中（zsearch/CSV/PDF dir） | 中 | 搜索已用，引用链未吸收 |
| Hypothesis：多 selector 与重附着 | 弱（仅草稿 span） | 弱 | 无 | 弱 | 关键结构缺口 |
| PDF.js：阅读器/渲染层 | 无 | 不应内建 | 无 | 无 | 不应在本产品补齐；只借 locator/corpus |
| Logseq：统一可查询知识关系 | 中（Co-STORM KB） | 弱（完成后压平） | 中 | 中 | 动态图只活在圆桌内部 |
| Joplin：稳定扩展 API/contract tests | 中（依赖注入/sidecar） | 中 | 弱至中 | 中 | 有边界，无统一 provider contract |
| ASReview：反馈驱动排序与 simulation | 中（三键反馈） | 弱 | 弱 | 无 replay | 采集了反馈，未形成学习闭环 |
| PaperQA2：自有库 evidence RAG | 强（后端） | 弱（无 Web UI 调用） | 强（bridge/隔离 runtime） | 强（bridge tests；无真实 UI 闭环） | 已接线，尚未成为用户路径 |
| GPT Researcher：多源证伪研究 | 强 | 强（对抗幕核心） | 中（内部 monkey patch） | 强（sidecar/批处理/API tests） | 工作流吸收充分，升级 seam 脆弱 |
| STORM/Co-STORM：圆桌知识策展 | 强 | 强 | 强（LM/RM/Agent 接口） | 强（单测/instance/log） | 吸收最充分，但未统一全产品知识层 |

### 按四层给总判断

| 层级 | 结论 | 依据 |
|---|---|---|
| 功能存在 | **充分，但有启发式/孤立能力** | 扫描、圆桌、对抗、PaperQA、三面检索、反馈、文件契约都已落地；PaperQA 未接 UI，Elo/embedding 仍是启发式 |
| 核心工作流整合 | **较充分但未闭环** | AI 主路径贯通；来源身份、批注、反馈学习、跨幕 evidence 复用仍割裂 |
| seam / adapter | **部分充分** | 纯引擎依赖注入和 sidecar 隔离优秀；真实 provider 仍散落、部分依赖私有 patch |
| 验证与可扩展性 | **工程验证较强，研究验证不足** | 对抗幕 live smoke 强；扫描曾出现全金/全离群退化，academic-count 后缺同级复测；另缺 selector/PDF corpus、历史 replay、质量指标和 provider contract suite |

因此，对“是否已经充分吸收各种开源项目优点”的准确回答是：

- **在 AI 引擎和单机产品工程上：基本是。** 尤其 STORM、GPT Researcher、PaperQA2 已不是概念借鉴，而是有边界、有降级、有测试的真实接入。
- **在成熟研究应用的资料—证据—批注—反馈底座上：还不是。** 现有能力强，但仍更像三台优秀引擎共享一组文件，而不是一套统一、可追溯、可回放的研究工作台。
- **并非所有缺口都应在 paper-muse 内补。** PDF 阅读、重型文献管理、多端同步、通用第二大脑应留给 Zotero、paper-annotator 或其他相邻工具；paper-muse 只需要稳定 contract。

## 5. 建议的最小吸收路线（不扩张成平台）

### P0：统一证据与锚点契约

定义单一 `EvidenceRef`（JSON schema + Python dataclass/TypedDict 均可），最小字段：

```text
id
source_type / source_id / source_uri / source_version
locator: {page, exact, prefix, suffix, start, end}
retrieval: {provider, query, retrieved_at, index_version}
relation: supports | refutes | context
status: verified | degraded | unresolved
```

先让 PaperQA、GPT Researcher、blindspot anchors、Co-STORM `Information` 和 adversary evidence 都能映射；不要先建数据库或 UI。

### P1：把外部能力收敛为三个窄 adapter

- `SearchProvider.search(query, budget) -> SearchResult[]`
- `CorpusProvider.ask(question, scope) -> EvidenceAnswer`
- `AnnotationSink.write(target, body, selectors) -> AnnotationRef`

每个实现共享 capability probe、degraded semantics、cost/provenance 和 contract tests。优先把 GPT Researcher 的 CNKI/zsearch 私有 monkey patch 迁到 custom endpoint/MCP；Zotero Local API 做只读 provider。

### P1：反馈事件日志 + 离线 replay

保留当前 `angle-feedback.json` 兼容面，另追加不可变事件：候选卡特征、排序、用户三键、点击深挖/送审、最终写作采用。先用规则回放比较排序，不急着上 active learning。

### P2：可重建 evidence graph，不替代七件文件

从现有 Markdown/JSON + `EvidenceRef` 派生 `topic/card/claim/failure/evidence/feedback` 图；UI 可按卡或主张查看支持/反对证据。图是索引/投影，可随时重建，文件契约仍是与写作技能链交接的稳定 SSOT。

### P2：建立研究型 regression corpora

- PDF/锚点 corpus：双栏、脚注、中文 OCR、页码漂移、轻微改稿后的重附着；
- provider contract corpus：成功、真零命中、认证缺失、限流、超时、坏 JSON；
- 历史扫描 replay：比较首张有价值卡位置、重复率、三类覆盖、来源可验证率；
- 对抗 corpus：支持/反对/证据不足、草稿 prompt injection、同一主张改写。

## 6. 不建议吸收的开源优点

1. **不在 paper-muse 内建 Zotero/Logseq/Joplin 的通用资料库。** 当前单用户、论文项目文件契约是优势。
2. **不内建 PDF.js 完整 Viewer/annotation editor。** 让 paper-annotator 拥有稿面，paper-muse 传标准 selector。
3. **不做通用插件市场。** 先有 3 个窄 adapter 和 contract tests，再谈动态发现。
4. **不把反馈闭环等同于训练模型。** 先留事件、回放、指标；数据不足时规则更可解释。
5. **不把“有引用”当“主张已被证据支持”。** 保持当前主引擎三态裁决和“无证据=未决”。
6. **不把 GPT Researcher 或其他 harness 升格为总编排器。** paper-muse 的差异化是研究者相对新颖性、中文法学检索面和两幕工作流；通用 harness 应继续停在工具层。

## 7. 最终结论

paper-muse 已经不是“没有吸收开源项目优点”的项目；相反，它在 AI 引擎选择和组合上已经相当成熟，且比许多开源研究原型更重视降级、性能、隔离和本地交付。下一阶段的主要收益不会来自再接一个大模型框架，而来自把现有强能力之间的缝补上：

> **稳定来源身份 + 可重附着锚点 + 统一 evidence contract + 反馈回放 + provider contract tests。**

完成这五点后，paper-muse 才能从“多个优秀开源引擎的高质量集成”升级为“研究资料、发现、证据和判断真正闭环的研究工作台”。
