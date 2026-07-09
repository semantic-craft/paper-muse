# paper-muse 性能改进 PRD

日期：2026-07-09
状态：实现已落地；真实付费端到端冒烟按需运行 `tools/perf_smoke.py --scan` / `--adversary-line` 复核
范围：构思幕扫描、圆桌检索、对抗幕证伪、Web 画布状态更新、自有库证据层

## Problem Statement

paper-muse 已经具备构思幕、圆桌深挖、对抗幕的主路径，但性能仍受外部 I/O 和本地编排方式制约。研究者需要在中文法学主题上快速看到可判断的卡片，并在需要时补挂中文学界、自有库、证伪证据；当前风险是部分昂贵步骤默认进入首要路径，重复检索缺少缓存，对抗幕 sidecar 每主张冷启动，状态接口反复全量传输，导致真实使用时延和 API 花费不可控。

用户视角的问题不是“某个函数慢”，而是：

- 首屏发现力要稳定进入可用状态，不能被尾部 CNKI/全文/深研步骤拖住。
- 同一个主题反复试扫时，不应重复花钱重复等待。
- 对抗幕要能做严肃证伪，但不应每条主张都付一次 Python + gpt-researcher 冷启动成本。
- Web 画布应只更新变化，而不是每 1.5 秒重传和重绘整面状态。
- 性能优化不能削弱既有红线：中文面降级明示、无证据即未决、研究者相对新颖性、七件文件契约。

## Solution

把性能路线拆成四条原则：

1. **先上墙，后补证据**：首屏只等必要 LLM 枚举；英文学术计数、自有库命中、CNKI、全文证据、证伪报告都异步补挂。
2. **能缓存就缓存**：对外部检索、学术计数、自有库查询、稳定 prompt 响应用本地磁盘缓存；缓存命中必须可观测。
3. **重进程常驻化**：gpt-researcher 仍保留隔离环境，但从“每主张启动一次”改成“一场审查复用一个 worker / 批量请求”。
4. **小改动优先**：先修现有参数、缓存、预算、增量状态；不迁移到 deep research harness，不替换 FastAPI + WebView 壳。

## User Stories

1. As a researcher, I want the first useful blindspot cards to appear within the existing 20s target, so that I can start judging angles before all evidence finishes.
2. As a researcher, I want all non-CNKI card content to settle within the existing 90s wall target, so that the scan feels usable even when CNKI is still tailing.
3. As a researcher, I want CNKI badges to be prioritized for the most promising cards, so that slow browser-bound searches spend time where they change my decision.
4. As a researcher, I want repeated scans of the same or similar topic to reuse stable search results, so that iteration is cheaper and faster.
5. As a researcher, I want `en_hits` to mean real scholarly density, not top-k saturation, so that “英热中冷” is a meaningful signal.
6. As a researcher, I want English scholarly counts to degrade clearly when keys or rate limits fail, so that I know whether a card was actually checked.
7. As a researcher, I want roundtable retrieval to request only the amount of Tavily content it actually uses, so that search latency and API cost stay bounded.
8. As a researcher, I want full text enrichment to be opt-in and narrow, so that normal roundtable turns are not slowed by unnecessary page extraction.
9. As a researcher, I want adversarial review to start once and process claims efficiently, so that reviewing a draft does not feel like launching a new deep-research job per claim.
10. As a researcher, I want adversarial review to preserve “未决 = 不放行,” so that speedups do not turn missing evidence into false confidence.
11. As a researcher, I want the UI to update only when cards or claims change, so that long reviews do not cause unnecessary redraw and payload churn.
12. As a researcher, I want product files to keep the same seven-file contract, so that downstream writing skills and paper-annotator keep working.
13. As a researcher, I want self-library evidence to come from cited PDF passages during deep review, so that own_hits can graduate from a badge to usable support.
14. As a researcher, I want performance budgets to be visible in smoke output, so that regressions are caught before adding new features.
15. As a maintainer, I want each performance change to be testable through existing engine/API seams, so that small optimizations do not require broad rewrites.

## Implementation Decisions

### P0: Fix Known Waste Before Adding Machinery

- Tavily search parameters must be passed through explicitly. `max_results`, `include_raw_content`, and search depth directly affect response size, cost, and latency; the wrapper must not build those values and then ignore them.
- Raw/full content retrieval remains opt-in. Default roundtable search should use bounded snippets; full text should be an explicit enhancement path.
- Retrieval caching uses the already-installed `diskcache` dependency first. Do not add Redis, a gateway, or a new background service for a single-user local app.
- Cache keys should include normalized query, retriever name, result limit, and relevant mode flags. Cache entries must distinguish success, true zero results, and degraded/unavailable.
- The first cache pass should cover English search/counts and zsearch. CNKI may cache true success/true empty results with a conservative TTL, but session/failure states must not be cached as if they were scholarly facts.

### P0: Make Scholarly Density Cheap And Meaningful

