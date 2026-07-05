// 画布 mock 的规范假数据 —— 四个设计方向共用同一份，保证审美对比公平。
// 字段结构照 GET /scan/status 的 cards[]（见 HANDOFF-ui-canvas.md §卡片数据结构）。
// 四个 mock 各自把这份数据内联进 HTML（自包含），用小 render 循环出卡，不要手写 7 遍卡片 DOM。

const MUSE_TOPIC = "平台经济中的数据权力与法律规制";

const MUSE_PROFILE = {
  field: "中文法学 · 数据法与反垄断",
  stance: "权利本位、法教义学训练",
  familiar: "个人信息保护法、反垄断法、比例原则",
  puzzle: "想找非显而易见的跨学科切入点，怕落进「数据确权」的红海",
};

// novelty 取值：主流 | 边缘有人做 | 交叉空白 | 中文面未检
// gold=true → 🥇 英热中冷（英文成熟 × 中文法学空白，引入型创新机会）
// outlier=true → 🔸 离群（仅一家模型提出）
// own_hits>0 且 novelty∈{交叉空白,边缘有人做} → 📚 已藏未用
// zh_hits=null → 中文面未检（CNKI 会话失败降级）
const MUSE_CARDS = [
  {
    id: 1,
    type: "学科视角",
    name: "热力学熵增与信息不对称的负熵逻辑",
    mechanism: "把平台对用户数据的聚合视为局部负熵积累，用熵增框架解释信息不对称如何自我强化。",
    why_nonobvious: "你的框架把数据垄断当作市场结构问题；熵视角把它重述为「信息势差的热力学必然」，绕开了反垄断的静态市场界定。",
    steelman: "物理隐喻派审稿人会打——「熵在社会科学是修辞不是机制」，要求你给出可测量的熵定义，否则视为伪类比。",
    feasibility: null,
    questions: [
      "如果熵只是隐喻，它比「信息不对称」这个现成概念多解释了什么？",
      "负熵积累能否落到可观测的平台指标（如数据回流速率）？",
    ],
    novelty: "交叉空白",
    gold: true,
    outlier: false,
    own_hits: 2,
    en_hits: 47,
    zh_hits: 1,
    anchors: [
      { title: "Entropy, Information, and the Economics of Scarcity", url: "https://doi.org/10.1103/entropy.2019.0471" },
      { title: "负熵、信息与市场秩序：一个跨学科综述", url: "https://kns.cnki.net/kcms/sample-4471" },
    ],
    source_models: ["deepseek", "gemini", "openai"],
  },
  {
    id: 2,
    type: "理论框架",
    name: "组织社会学的制度同构（DiMaggio & Powell）",
    mechanism: "用强制/模仿/规范三种同构压力，解释为何各大平台的治理规则趋同——趋同本身即规制失灵的信号。",
    why_nonobvious: "你默认平台差异来自竞争；制度同构预测它们会趋同，把「规则雷同」从巧合变成可解释的因变量。",
    steelman: "法教义学审稿人会打——「社会学描述性理论进不了规范分析，法律要的是应然不是实然趋同」。",
    feasibility: null,
    questions: [
      "同构若是事实，规范上我们该纠正它还是利用它？",
    ],
    novelty: "边缘有人做",
    gold: false,
    outlier: false,
    own_hits: 3,
    en_hits: 210,
    zh_hits: 34,
    anchors: [
      { title: "The Iron Cage Revisited: Institutional Isomorphism", url: "https://doi.org/10.2307/2095101" },
      { title: "平台治理规则的制度趋同现象研究", url: "https://kns.cnki.net/kcms/sample-2101" },
    ],
    source_models: ["openai", "gemini"],
  },
  {
    id: 3,
    type: "研究方法",
    name: "裁判文书大样本量化分析",
    mechanism: "抓取裁判文书网数据监管相关判决，编码争点与裁量结果，用回归揭示裁判的隐性规制偏好。",
    why_nonobvious: "你打算做规范推演；量化能证伪或证实「法院实际上如何规制」，把应然论证锚到实然分布上。",
    steelman: "实证法学审稿人会打——「裁判文书网选择性公开 + 撤诉不上网，样本偏差足以颠覆任何回归结论」。",
    feasibility: "数据来源：中国裁判文书网（2014–2021 公开判决）+ 北大法宝补全；约 3,000 份数据监管相关文书可编码。",
    questions: [
      "选择性公开的样本偏差，能否用断点回归或工具变量部分校正？",
      "量化结论若与教义学结论冲突，谁让步？",
    ],
    novelty: "主流",
    gold: false,
    outlier: false,
    own_hits: 0,
    en_hits: 156,
    zh_hits: 289,
    anchors: [
      { title: "Judicial Behavior in the Age of Big Data", url: "https://doi.org/10.1093/jla/laz009" },
      { title: "基于裁判文书大数据的司法裁量实证研究", url: "https://kns.cnki.net/kcms/sample-2890" },
    ],
    source_models: ["deepseek", "openai", "gemini"],
  },
  {
    id: 4,
    type: "理论框架",
    name: "福柯的规训权力与平台生命政治",
    mechanism: "平台不靠禁止而靠「可见性架构」规训用户行为，数据垄断是全景敞视的当代形态。",
    why_nonobvious: "反垄断话语聚焦价格与市场份额；规训视角把权力重定位到「行为塑造」，解释了免费服务为何仍是权力关系。",
    steelman: "分析法学审稿人会打——「福柯太软，无法生成可操作的裁判规则，只能做批判不能做教义」。",
    feasibility: null,
    questions: [
      "规训视角除了批判，能否产出一条可裁判的规则？",
    ],
    novelty: "交叉空白",
    gold: false,
    outlier: true,
    own_hits: 1,
    en_hits: 88,
    zh_hits: 12,
    anchors: [
      { title: "Discipline and Punish: The Birth of the Platform", url: "https://doi.org/10.1177/platform.2021.088" },
    ],
    source_models: ["gemini"],
  },
  {
    id: 5,
    type: "学科视角",
    name: "科斯交易成本视角下的数据确权",
    mechanism: "数据权属不清的真问题不是归谁，而是交易成本过高导致的配置失灵；确权应最小化谈判成本而非追求道德归属。",
    why_nonobvious: "你从人格权/财产权二分入手；科斯把问题从「谁应拥有」转成「怎样分配最省成本」，可能消解确权之争本身。",
    steelman: "权利本位审稿人会打——「把人格数据还原成成本计算，抹掉了尊严维度，法律不能纯效率导向」。",
    feasibility: null,
    questions: [
      "若确权只看效率，人格尊严的底线由谁守？",
      "交易成本能在数据场景被真实测量吗？",
    ],
    novelty: "边缘有人做",
    gold: false,
    outlier: false,
    own_hits: 0,
    en_hits: 320,
    zh_hits: 58,
    anchors: [
      { title: "The Problem of Social Cost, Revisited for Data", url: "https://doi.org/10.1086/coase.data" },
      { title: "交易成本理论下的数据要素配置", url: "https://kns.cnki.net/kcms/sample-5801" },
    ],
    source_models: ["deepseek", "openai"],
  },
  {
    id: 6,
    type: "研究方法",
    name: "平台间数据流动的社会网络分析",
    mechanism: "把平台、数据、监管者建成网络图，用中心性与结构洞指标定位真正的数据权力节点。",
    why_nonobvious: "法律看合同双方；网络分析看整个生态的拓扑，可能发现权力集中在合同之外的「结构洞」上。",
    steelman: "方法论审稿人会打——「网络指标漂亮但缺因果，结构洞 ≠ 法律责任，你怎么从拓扑跳到归责？」。",
    feasibility: "数据来源：平台隐私政策披露的数据共享清单 + App SDK 调用图谱（可用 AppInspect 类工具抓取）；样本约 200 个 App。",
    questions: [
      "拓扑中心性如何转译成法律上的「控制者」认定？",
    ],
    novelty: "中文面未检",
    gold: false,
    outlier: false,
    own_hits: 0,
    en_hits: 64,
    zh_hits: null,
    anchors: [
      { title: "Structural Holes and Information Control in Platform Ecosystems", url: "https://doi.org/10.1016/socnet.2020.064" },
    ],
    source_models: ["openai", "gemini"],
  },
  {
    id: 7,
    type: "学科视角",
    name: "复杂适应系统与监管的涌现失灵",
    mechanism: "平台生态是复杂适应系统，事前规则在涌现行为前必然滞后；规制应从「设定规则」转向「调节系统参数」。",
    why_nonobvious: "你假设好规则能预防危害；复杂系统视角指出涌现不可预测，规制目标应是韧性而非预防。",
    steelman: "法治主义审稿人会打——「『调参数』式规制违反法的明确性与可预期性，等于给监管者空白授权」。",
    feasibility: null,
    questions: [
      "韧性导向的规制如何与法的可预期性调和？",
      "「调节系统参数」在法律上如何取得授权正当性？",
    ],
    novelty: "交叉空白",
    gold: true,
    outlier: false,
    own_hits: 0,
    en_hits: 73,
    zh_hits: 3,
    anchors: [
      { title: "Governing Complex Adaptive Systems: Resilience over Prevention", url: "https://doi.org/10.1111/cas.2022.073" },
      { title: "复杂系统治理与监管韧性的法学转向", url: "https://kns.cnki.net/kcms/sample-7303" },
    ],
    source_models: ["deepseek", "gemini"],
  },
];

// 便于内联：export 兼容（浏览器里直接用全局变量即可，无模块系统时忽略下一行）
if (typeof module !== "undefined") { module.exports = { MUSE_TOPIC, MUSE_PROFILE, MUSE_CARDS }; }
