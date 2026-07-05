# 多源检索层（Perplexity + Jina + Tavily 混检）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让论文构思者的圆桌/批量流程可在 Tavily、Perplexity、混合多源之间切换检索源，并可选用 Jina Reader 把命中结果增强为全文证据；App、服务端、CLI 三个入口同步暴露该选择。

**Architecture:** 所有检索器都是 `knowledge_storm/rm.py` 里 `dspy.Retrieve` 的子类，遵循 `TavilySearchRM` 的既有契约（`forward(query_or_queries, exclude_urls) -> [{description, snippets:[str], title, url}]` + `get_usage_and_reset()`）。新增 `PerplexitySearchRM`（直连 Search API）、`JinaFullTextRM`（装饰任意 RM，检索后抓全文替换 snippets）、`MixedRM`（多源并联去重交错）。`muse_server.py` 加 `build_rm()` 工厂按请求参数装配，SwiftUI App 与 CLI 传字符串选源。

**Tech Stack:** Python 3.11（uv venv，无 pip 直装）、requests、pytest（新增 dev 依赖）、dspy（现有）、FastAPI（现有）、SwiftUI（app/）。

**现状盘点（2026-07-05）：**
- `TavilySearchRM` 已在用（圆桌 + 批量），有空 query 容错的本地修补（rm.py `forward` 内注释可见）。
- `pplx` / `jina` CLI 本机可用 → 两把 key 用户手里有，但 **secrets.toml 里还没有** `PERPLEXITY_API_KEY` / `JINA_API_KEY`。
- venv 无 pytest；仓库无 tests/ 目录。
- Perplexity Search API：`POST https://api.perplexity.ai/search`，Bearer 鉴权，body `{"query": "...", "max_results": N}`，响应 `results[]` 含 `title/url/snippet`（Context7 官方文档确认）。
- Jina Reader：`GET https://r.jina.ai/{url}`，Bearer 鉴权，`X-Max-Tokens` 截断、`X-Timeout` 控时，返回 markdown 纯文本（Context7 官方文档确认）。

**File Structure（本计划涉及的全部文件）：**
- Modify: `knowledge_storm/rm.py` — 追加三个 RM 类（文件末尾）
- Create: `tests/conftest.py` — 注入仓库根到 sys.path + 加载 secrets.toml
- Create: `tests/test_multisource_rm.py` — 三个 RM 的测试
- Modify: `muse_server.py` — `SessionReq` 加 `retriever`/`fulltext`，抽 `build_rm()` 工厂
- Modify: `examples/costorm_examples/run_costorm_deepseek.py` — `--retriever`/`--fulltext` 旗标
- Modify: `app/Sources/MuseClient.swift` — `createSession` 增参
- Modify: `app/Sources/RoundtableView.swift` — 设置页加检索源 Picker + 全文增强开关
- Modify: `secrets.toml.example` — 两把新 key 的模板与说明

---

### Task 0: 前置准备（key 入位 + pytest）

**Files:**
- Modify: `secrets.toml.example`
- Modify: `secrets.toml`（含真实 key，已 gitignore，用户手动填）

- [ ] **Step 1: secrets.toml.example 追加模板**

在 `# ============ retriever configurations ============` 小节的 Tavily 行后追加：

```toml
# Perplexity Search API（多源检索可选源）— https://docs.perplexity.ai
PERPLEXITY_API_KEY="pplx-YOUR_PERPLEXITY_KEY"

# Jina Reader（检索结果全文增强，可选）— https://jina.ai/reader
JINA_API_KEY="jina_YOUR_JINA_KEY"
```

- [ ] **Step 2: 用户动作——把真实 key 填进 secrets.toml**

本机 `pplx`、`jina` CLI 均可用，key 就在用户手里（`pplx` 读 `PERPLEXITY_API_KEY`，`jina` 读 `JINA_API_KEY`，通常在 shell 配置或各自 config 里）。把两行真实 key 追加到 `secrets.toml`。**没有这步，后续真实 API 测试会全部 skip，不算失败。**

- [ ] **Step 3: 装 pytest**

```bash
cd ~/Projects/paper-muse && VIRTUAL_ENV=.venv uv pip install pytest
.venv/bin/python -m pytest --version
```

Expected: 打出 pytest 版本号。

- [ ] **Step 4: Commit**

```bash
git add secrets.toml.example
git commit -m "chore: secrets 模板加 Perplexity/Jina key 占位"
```

