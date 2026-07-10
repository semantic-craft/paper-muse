import json
from copy import deepcopy
from urllib import error
from urllib.parse import parse_qs, urlparse

from evidence import ProviderRecord
from zotero_local import ZoteroLocalAdapter


class FixtureResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_local_api_enriches_item_attachment_annotation_and_metadata():
    requests = []
    item = {
        "key": "ITEM0001",
        "version": 12,
        "library": {"type": "user", "id": 0, "name": "My Library"},
        "data": {
            "key": "ITEM0001",
            "itemType": "journalArticle",
            "title": "Platform Governance",
            "collections": ["COLL0001"],
            "tags": [{"tag": "platform"}, {"tag": "governance"}],
        },
    }
    attachment = {
        "key": "ATTACH01",
        "version": 7,
        "library": {"type": "user", "id": 0, "name": "My Library"},
        "data": {
            "key": "ATTACH01",
            "itemType": "attachment",
            "title": "Platform Governance.pdf",
            "parentItem": "ITEM0001",
            "collections": [],
            "tags": [],
        },
    }
    annotation = {
        "key": "ANNOT001",
        "version": 3,
        "library": {"type": "user", "id": 0, "name": "My Library"},
        "data": {
            "key": "ANNOT001",
            "itemType": "annotation",
            "parentItem": "ATTACH01",
            "annotationText": "A stable annotation",
            "collections": [],
            "tags": [{"tag": "important"}],
        },
    }

    def open_fixture(request, timeout):
        requests.append((request, timeout))
        path = urlparse(request.full_url).path
        if path.endswith("/users/0/items"):
            return FixtureResponse([item])
        if path.endswith("/items/ITEM0001/children"):
            return FixtureResponse([attachment])
        if path.endswith("/items/ATTACH01/children"):
            return FixtureResponse([annotation])
        raise AssertionError(request.full_url)

    records = (
        ProviderRecord(
            source_id="ITEM0001",
            title="Platform Governance",
            url="file:///library/ITEM0001",
            source_kind="library-document",
            relation="context",
            verification_status="unresolved",
        ),
    )

    result = ZoteroLocalAdapter(opener=open_fixture, timeout=0.25).enrich(records)

    assert all(req.get_method() == "GET" for req, _timeout in requests)
    assert all(timeout == 0.25 for _req, timeout in requests)
    assert all(req.get_header("Zotero-api-version") == "3" for req, _ in requests)
    query = parse_qs(urlparse(requests[0][0].full_url).query)
    assert query["itemKey"] == ["ITEM0001"]

    assert result.status["state"] == "ok"
    assert result.status["hits"] == 3
    item, attachment, annotation = result.records
    assert item.identity == "zotero:user:0:ITEM0001"
    assert item.locator_value == "zotero://select/library/items/ITEM0001"
    assert item.version == "12"
    assert item.verification_status == "identity-enriched"
    assert item.native == {
        "provider": "zotero-local",
        "library_type": "user",
        "library_id": 0,
        "library_name": "My Library",
        "item_key": "ITEM0001",
        "item_version": 12,
        "item_type": "journalArticle",
        "parent_item": None,
        "attachment_key": None,
        "annotation_key": None,
        "collections": ["COLL0001"],
        "tags": ["platform", "governance"],
    }
    assert attachment.native["attachment_key"] == "ATTACH01"
    assert attachment.native["parent_item"] == "ITEM0001"
    assert annotation.native["annotation_key"] == "ANNOT001"
    assert annotation.native["parent_item"] == "ATTACH01"


def test_zsearch_enrichment_uses_stable_zotero_identity_without_changing_count(
    monkeypatch, tmp_path
):
    import blindspot

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))

    class ZsearchResult:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            [
                {
                    "key": "ITEM0001",
                    "title": "Platform Governance",
                    "url": "file:///library/ITEM0001.pdf",
                }
            ]
        )

    monkeypatch.setattr(
        blindspot.subprocess, "run", lambda *args, **kwargs: ZsearchResult
    )
    api_calls = []

    def open_fixture(request, timeout):
        api_calls.append(timeout)
        if "/children" in request.full_url:
            return FixtureResponse([])
        return FixtureResponse(
            [
                {
                    "key": "ITEM0001",
                    "version": 12,
                    "library": {"type": "user", "id": 0, "name": "My Library"},
                    "data": {
                        "key": "ITEM0001",
                        "itemType": "journalArticle",
                        "title": "Platform Governance",
                        "collections": ["COLL0001"],
                        "tags": [{"tag": "platform"}],
                    },
                }
            ]
        )

    adapter = ZoteroLocalAdapter(opener=open_fixture)

    search = blindspot.real_own_search(limit=3, zotero_adapter=adapter)
    fast = search("platform governance")

    assert fast["hits"] == 1
    assert fast["status"]["state"] == "ok"
    assert api_calls == []

    result = search.enrich(fast)
    repeated = search.enrich(search("platform governance"))

    assert result["identity_status"]["state"] == "ok"
    assert repeated["hits"] == 1
    assert api_calls == [1.0, 1.0]  # batch item + children；第二次 enrich 命中内存缓存
    ref = result["evidence"][0]
    assert ref["source"]["identity"] == "zotero:user:0:ITEM0001"
    assert ref["source"]["native"]["collections"] == ["COLL0001"]
    assert ref["locator"] == {
        "kind": "zotero-select",
        "value": "zotero://select/library/items/ITEM0001",
        "exact": "Platform Governance",
    }
    assert ref["verification"]["status"] == "identity-enriched"