- `en_hits` should move from generic top-k web search to academic-source count semantics. Existing issue #5 is the canonical implementation ticket.
- OpenAlex/pyalex is the cheap broad leg; Semantic Scholar is the higher-precision leg with stricter rate limits. The UI should expose degraded states rather than fabricating counts.
- The Owl-style “Has anyone …?” formulation is a query-normalization pattern, not a dependency on FutureHouse/Edison cloud services.

### P1: Bound Slow Evidence Work

- CNKI stays self-built because it is the Chinese legal scholarship differentiator, but it must run under a visible budget. The default should prioritize top cards rather than serially checking every card before considering a scan operationally complete.
- CNKI priority should use signals already present: card emission order, outlier/gold potential, user focus, and own_hits/en_hits once available. Do not build a new ranking subsystem for this PRD.
- Mixed retrievers and full-text enrichers should run small I/O concurrency where safe. Use the standard library executor before introducing async rewrites.

### P1: Reuse Heavy Sidecar State

- gpt-researcher remains in `.venv-gptr` to avoid dependency pollution.
- The sidecar should process a whole adversarial review session through one long-lived worker or one batch invocation. It should not cold-start per claim when multiple claims are reviewed together.
- The review engine remains the authority for verdicts. The sidecar only returns sources, memo text, and density metadata.
- GPT Researcher MCP/custom retriever support remains the preferred integration direction for CNKI/zsearch, but the short-term implementation can keep the current in-process patch if it is simpler.

### P1: Incremental Status Delivery

- Keep polling as the first implementation if a version field avoids redundant render and payload work. SSE is the next step only if polling remains visibly wasteful.
- Status responses should carry a monotonic version or updated-at value. If unchanged, the server can return a cheap unchanged response or the client can skip render.
- Large static fields, especially draft source text, should not be resent every poll after the client has received them.

### P2: Evidence Depth Belongs Behind User Intent

- zsearch remains the fast own_hits badge mechanism.
- PaperQA/PaperQA2 should power deep evidence answers and cited self-library passages after the user opens a card, starts a roundtable, or runs adversarial review. Existing issue #11 is the canonical implementation ticket.
- PaperQA indexing should be persistent and pinned by version; repeated queries over the same PDF directory should skip re-indexing except for changed files.

## Testing Decisions

- Test at the highest useful seam: API-level behavior for `/scan`, `/adversary`, and roundtable session flows; pure engine tests only for deterministic policy functions.
- Existing unit tests around blindspot streaming, CNKI true empty handling, evidence verdicts, and multi-source RM behavior are the prior art.
- Each performance slice should include the smallest regression check that would fail if the optimization silently stopped working:
  - Tavily wrapper passes search parameters to the client.
  - Cached retrievers avoid a second underlying call for the same normalized query.
  - Academic en_hits can distinguish total scholarly counts from top-k result length.
  - CNKI priority budget leaves low-priority cards pending/degraded without blocking the wall.
  - Sidecar session processes multiple claims without multiple cold-start invocations.
  - Status polling skips render when version is unchanged and does not resend source text repeatedly.
- Real paid/API smoke remains separate from unit tests. The smoke record should report first-card time, wall-complete time, CNKI-tail time, LLM cache hits, search cache hits, sidecar startup count, and API degradation states.

## Out of Scope

- No migration to deer-flow, LangGraph, smolagents, or another generic deep research harness.
- No Redis, queue service, database, or cloud deployment.
- No change to the product’s single-user local assumption.
- No removal of CNKI or zsearch.
- No relaxation of adversarial review safety: missing evidence remains “未决.”
- No broad UI redesign; incremental state delivery should preserve current visual behavior.
- No full PaperQA replacement for zsearch badges.

## Issue Breakdown

### Existing Issues To Reuse

- #5 `[M2] en_hits 换学术源：semanticscholar＋pyalex 双路＋Owl 问式规范`
- #11 `[M4] own_hits 证据层：paper-qa 底座（Zotero PDF 索引→sources.md 契约）`
- #15 `[M0-bug] 扫描时延架构...` for CNKI tail-state semantics and any top-N/priority budget decision

### New Vertical Slices

1. #18 Fix Tavily parameter pass-through and default retrieval budget.
2. #19 Add disk-backed cache for stable retrieval results.
3. #20 Add small I/O concurrency for mixed retrieval and full-text enrichment.
4. #21 Reuse gpt-researcher sidecar state across one adversarial review session.
5. #22 Add versioned/incremental status snapshots for scan and adversary polling.
6. #23 Add performance smoke readout for the end-to-end run.

## Further Notes

External sources checked during the performance review:

- Tavily Python SDK: `max_results` and `include_raw_content` must be set explicitly because they affect response size and latency.
- LiteLLM cache docs: response caching supports disk cache and is intended to reduce repeated latency and cost.
- FastAPI docs: `StreamingResponse` is available if polling plus versioning is insufficient.
- sse-starlette: a production-ready SSE implementation exists, but it is optional for this single-user app.
- GPT Researcher docs: MCP/custom retriever patterns support hybrid web + specialized data sources and expose fast/deep strategy tradeoffs.
- PaperQA docs: local PDF directories are indexed persistently; repeated queries skip unchanged indexing/chunking.