---

### Task 1: PerplexitySearchRM

**Files:**
- Modify: `knowledge_storm/rm.py`（文件末尾追加类）
- Create: `tests/conftest.py`
- Create: `tests/test_multisource_rm.py`

- [ ] **Step 1: 建 tests/conftest.py**

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_storm.utils import load_api_key

load_api_key(toml_file_path=str(ROOT / "secrets.toml"))
```

- [ ] **Step 2: 写失败测试**

`tests/test_multisource_rm.py`：

```python
import os

import pytest

from knowledge_storm.rm import PerplexitySearchRM

needs_pplx = pytest.mark.skipif(
    not os.environ.get("PERPLEXITY_API_KEY"), reason="需要 PERPLEXITY_API_KEY"
)


@needs_pplx
def test_perplexity_rm_returns_storm_shaped_results():
    rm = PerplexitySearchRM(k=3)
    results = rm.forward("生成式人工智能 平台责任")
    assert results, "应至少返回一条结果"
    for r in results:
        assert {"description", "snippets", "title", "url"} <= set(r)
        assert isinstance(r["snippets"], list) and r["snippets"][0]
    assert rm.get_usage_and_reset() == {"PerplexitySearchRM": 1}


@needs_pplx
def test_perplexity_rm_skips_blank_query():
    rm = PerplexitySearchRM(k=2)
    assert rm.forward(["", "  "]) == []
```

- [ ] **Step 3: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_multisource_rm.py -v
```

Expected: `ImportError: cannot import name 'PerplexitySearchRM'`（key 未填则显示 2 skipped，同样先完成 Step 4）。

- [ ] **Step 4: 实现 PerplexitySearchRM**

`knowledge_storm/rm.py` 文件末尾追加（该文件顶部已 `import logging/os/requests/dspy`，无需新增 import）：

```python
class PerplexitySearchRM(dspy.Retrieve):
    """Retrieve information using the Perplexity Search API.

    POST https://api.perplexity.ai/search  body: {"query": ..., "max_results": k}
    响应 results[] 含 title/url/snippet。返回值与 TavilySearchRM 同构。
    """

    def __init__(self, perplexity_api_key=None, k: int = 3, is_valid_source: Callable = None):
        super().__init__(k=k)
        self.perplexity_api_key = perplexity_api_key or os.environ.get("PERPLEXITY_API_KEY")
        if not self.perplexity_api_key:
            raise RuntimeError(
                "You must supply perplexity_api_key or set environment variable PERPLEXITY_API_KEY"
            )
        self.k = k
        self.usage = 0
        self.is_valid_source = is_valid_source if is_valid_source else lambda x: True

    def get_usage_and_reset(self):
        usage = self.usage
        self.usage = 0
        return {"PerplexitySearchRM": usage}

    def forward(self, query_or_queries: Union[str, List[str]], exclude_urls: List[str] = []):
        queries = (
            [query_or_queries] if isinstance(query_or_queries, str) else query_or_queries
        )
        collected_results = []
        for query in queries:
            # 与 TavilySearchRM 同款容错：空 query 跳过、单次失败不拖垮长流程
            if not query or not query.strip():
                continue
            self.usage += 1
            try:
                resp = requests.post(
                    "https://api.perplexity.ai/search",
                    headers={"Authorization": f"Bearer {self.perplexity_api_key}"},
                    json={"query": query, "max_results": self.k},
                    timeout=30,
                )
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    url = r.get("url", "")
                    if not url or not self.is_valid_source(url) or url in exclude_urls:
                        continue
                    snippet = r.get("snippet") or r.get("title", "")
                    collected_results.append(
                        {
                            "description": snippet,
                            "snippets": [snippet],
                            "title": r.get("title", ""),
                            "url": url,
                        }
                    )
            except Exception as e:
                logging.error(f"PerplexitySearchRM error for query '{query}': {e}")
        return collected_results
```

注意：`test_perplexity_rm_returns_storm_shaped_results` 断言 usage==1，而空 query 测试要求空串不计数——所以 `self.usage += 1` 必须放在空 query 判断**之后**（如上）。

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_multisource_rm.py -v
```

Expected: 2 passed（key 未填则 2 skipped——先回 Task 0 Step 2）。

- [ ] **Step 6: Commit**

```bash
git add knowledge_storm/rm.py tests/conftest.py tests/test_multisource_rm.py
git commit -m "feat: 加 PerplexitySearchRM（Perplexity Search API 检索源）"
```

---

### Task 2: JinaFullTextRM（全文增强装饰器）

**Files:**
- Modify: `knowledge_storm/rm.py`（末尾追加）
- Modify: `tests/test_multisource_rm.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

