import httpx
import pytest

from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.dcl import fetch_all, sync_dcl
from vesper.catalog.snapshot import load_snapshot


def ledger(models=DCL_MODELS, vendors=DCL_VENDORS, *, total=None, endless=False):
    seen = []

    def handler(request):
        seen.append(request.url)
        p, q = request.url.path, request.url.params
        if p.endswith("/blocks/latest"):
            return httpx.Response(200, json={"block": {"header": {"height": "324957"}}})
        rows, key = (models, "model") if p == "/dcl/model/models" else (vendors, "vendorInfo")
        start = int(q.get("pagination.key", "0=").rstrip("=")) if "pagination.key" in q else 0
        size = int(q["pagination.limit"])
        nxt = None if (start + size >= len(rows) and not endless) else f"{start + size}="  # '=' tests URL encoding
        body = {key: rows[start:start + size], "pagination": {"next_key": nxt}}
        if "pagination.count_total" in q:
            body["pagination"]["total"] = str(total if total is not None else len(rows))
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_all_follows_next_key_and_checks_the_total():
    client, seen = ledger()
    with client:
        assert fetch_all(client, "/dcl/model/models", "model", page_size=4) == DCL_MODELS
    assert [u.params.get("pagination.key") for u in seen] == [None, "4="]


def test_a_short_read_is_refused():
    client, _ = ledger(total=7)
    with client, pytest.raises(IngestError, match="received 6 records but the ledger reports 7"):
        fetch_all(client, "/dcl/model/models", "model", page_size=4)


def test_endless_pagination_is_refused():
    client, _ = ledger(endless=True)
    with client, pytest.raises(IngestError, match="did not end"):
        fetch_all(client, "/dcl/model/models", "model", page_size=1, max_pages=3)


def test_sync_versions_the_snapshot_by_chain_height(tmp_path):
    client, _ = ledger()
    with client:
        sync_dcl(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "dcl")
    assert meta["version"] == "height-324957" and meta["models"] == 6 and meta["vendors"] == 2
    assert meta["license"] == "DCL-terms-unverified" and set(files) == {"models.json", "vendors.json"}
