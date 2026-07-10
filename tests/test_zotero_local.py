import json
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
    payload = [
        {
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
        },
        {
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
        },
        {
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
        },
    ]

    def open_fixture(request, timeout):
        requests.append((request, timeout))
        return FixtureResponse(payload)

    records = tuple(
        ProviderRecord(
            source_id=key,
            title=title,
            url=f"file:///library/{key}",
            source_kind="library-document",
            relation="context",
            verification_status="unresolved",
        )
        for key, title in (
            ("ITEM0001", "Platform Governance"),
            ("ATTACH01", "Platform Governance.pdf"),
            ("ANNOT001", "A stable annotation"),
        )
    )

    result = ZoteroLocalAdapter(opener=open_fixture, timeout=0.25).enrich(records)

    request, timeout = requests[0]
    assert request.get_method() == "GET"
    assert timeout == 0.25
    assert request.get_header("Zotero-api-version") == "3"
    query = parse_qs(urlparse(request.full_url).query)
    assert query["itemKey"] == ["ITEM0001,ATTACH01,ANNOT001"]

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

    def open_fixture(_request, timeout):
        api_calls.append(timeout)
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
    result = search("platform governance")
    repeated = search("platform governance")

    assert result["hits"] == 1
    assert result["status"]["state"] == "ok"
    assert result["identity_status"]["state"] == "ok"
    assert repeated["hits"] == 1
    assert api_calls == [1.0]
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
    result = blindspot.real_own_search(
        limit=3, zotero_adapter=ZoteroLocalAdapter(opener=unavailable)
    )("owned")

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