`tests/test_multisource_rm.py` 末尾追加：

```python
from knowledge_storm.rm import JinaFullTextRM

needs_jina = pytest.mark.skipif(
    not (os.environ.get("JINA_API_KEY") and os.environ.get("PERPLEXITY_API_KEY")),
    reason="需要 JINA_API_KEY + PERPLEXITY_API_KEY",
)


@needs_jina
def test_jina_fulltext_enriches_top_results():
    base = PerplexitySearchRM(k=2)
    rm = JinaFullTextRM(base_rm=base, top_n=1, max_tokens=1500)
    results = rm.forward("欧盟人工智能法案 高风险系统 义务")
    assert results
    top = results[0]
    # 全文增强后 top1 的 snippets 应明显厚于一条搜索摘要
    assert sum(len(s) for s in top["snippets"]) > 600
    usage = rm.get_usage_and_reset()
    assert "JinaFullTextRM" in usage and "PerplexitySearchRM" in usage
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_multisource_rm.py::test_jina_fulltext_enriches_top_results -v
```

Expected: `ImportError: cannot import name 'JinaFullTextRM'`。

- [ ] **Step 3: 实现 JinaFullTextRM**

`knowledge_storm/rm.py` 末尾追加：

```python
class JinaFullTextRM(dspy.Retrieve):
    """包装任意 RM：检索后用 Jina Reader (GET https://r.jina.ai/{url}) 抓取
    前 top_n 条结果的正文 markdown，切成 ~1000 字符的 snippets 替换原摘要，
    给圆桌更厚的证据。抓取失败时保留原 snippets，绝不让增强步骤拖垮检索。
    """

    SNIPPET_CHUNK = 1000
    MAX_CHUNKS = 3

    def __init__(self, base_rm, top_n: int = 3, max_tokens: int = 4000, jina_api_key=None):
        super().__init__(k=base_rm.k)
        self.base_rm = base_rm
        self.top_n = top_n
        self.max_tokens = max_tokens
        self.jina_api_key = jina_api_key or os.environ.get("JINA_API_KEY")
        if not self.jina_api_key:
            raise RuntimeError(
                "You must supply jina_api_key or set environment variable JINA_API_KEY"
            )
        self.usage = 0

    def get_usage_and_reset(self):
        merged = self.base_rm.get_usage_and_reset()
        merged["JinaFullTextRM"] = self.usage
        self.usage = 0
        return merged

    def _read(self, url: str) -> str:
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers={
                "Authorization": f"Bearer {self.jina_api_key}",
                "X-Max-Tokens": str(self.max_tokens),
                "X-Timeout": "20",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text

    def forward(self, query_or_queries: Union[str, List[str]], exclude_urls: List[str] = []):
        results = self.base_rm.forward(query_or_queries, exclude_urls)
        for r in results[: self.top_n]:
            try:
                full_text = self._read(r["url"])
                if len(full_text) > 200:
                    self.usage += 1
                    limit = self.SNIPPET_CHUNK * self.MAX_CHUNKS
                    r["snippets"] = [
                        full_text[i : i + self.SNIPPET_CHUNK]
                        for i in range(0, min(len(full_text), limit), self.SNIPPET_CHUNK)
                    ]
            except Exception as e:
                logging.warning(f"JinaFullTextRM read failed for {r.get('url')}: {e}")
        return results
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_multisource_rm.py -v
```

Expected: 3 passed（或相应 skipped）。

- [ ] **Step 5: Commit**

```bash
git add knowledge_storm/rm.py tests/test_multisource_rm.py
git commit -m "feat: 加 JinaFullTextRM 全文增强装饰器（r.jina.ai）"
```

---

> **评审修订（2026-07-05，Task 1-2 质量评审后并入 Task 3 执行）：**
> 1. `JinaFullTextRM.max_tokens` 默认 4000 → **1200**（抓取预算应对齐保留预算 `SNIPPET_CHUNK×MAX_CHUNKS=3000` 字符，默认 4000 token≈16k 字符属静默浪费）；
> 2. 测试文件中部的 `from knowledge_storm.rm import JinaFullTextRM` 提升到文件顶部 import 区；
> 3. 增补离线降级测试：monkeypatch `knowledge_storm.rm.requests.get` 抛 ConnectionError，断言 JinaFullTextRM 包着 stub base 时 snippets 原样保留、`usage["JinaFullTextRM"]==0`——锁死「只增强、永不劣化」保证。

