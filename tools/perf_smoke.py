#!/usr/bin/env python3
"""Read paper-muse performance counters and optionally run local smoke flows.

Usage:
    .venv/bin/python tools/perf_smoke.py
    .venv/bin/python tools/perf_smoke.py --scan --topic "平台数据权力"
    .venv/bin/python tools/perf_smoke.py --adversary-line "算法透明度必然提升司法公正"
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any
from urllib import error, request


def _json_request(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None,
                  timeout: float = 30) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    return json.loads(raw.decode("utf-8") or "{}")


def _safe_request(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None,
                  timeout: float = 30) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return _json_request(base_url, method, path, payload, timeout), None
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # pragma: no cover - smoke tool should report, not crash.
        return None, str(exc)


def _perf(base_url: str) -> dict[str, Any]:
    body, err = _safe_request(base_url, "GET", "/perf/status", timeout=10)
    return {"error": err} if err else body or {}


def _stats_delta(before: dict[str, Any], after: dict[str, Any], key: str) -> dict[str, int]:
    left = before.get(key) or {}
    right = after.get(key) or {}
    delta: dict[str, int] = {}
    for name, value in right.items():
        if isinstance(value, int):
            delta[name] = value - int(left.get(name, 0) or 0)
    return delta


def _wait_for_scan(base_url: str, timeout_s: float, interval_s: float) -> dict[str, Any]:
    started = time.monotonic()
    first_card_s = None
    non_cnki_ready_s = None
    final: dict[str, Any] = {}

    while time.monotonic() - started < timeout_s:
        body, err = _safe_request(base_url, "GET", "/scan/status", timeout=20)
        if err:
            return {"phase": "error", "error": err}
        final = body or {}
        cards = final.get("cards") or []
        elapsed = time.monotonic() - started
        if cards and first_card_s is None:
            first_card_s = elapsed
        if cards and non_cnki_ready_s is None:
            if all(c.get("en_hits") is not None and c.get("own_hits") is not None for c in cards):
                non_cnki_ready_s = elapsed
        if final.get("phase") in {"done", "error"}:
            break
        time.sleep(interval_s)
    else:
        final = {"phase": "timeout", "error": f"scan did not finish within {timeout_s:.0f}s"}

    cards = final.get("cards") or []
    elapsed = time.monotonic() - started
    return {
        "phase": final.get("phase"),
        "error": final.get("error"),
        "cards": len(cards),
        "first_card_s": round(first_card_s, 2) if first_card_s is not None else None,
        "non_cnki_ready_s": round(non_cnki_ready_s, 2) if non_cnki_ready_s is not None else None,
        "final_s": round(elapsed, 2),
        "cnki_tail_s": round(elapsed - non_cnki_ready_s, 2) if non_cnki_ready_s is not None else None,
        "zh_true_empty_cards": sum(1 for c in cards if c.get("zh_hits") == 0),
        "zh_degraded_cards": sum(1 for c in cards if c.get("zh_hits") is None),
    }


def _wait_for_adversary(base_url: str, timeout_s: float, interval_s: float) -> dict[str, Any]:
    started = time.monotonic()
    final: dict[str, Any] = {}

    while time.monotonic() - started < timeout_s:
        body, err = _safe_request(base_url, "GET", "/adversary/status", timeout=20)
        if err:
            return {"phase": "error", "error": err}
        final = body or {}
        if final.get("phase") in {"done", "error"}:
            break
        time.sleep(interval_s)
    else:
        final = {"phase": "timeout", "error": f"adversary did not finish within {timeout_s:.0f}s"}

    claims = final.get("claims") or []
    return {
        "phase": final.get("phase"),
        "error": final.get("error"),
        "claims": len(claims),
        "failures": sum(len(c.get("failures") or []) for c in claims),
        "final_s": round(time.monotonic() - started, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="paper-muse local performance smoke readout")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--topic", default="平台经济中的数据权力与法律规制")
    parser.add_argument("--scan", action="store_true", help="start and measure one blindspot scan")
    parser.add_argument("--adversary-line", help="start and measure one line-mode adversarial review")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--interval", type=float, default=1.5)
    args = parser.parse_args()

    result: dict[str, Any] = {"base_url": args.base_url}
    health, health_err = _safe_request(args.base_url, "GET", "/health", timeout=10)
    result["health"] = {"error": health_err} if health_err else health
    release_health, release_health_err = _safe_request(args.base_url, "GET", "/release/health", timeout=10)
    result["release_health"] = {"error": release_health_err} if release_health_err else release_health
    before = _perf(args.base_url)
    result["perf_before"] = before
    # #49：smoke 读数在顶层携带被测代码版本——旧读数（不同 code_version）不能冒充验证了新代码
    result["code_version"] = before.get("code_version", "") if isinstance(before, dict) else ""
    if health_err:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    if args.scan:
        _, err = _safe_request(args.base_url, "POST", "/scan", {"topic": args.topic}, timeout=20)
        result["scan"] = {"phase": "error", "error": err} if err else _wait_for_scan(
            args.base_url, args.timeout, args.interval)

    if args.adversary_line:
        _, err = _safe_request(
            args.base_url,
            "POST",
            "/adversary",
            {"mode": "line", "line": args.adversary_line},
            timeout=20,
        )
        result["adversary"] = {"phase": "error", "error": err} if err else _wait_for_adversary(
            args.base_url, args.timeout, args.interval)

    after = _perf(args.base_url)
    result["perf_after"] = after
    result["perf_delta"] = {
        "retrieval_cache": _stats_delta(before, after, "retrieval_cache"),
        "sidecar": _stats_delta(before, after, "sidecar"),
        "llm_cache": after.get("llm_cache") or before.get("llm_cache"),
    }
    if not args.scan and not args.adversary_line:
        result["note"] = "pass --scan and/or --adversary-line to run end-to-end work"

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
