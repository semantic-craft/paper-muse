#!/usr/bin/env python3
"""从 Zotero collection 导出成 STORM VectorRM 的 CSV（content/title/url/description 列）。

用法:
    python tools/zotero_to_storm_csv.py <collection_key> <output.csv> [--abstract-only]

- content: 默认抓 PDF 全文（zfulltext excerpt），无全文则退回 abstract；
           --abstract-only 只用摘要做 content（快，省去逐条抓全文）。
- url:     STORM 用 url 作文档唯一标识，必须唯一且非空 →
           url 优先，否则 doi:<doi>，再否则 zotero:<key>。
- 依赖本地 zsearch / zfulltext（zotero-cli-agent）；脚本本身不含任何 key。

collection_key 怎么找：`zsearch ls`（不带参数）列出所有 collection 及其 key。

导出后灌库 + 出文:
    python examples/storm_examples/run_storm_wiki_gpt_with_VectorRM.py \\
        --csv-file-path <output.csv> --collection-name <任意名> \\
        --vector-db-mode offline --offline-vector-db-dir ./vdb --device mps \\
        --do-research --do-generate-outline --do-generate-article --do-polish-article
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from typing import List, Dict


def _run(cmd: List[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def collection_item_keys(coll_key: str) -> List[str]:
    """从 `zsearch ls <coll_key>` 表格里提取 8 字符 item key（排除 collection key 本身）。"""
    out = _run(["zsearch", "ls", coll_key])
    return [k for k in re.findall(r"\b[A-Z0-9]{8}\b", out) if k != coll_key]


def item_meta(key: str) -> Dict:
    out = _run(["zsearch", "get", key, "--json"])
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return {}


def item_fulltext(key: str) -> str:
    """拼接 zfulltext excerpt 的所有 chunk（按 chunk_idx 排序）= 完整 PDF 全文。"""
    out = _run(["zfulltext", "excerpt", key, "--json"])
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return ""
    chunks = data if isinstance(data, list) else data.get("chunks", [])
    chunks = sorted(chunks, key=lambda c: c.get("chunk_idx", 0))
    return "\n".join(c.get("text", "") for c in chunks).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Zotero collection → STORM VectorRM CSV")
    ap.add_argument("collection_key", help="Zotero collection key（`zsearch ls` 查）")
    ap.add_argument("output_csv")
    ap.add_argument(
        "--abstract-only",
        action="store_true",
        help="只用摘要做 content，不逐条抓 PDF 全文（快）",
    )
    args = ap.parse_args()

    keys = collection_item_keys(args.collection_key)
    if not keys:
        print(
            f"collection {args.collection_key} 没有条目（或 key 错；用 `zsearch ls` 查）",
            file=sys.stderr,
        )
        return 1
    print(f"collection {args.collection_key}: {len(keys)} 条，开始导出…")

    rows, skipped = [], 0
    for i, key in enumerate(keys, 1):
        meta = item_meta(key)
        title = (meta.get("title") or "").strip()
        abstract = (meta.get("abstract") or "").strip()
        fulltext = "" if args.abstract_only else item_fulltext(key)
        content = fulltext or abstract
        if not content:
            skipped += 1
            continue
        # STORM 用 url 做唯一标识，保证非空且唯一
        url = meta.get("url") or (
            f"doi:{meta['doi']}" if meta.get("doi") else f"zotero:{key}"
        )
        rows.append(
            {
                "content": content,
                "title": title or key,
                "url": url,
                "description": abstract,
            }
        )
        print(f"  [{i}/{len(keys)}] {'全文' if fulltext else '摘要'} | {title[:36]}")

    with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["content", "title", "url", "description"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"✅ 导出 {len(rows)} 条 → {args.output_csv}（跳过 {skipped} 条无内容）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