### Task 3: MixedRM（多源并联）

**Files:**
- Modify: `knowledge_storm/rm.py`（末尾追加）
- Modify: `tests/test_multisource_rm.py`（追加纯单元测试，不需要任何 key）

- [ ] **Step 1: 追加失败测试（stub RM，离线可跑）**

`tests/test_multisource_rm.py` 末尾追加：

```python
from knowledge_storm.rm import MixedRM


class _StubRM:
    def __init__(self, name, urls):
        self.k = len(urls)
        self._name = name
        self._urls = urls

    def get_usage_and_reset(self):
        return {self._name: 1}

    def forward(self, query_or_queries, exclude_urls=[]):
        return [
            {"description": u, "snippets": [u], "title": u, "url": u}
            for u in self._urls
        ]


def test_mixed_rm_interleaves_and_dedups():
    a = _StubRM("A", ["u1", "u2", "u3"])
    b = _StubRM("B", ["u2", "u4"])
    rm = MixedRM([a, b])
    urls = [r["url"] for r in rm.forward("任意查询")]
    # 逐位交错：i=0 取 a=u1, b=u2；i=1 取 a=u2(重复丢弃), b=u4；i=2 取 a=u3
    assert urls == ["u1", "u2", "u4", "u3"]
    assert rm.get_usage_and_reset() == {"A": 1, "B": 1}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_multisource_rm.py::test_mixed_rm_interleaves_and_dedups -v
```

Expected: `ImportError: cannot import name 'MixedRM'`。

- [ ] **Step 3: 实现 MixedRM**

`knowledge_storm/rm.py` 末尾追加：

```python
class MixedRM(dspy.Retrieve):
    """并联多个 RM：逐源检索，按 URL 去重、逐位交错合并，保证来源多样性。
    ponytail: 顺序请求 + 简单交错，不做语义精排；要精排时在合并后接
    Jina reranker（https://jina.ai/reranker）升级。
    """

    def __init__(self, rms: List[dspy.Retrieve]):
        if not rms:
            raise ValueError("MixedRM 至少需要一个子检索器")
        super().__init__(k=max(rm.k for rm in rms))
        self.rms = rms

    def get_usage_and_reset(self):
        merged = {}
        for rm in self.rms:
            merged.update(rm.get_usage_and_reset())
        return merged

    def forward(self, query_or_queries: Union[str, List[str]], exclude_urls: List[str] = []):
        per_source = []
        for rm in self.rms:
            try:
                per_source.append(rm.forward(query_or_queries, exclude_urls))
            except Exception as e:
                logging.error(f"MixedRM sub-retriever {type(rm).__name__} failed: {e}")
                per_source.append([])
        seen, merged = set(), []
        for i in range(max((len(r) for r in per_source), default=0)):
            for results in per_source:
                if i < len(results):
                    url = results[i]["url"]
                    if url not in seen:
                        seen.add(url)
                        merged.append(results[i])
        return merged
```

- [ ] **Step 4: 跑全部测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_multisource_rm.py -v
```

Expected: 4 passed（无 key 时 3 skipped + 1 passed——MixedRM 测试必须绿）。

- [ ] **Step 5: Commit**

```bash
git add knowledge_storm/rm.py tests/test_multisource_rm.py
git commit -m "feat: 加 MixedRM 多源并联检索（URL 去重 + 交错合并）"
```

---

### Task 4: muse_server 暴露检索源选择

**Files:**
- Modify: `muse_server.py`

- [ ] **Step 1: 改 import 与 SessionReq**

`muse_server.py` 里 `from knowledge_storm.rm import TavilySearchRM` 改为：

```python
from knowledge_storm.rm import (
    TavilySearchRM,
    PerplexitySearchRM,
    JinaFullTextRM,
    MixedRM,
)
```

`SessionReq` 增加两个字段（放在 `output_dir` 之前）：

```python
    retriever: str = "tavily"       # tavily | perplexity | mixed
    fulltext: bool = False          # True = Jina Reader 全文增强 top3
