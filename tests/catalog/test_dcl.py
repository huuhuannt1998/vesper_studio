import httpx
import pytest

from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.dcl import fetch_all, sync_dcl
from vesper.catalog.snapshot import load_snapshot


def ledger(models=DCL_MODELS, vendors=DCL_VENDORS, *, total=None, endless=False, omit_total=False, raw_block=None):
    seen = []

    def handler(request):
        seen.append(request)
        p, q = request.url.path, request.url.params
        if p.endswith("/blocks/latest"):
            if raw_block is not None:
                return httpx.Response(200, text=raw_block)
            return httpx.Response(200, json={"block": {"header": {"height": "324957"}}})
        rows, key = (models, "model") if p == "/dcl/model/models" else (vendors, "vendorInfo")
        start = int(q.get("pagination.key", "0=").rstrip("=")) if "pagination.key" in q else 0
        size = int(q["pagination.limit"])
        nxt = None if (start + size >= len(rows) and not endless) else f"{start + size}="  # '=' tests URL encoding
        body = {key: rows[start:start + size], "pagination": {"next_key": nxt}}
        if "pagination.count_total" in q and not omit_total:
            body["pagination"]["total"] = str(total if total is not None else len(rows))
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_all_follows_next_key_and_checks_the_total():
    client, seen = ledger()
    with client:
        assert fetch_all(client, "/dcl/model/models", "model", page_size=4) == DCL_MODELS
    assert [r.url.params.get("pagination.key") for r in seen] == [None, "4="]


def test_a_short_read_is_refused():
    client, _ = ledger(total=8)
    with client, pytest.raises(IngestError, match="received 7 records but the ledger reports 8"):
        fetch_all(client, "/dcl/model/models", "model", page_size=4)


def test_endless_pagination_is_refused():
    client, _ = ledger(endless=True)
    with client, pytest.raises(IngestError, match="did not end"):
        fetch_all(client, "/dcl/model/models", "model", page_size=1, max_pages=3)


def test_sync_versions_the_snapshot_by_chain_height(tmp_path):
    client, _ = ledger()
    with client:
        sync_dcl(tmp_path, client, height="latest")
    meta, files = load_snapshot(tmp_path, "dcl")
    assert meta["version"] == "height-324957" and meta["models"] == 7 and meta["vendors"] == 2
    assert meta["license"] == "DCL-terms-unverified" and set(files) == {"models.json", "vendors.json"}


def test_a_ledger_that_omits_the_total_is_refused():
    client, _ = ledger(omit_total=True)
    with client, pytest.raises(IngestError, match="did not report a total"):
        fetch_all(client, "/dcl/model/models", "model", page_size=4)


def test_an_empty_vendor_list_is_refused(tmp_path):
    client, _ = ledger(vendors=[])
    with client, pytest.raises(IngestError, match="no vendors"):
        sync_dcl(tmp_path, client)
    assert not (tmp_path / "dcl").exists()


@pytest.mark.parametrize("body", ["<html>maintenance</html>", "{}"])
def test_a_malformed_reply_is_an_ingest_error(tmp_path, body):
    client, _ = ledger(raw_block=body)
    with client, pytest.raises(IngestError, match="blocks/latest"):
        sync_dcl(tmp_path, client, height="latest")


def test_sync_reads_the_ledger_at_the_pinned_height_so_a_fresh_sync_gives_the_same_products(tmp_path):
    from vesper.catalog.sources import SOURCES
    assert SOURCES["dcl"].version == "height-324961"
    client, seen = ledger()
    with client:
        sync_dcl(tmp_path, client)
    meta, _ = load_snapshot(tmp_path, "dcl")
    assert meta["version"] == "height-324961" and meta["block_height"] == 324961
    assert not any(r.url.path.endswith("/blocks/latest") for r in seen)
    assert {r.headers.get("x-cosmos-block-height") for r in seen} == {"324961"}


def test_sync_at_the_latest_height_asks_the_ledger_for_it_and_reads_there(tmp_path):
    client, seen = ledger()
    with client:
        sync_dcl(tmp_path, client, height="latest")
    data = [r for r in seen if not r.url.path.endswith("/blocks/latest")]
    assert {r.headers.get("x-cosmos-block-height") for r in data} == {"324957"}
