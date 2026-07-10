"""Read-only Zotero Local API identity enrichment for zsearch evidence."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, replace
from urllib import error, parse, request

from evidence import ProviderRecord, RetrievalStatus, ZoteroNativeIdentity


@dataclass(frozen=True)
class ZoteroEnrichment:
    records: tuple[ProviderRecord, ...]
    status: RetrievalStatus


class ZoteroLocalAdapter:
    """Batch-resolve zsearch item keys through Zotero's GET-only local API."""

    def __init__(
        self,
        base_url: str = "http://localhost:23119/api",
        timeout: float = 1.0,
        opener=request.urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = opener

    def _status(self, state, hits=None, message=None):
        return {
            "provider": "zotero-local",
            "state": state,
            "query": "itemKey batch",
            "hits": hits,
            "message": message,
        }

    def _get_json(self, url):
        req = request.Request(
            url,
            headers={"Zotero-API-Version": "3"},
            method="GET",
        )
        with self.opener(req, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8") or "[]")

    def _fetch(self, keys):
        query = parse.urlencode(
            {"itemKey": ",".join(keys), "include": "data", "limit": len(keys)}
        )
        return self._get_json(f"{self.base_url}/users/0/items?{query}")

    def _fetch_children(self, item_key):
        query = parse.urlencode({"include": "data"})
        return self._get_json(
            f"{self.base_url}/users/0/items/{parse.quote(item_key)}/children?{query}"
        )

    def _with_descendants(self, payload):
        items = list(payload)
        seen = {item.get("key") for item in items if isinstance(item, dict)}
        queue = [(item, 0) for item in items if isinstance(item, dict)]
        while queue:
            item, depth = queue.pop(0)
            if depth >= 2 or not item.get("key"):
                continue
            children = self._fetch_children(item["key"])
            if not isinstance(children, list):
                raise ValueError("Zotero children must be a list")
            for child in children:
                if not isinstance(child, dict) or not child.get("key"):
                    continue
                if child["key"] in seen:
                    continue
                seen.add(child["key"])
                items.append(child)
                queue.append((child, depth + 1))
        return items

    def enrich(self, records: tuple[ProviderRecord, ...]) -> ZoteroEnrichment:
        keys = list(
            dict.fromkeys(record.source_id for record in records if record.source_id)
        )
        if not keys:
            return ZoteroEnrichment(records, self._status("empty", hits=0))
        try:
            payload = self._fetch(keys)
        except error.HTTPError as exc:
            state = "rate-limited" if exc.code == 429 else "unavailable"
            return ZoteroEnrichment(records, self._status(state, message=str(exc)))
        except (TimeoutError, socket.timeout) as exc:
            return ZoteroEnrichment(records, self._status("timeout", message=str(exc)))
        except error.URLError as exc:
            return ZoteroEnrichment(
                records, self._status("unavailable", message=str(exc))
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return ZoteroEnrichment(
                records, self._status("bad-payload", message=str(exc))
            )

        if not isinstance(payload, list):
            return ZoteroEnrichment(
                records,
                self._status("bad-payload", message="Zotero items must be a list"),
            )
        try:
            payload = self._with_descendants(payload)
        except (error.HTTPError, error.URLError, TimeoutError, socket.timeout) as exc:
            return ZoteroEnrichment(
                records, self._status("degraded", message=f"children: {exc}")
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return ZoteroEnrichment(
                records, self._status("bad-payload", message=str(exc))
            )

        by_key = {
            item.get("key"): item
            for item in payload
            if isinstance(item, dict)
            and item.get("key")
            and isinstance(item.get("data"), dict)
        }
        enriched = tuple(
            self._enrich_record(record, by_key.get(record.source_id))
            for record in records
        )
        original_keys = {record.source_id for record in records}
        descendants = tuple(
            self._enrich_record(self._record_for_item(item), item)
            for item in payload
            if item.get("key") not in original_keys
        )
        enriched += descendants
        matched = sum(
            record.verification_status == "identity-enriched" for record in enriched
        )
        return ZoteroEnrichment(
            enriched,
            self._status("ok" if matched else "empty", hits=matched),
        )

    def _record_for_item(self, item):
        data = item.get("data") or {}
        title = (
            data.get("title")
            or data.get("annotationText")
            or data.get("filename")
            or item.get("key")
            or ""
        )
        return ProviderRecord(
            source_id=item.get("key") or "",
            title=title,
            url="",
            source_kind="library-document",
            relation="context",
            verification_status="unresolved",
        )

    def _enrich_record(self, record: ProviderRecord, item: dict | None):
        if not item:
            return record
        data = item["data"]
        library = item.get("library") or {}
        library_type = library.get("type") or "user"
        library_id = int(library.get("id") or 0)
        item_key = item["key"]
        item_type = data.get("itemType") or "item"
        if library_type == "group":
            locator = f"zotero://select/groups/{library_id}/items/{item_key}"
        else:
            locator = f"zotero://select/library/items/{item_key}"
        native: ZoteroNativeIdentity = {
            "provider": "zotero-local",
            "library_type": library_type,
            "library_id": library_id,
            "library_name": library.get("name") or "",
            "item_key": item_key,
            "item_version": int(item.get("version") or data.get("version") or 0),
            "item_type": item_type,
            "parent_item": data.get("parentItem") or None,
            "attachment_key": item_key if item_type == "attachment" else None,
            "annotation_key": item_key if item_type == "annotation" else None,
            "collections": list(data.get("collections") or []),
            "tags": [
                tag.get("tag", "")
                for tag in data.get("tags") or []
                if isinstance(tag, dict)
            ],
        }
        return replace(
            record,
            version=str(native["item_version"]),
            verification_status="identity-enriched",
            identity=f"zotero:{library_type}:{library_id}:{item_key}",
            locator_kind="zotero-select",
            locator_value=locator,
            native=native,
        )