```

- [ ] **Step 2: 抽 build_rm 工厂并替换 build_runner 里的 rm 构造**

`build_runner` 中原来的 `rm = TavilySearchRM(...)` 三行替换为 `rm = build_rm(req, runner_argument.retrieve_top_k)`，并在 `build_runner` 上方新增：

```python
def build_rm(req: "SessionReq", k: int):
    def tavily():
        return TavilySearchRM(
            tavily_search_api_key=os.getenv("TAVILY_API_KEY"),
            k=k,
            include_raw_content=True,
        )

    if req.retriever == "tavily":
        base = tavily()
    elif req.retriever == "perplexity":
        base = PerplexitySearchRM(k=k)
    elif req.retriever == "mixed":
        base = MixedRM([tavily(), PerplexitySearchRM(k=k)])
    else:
        raise RuntimeError(f"未知检索源 {req.retriever}（可选 tavily / perplexity / mixed）")
    if req.fulltext:
        base = JinaFullTextRM(base_rm=base, top_n=3)
    return base
```

（`build_runner` 抛的 RuntimeError 会被 `warm_start_bg` 捕获进 `SESSION["error"]`，App 端已有展示路径，无需新错误处理。）

- [ ] **Step 3: 快速验证——起服务打一发 perplexity 会话**

```bash
cd ~/Projects/paper-muse && .venv/bin/python muse_server.py --port 8765 &
sleep 5
curl -s -X POST http://127.0.0.1:8765/session -H 'Content-Type: application/json' \
  -d '{"topic":"数据跨境流动的安全评估","retriever":"perplexity","warmstart_experts":1,"warmstart_turns":1,"retrieve_top_k":3}'
# 轮询直到 ready（约 2 分钟）：
watch -n 5 'curl -s http://127.0.0.1:8765/status | head -c 200'
```

Expected: `phase` 走到 `ready` 且 `turns` 非空；结束后 `kill $(lsof -ti tcp:8765)`。

- [ ] **Step 4: Commit**

```bash
git add muse_server.py
git commit -m "feat: muse_server 支持 retriever/fulltext 检索源选择"
```

---

### Task 5: App 设置页加检索源选择

**Files:**
- Modify: `app/Sources/MuseClient.swift`
- Modify: `app/Sources/RoundtableView.swift`

- [ ] **Step 1: MuseClient.createSession 增参**

`createSession` 整个方法替换为：

```swift
    func createSession(topic: String, model: String, retriever: String, fulltext: Bool) async throws {
        struct Req: Encodable {
            let topic: String
            let model: String
            let retriever: String
            let fulltext: Bool
        }
        _ = try await post(
            "session",
            body: Req(topic: topic, model: model, retriever: retriever, fulltext: fulltext),
            timeout: 30
        )
    }
```

- [ ] **Step 2: RoundtableViewModel 增状态并传参**

`RoundtableViewModel` 的 `@Published var usePro = false` 下面加：

```swift
    @Published var retriever = "tavily"
    @Published var fulltext = false