def test_zotero_unavailable_keeps_zsearch_hits_and_marks_identity_degraded(
    monkeypatch, tmp_path
):
    import blindspot

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))

    class ZsearchResult:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            [{"key": "ITEM0001", "title": "Owned", "url": "file:///owned.pdf"}]
        )

    def unavailable(_request, timeout):
        raise error.URLError("Zotero is not running")

    monkeypatch.setattr(
        blindspot.subprocess, "run", lambda *args, **kwargs: ZsearchResult
    )
    search = blindspot.real_own_search(
        limit=3, zotero_adapter=ZoteroLocalAdapter(opener=unavailable)
    )
    fast = search("owned")
    result = search.enrich(fast)

    assert fast["hits"] == 1
    assert result["hits"] == 1
    assert result["status"]["state"] == "ok"
    assert result["identity_status"]["state"] == "unavailable"
    assert result["evidence"][0]["verification"]["status"] == "unresolved"


def test_unmatched_zsearch_document_stays_unresolved_context():
    record = ProviderRecord(
        source_id="UNKNOWN1",
        title="Unmatched document",
        url="file:///unmatched.pdf",
        source_kind="library-document",
        relation="context",
        verification_status="unresolved",
    )
    adapter = ZoteroLocalAdapter(opener=lambda _request, timeout: FixtureResponse([]))

    result = adapter.enrich((record,))

    assert result.status["state"] == "empty"
    assert result.records == (record,)
    assert result.records[0].relation == "context"
    assert result.records[0].verification_status == "unresolved"


def test_scan_publishes_own_hits_before_zotero_identity_enrichment(
    monkeypatch, tmp_path
):
    import blindspot

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))

    class ZsearchResult:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            [{"key": "ITEM0001", "title": "Owned", "url": "file:///owned.pdf"}]
        )

    monkeypatch.setattr(
        blindspot.subprocess, "run", lambda *args, **kwargs: ZsearchResult
    )
    updates = []

    def open_fixture(request, timeout):
        assert any(
            update.get("own_hits") == 1 and update.get("own_identity_status") is None
            for update in updates
        ), "own_hits must be published before Zotero Local API starts"
        if "/children" in request.full_url:
            return FixtureResponse([])
        return FixtureResponse(
            [
                {
                    "key": "ITEM0001",
                    "version": 1,
                    "library": {"type": "user", "id": 0, "name": "My Library"},
                    "data": {
                        "key": "ITEM0001",
                        "itemType": "journalArticle",
                        "title": "Owned",
                        "collections": [],
                        "tags": [],
                    },
                }
            ]
        )

    own_search = blindspot.real_own_search(
        limit=3, zotero_adapter=ZoteroLocalAdapter(opener=open_fixture)
    )
    card_reply = json.dumps(
        {
            "cards": [
                {
                    "type": "理论框架",
                    "name": "制度空白",
                    "mechanism": "m",
                    "why_nonobvious": "w",
                    "steelman": "s",
                    "questions": ["q"],
                }
            ]
        }
    )

    cards = blindspot.run_scan(
        topic="平台责任",
        profile="",
        output_dir=str(tmp_path),
        providers={"deepseek": lambda _prompt: card_reply},
        decompose_llm=lambda _prompt: json.dumps({"fundamentals": ["f"]}),
        en_search=lambda _query: [],
        zh_search=lambda _query: {
            "hits": 0,
            "evidence": [],
            "status": {"provider": "cnki", "state": "empty", "hits": 0},
        },
        own_search=own_search,
        on_card=lambda _card: None,
        on_update=lambda card: updates.append(deepcopy(card)) if card else None,
    )

    assert cards[0]["own_hits"] == 1
    assert cards[0]["own_identity_status"]["state"] == "ok"


def test_identity_enrichment_does_not_truncate_children_at_zsearch_limit(
    monkeypatch, tmp_path
):
    import blindspot

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))

    class ZsearchResult:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            [
                {"key": "PARENT01", "title": "Parent 1", "url": "file:///p1"},
                {"key": "PARENT02", "title": "Parent 2", "url": "file:///p2"},
            ]
        )

    parents = [
        {
            "key": key,
            "version": 1,
            "library": {"type": "user", "id": 0, "name": "My Library"},
            "data": {
                "key": key,
                "itemType": "journalArticle",
                "title": title,
                "collections": [],
                "tags": [],
            },
        }
        for key, title in (("PARENT01", "Parent 1"), ("PARENT02", "Parent 2"))
    ]
    children = {
        "PARENT01": [
            {
                "key": "ATTACH01",
                "version": 1,
                "library": {"type": "user", "id": 0, "name": "My Library"},
                "data": {
                    "key": "ATTACH01",
                    "itemType": "attachment",
                    "title": "Attachment 1",
                    "parentItem": "PARENT01",
                    "collections": [],
                    "tags": [],
                },
            }
        ],
        "PARENT02": [
            {
                "key": "ATTACH02",
                "version": 1,
                "library": {"type": "user", "id": 0, "name": "My Library"},
                "data": {
                    "key": "ATTACH02",
                    "itemType": "attachment",
                    "title": "Attachment 2",
                    "parentItem": "PARENT02",
                    "collections": [],
                    "tags": [],
                },
            }
        ],
    }

    def open_fixture(request, timeout):
        path = urlparse(request.full_url).path
        if path.endswith("/users/0/items"):
            return FixtureResponse(parents)
        key = path.split("/")[-2]
        return FixtureResponse(children.get(key, []))

    monkeypatch.setattr(
        blindspot.subprocess, "run", lambda *args, **kwargs: ZsearchResult
    )
    search = blindspot.real_own_search(
        limit=2, zotero_adapter=ZoteroLocalAdapter(opener=open_fixture)
    )

    result = search.enrich(search("platform"))

    assert result["hits"] == 2
    assert {ref["retrieval"]["source_id"] for ref in result["evidence"]} == {
        "PARENT01",
        "PARENT02",
        "ATTACH01",
        "ATTACH02",
    }