```

`start()` 里 `client.createSession(` 调用替换为：

```swift
                try await client.createSession(
                    topic: t,
                    model: usePro ? "deepseek-v4-pro" : "deepseek-v4-flash",
                    retriever: retriever,
                    fulltext: fulltext
                )
```

- [ ] **Step 3: setup 视图加 Picker 与开关**

`setup` 里 `Toggle("深度模式…)` 那行的**上方**插入：

```swift
            Picker("检索源", selection: $vm.retriever) {
                Text("网络快搜（Tavily）").tag("tavily")
                Text("深度搜索（Perplexity）").tag("perplexity")
                Text("双源混合").tag("mixed")
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 460)
            Toggle("Jina 全文增强（证据更厚，热身稍慢）", isOn: $vm.fulltext)
                .toggleStyle(.checkbox)
```

- [ ] **Step 4: 重建并启动验证**

```bash
cd ~/Projects/paper-muse/app && xcodegen generate && \
xcodebuild -project PaperMuse.xcodeproj -scheme PaperMuse -configuration Debug -derivedDataPath build build 2>&1 | grep -E "error:|BUILD"
open build/Build/Products/Debug/PaperMuse.app
```

Expected: `BUILD SUCCEEDED`；App 设置页出现三段式检索源选择 + 全文增强开关。

- [ ] **Step 5: Commit**

```bash
git add app/Sources/MuseClient.swift app/Sources/RoundtableView.swift
git commit -m "feat: App 设置页支持检索源与 Jina 全文增强选择"
```

---

### Task 6: CLI（muse.command 圆桌 REPL）同步

**Files:**
- Modify: `examples/costorm_examples/run_costorm_deepseek.py`

- [ ] **Step 1: import 与旗标**

`from knowledge_storm.rm import TavilySearchRM` 改为：

```python
from knowledge_storm.rm import (
    TavilySearchRM,
    PerplexitySearchRM,
    JinaFullTextRM,
    MixedRM,
)
```

argparse 部分（`--retrieve-top-k` 之后）追加：

```python
    parser.add_argument(
        "--retriever",
        type=str,
        choices=["tavily", "perplexity", "mixed"],
        default="tavily",
        help="检索源：tavily 快 / perplexity 深 / mixed 双源混合",
    )
    parser.add_argument(
        "--fulltext",
        action="store_true",
        help="用 Jina Reader 把 top3 结果增强为全文（需 JINA_API_KEY）",
    )
```

- [ ] **Step 2: main() 里替换 rm 构造**

原 `rm = TavilySearchRM(...)` 四行替换为：

```python
    def _tavily():
        return TavilySearchRM(
            tavily_search_api_key=os.getenv("TAVILY_API_KEY"),
            k=runner_argument.retrieve_top_k,
            include_raw_content=True,
        )

    if args.retriever == "perplexity":
        rm = PerplexitySearchRM(k=runner_argument.retrieve_top_k)
    elif args.retriever == "mixed":
        rm = MixedRM([_tavily(), PerplexitySearchRM(k=runner_argument.retrieve_top_k)])
    else:
        rm = _tavily()
    if args.fulltext:
        rm = JinaFullTextRM(base_rm=rm, top_n=3)
```

同时把文件头 docstring 的 key 列表补上 `PERPLEXITY_API_KEY / JINA_API_KEY（可选）`。

- [ ] **Step 3: 冒烟**

```bash
cd ~/Projects/paper-muse && printf '\nq\n' | .venv/bin/python \
  examples/costorm_examples/run_costorm_deepseek.py \
  --topic "算法推荐的透明度义务" --retriever perplexity \
  --warmstart-experts 1 --warmstart-turns 1 --retrieve-top-k 3 2>&1 | tail -15
```

Expected: 热身完成、一轮发言、`已保存：…report.md`。

- [ ] **Step 4: Commit**

```bash
git add examples/costorm_examples/run_costorm_deepseek.py
git commit -m "feat: 圆桌 REPL 支持 --retriever/--fulltext"
```

---

### Task 7: 端到端验收

- [ ] **Step 1: 全量测试**

```bash
cd ~/Projects/paper-muse && .venv/bin/python -m pytest tests/ -v
```

Expected: 全绿（缺 key 项 skipped 并注明原因）。

- [ ] **Step 2: App 真实走一遍混合源圆桌**

打开 PaperMuse.app → 检索源选「双源混合」+ 勾「Jina 全文增强」→ 输入真实在写的论文主题 → 圆桌就绪后插话一次 → 结束出报告 → 「在访达中打开」确认 `costorm_<主题>/report.md` 引用条目里同时出现不同来源域名。

- [ ] **Step 3: Commit（如有零星修补）后停**

不 push；是否推 GitHub 由用户跑 `/update-github` 决定。

---

## 附录：本计划之外的后续方向（各自单独立计划，勿混入本计划执行）

1. **Zotero 自有语料入圆桌**：`VectorRM`（qdrant 离线库，`tools/zotero_to_storm_csv.py` 已能导语料）作为第四检索源接进 `build_rm`，与 web 源混检——圆桌专家直接引用你自己的文献库。先跑 `git show 298e198 --stat` 与 `ls results/vectorrm_smoke` 确认既有向量库路径与 embedding 参数，再立计划。
2. **questions.md 文件契约**：`/report` 时把 Moderator 的问题单独抽成 `questions.md`（含文献依据），供 `grill-with-docs` 当拷问弹药；配套在 xw-writing 侧加读取规则。
3. **思维导图侧栏**：App 里可视化 `instance_dump.json` 的 knowledge_base 树。
4. **anamra 正式修复**：`PaperToolsLauncher.swift:19` 改 `Projects/paper-muse` + 面板加 PaperMuse.app 入口，发版后删 `~/Projects/storm` 软链（已挂 agent-memory open-loops）。
5. **Jina reranker 精排**：`MixedRM` 合并后接 rerank——等混检真用出「结果杂」的痛感再做（类注释已留升级点）。
