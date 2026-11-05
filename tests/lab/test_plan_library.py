"""The plan library behind compose's gallery (design 2026-11-05): its index, filters, thumbnails and plans, and the
server's endpoints, on hand-made fixture plans (tests/lab/plan_library_fixture.py), never dataset files."""
import json
import struct

import pytest

from tests.lab.plan_library_fixture import NO_FLOOR, ONE_FLOOR, TWO_FLOORS, write_library
from vesper.lab import plan_library as pl
from vesper.validation.plans import parse_cubicasa_svg


@pytest.fixture
def lib(tmp_path):
    write_library(tmp_path / "datasets")
    return pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache", thumbnailer=lambda src, dest: False)


def test_the_library_is_the_configured_folder_or_its_cubicasa5k_folder(tmp_path):
    folder = write_library(tmp_path / "datasets")
    assert pl.find_library(tmp_path / "datasets") == folder
    assert pl.find_library(folder) == folder
    assert pl.find_library(tmp_path / "nowhere") is None and pl.find_library(None) is None
    assert pl.plan_ids(folder) == ["colorful/7", "hq/1", "hq/2"]  # groups by name, plans by number; no test.txt


def test_the_index_records_each_plan_s_rooms_floors_area_and_whether_it_reads(lib):
    assert lib.wait() == "ready"
    rows = {r["id"]: r for r in lib._rows}
    one, two, bad = rows["hq/1"], rows["hq/2"], rows["colorful/7"]
    assert one == {"id": "hq/1", "group": "hq", "size": [1200, 800], "rooms": 3, "spaces": 6, "floors": 1,
                   "area_m2": pytest.approx(58.75, abs=0.05), "scale": "labels", "ok": True}
    # two floors: counted on the floor the home is built from, the one with the front door
    assert (two["rooms"], two["spaces"], two["floors"], two["scale"]) == (2, 3, 2, "assumed")
    assert two["area_m2"] == pytest.approx(38.25, abs=0.05)
    assert bad["ok"] is False and "no floor plan" in bad["error"]


def test_the_index_is_cached_by_the_library_s_path_and_plan_count_and_rebuilt_when_it_changes(tmp_path, monkeypatch):
    write_library(tmp_path / "datasets")
    first = pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache")
    assert first.wait() == "ready" and first.seconds is not None
    assert (first.place / "index.json").is_file() and str(tmp_path / "cache") in str(first.place)

    def never(root, pid):
        raise AssertionError("a current index is read from the cache, not built again")
    monkeypatch.setattr(pl, "index_row", never)
    again = pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache")
    assert again.wait() == "ready" and again.seconds is None and len(again._rows) == 3
    monkeypatch.undo()
    write_library(tmp_path / "datasets", {"hq/3": ONE_FLOOR})  # a plan more: indexed again
    grown = pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache")
    assert grown.wait() == "ready" and grown.seconds is not None and len(grown._rows) == 4


def test_the_listing_filters_by_rooms_floors_and_id_and_never_lists_an_unreadable_plan(lib):
    lib.wait()
    every = lib.listing()
    assert every["state"] == "ready" and every["progress"] == {"done": 3, "total": 3}
    assert [i["id"] for i in every["items"]] == ["hq/1", "hq/2"] and every["unreadable"] == 1
    assert every["licence"] == "CC BY-NC-SA 4.0"
    three_four = lib.listing(rooms="3-4", floors="1")
    assert [i["id"] for i in three_four["items"]] == ["hq/1"] and three_four["total"] == 1
    assert [i["id"] for i in lib.listing(rooms="1-2")["items"]] == ["hq/2"]
    assert lib.listing(rooms="1-2", floors="1")["items"] == []  # hq/2 draws two floors
    assert [i["id"] for i in lib.listing(q="HQ/2")["items"]] == ["hq/2"]
    assert [i["id"] for i in lib.listing(offset=1, limit=1)["items"]] == ["hq/2"]
    for bad in ({"rooms": "3"}, {"rooms": "../x"}, {"floors": "2"}):
        with pytest.raises(pl.LibraryError):
            lib.listing(**bad)


def test_the_filters_match_rooms_as_a_listing_counts_them():
    row = {"id": "hq/9", "ok": True, "rooms": 7, "floors": 1}
    assert pl.matches(row, pl.filters("7-")) and not pl.matches(row, pl.filters("5-6"))
    assert pl.matches(row, pl.filters("", "1", "9")) and not pl.matches(row, pl.filters(q="hq 8"))
    assert not pl.matches({**row, "ok": False}, pl.filters())


def test_a_plan_is_reached_only_by_an_id_the_index_holds(lib):
    lib.wait()
    assert lib.row("hq/1")["ok"]
    for pid in ("hq/99", "../hq/1", "hq/1/../2", "/etc/passwd", "hq/1/model.svg", "", None, "hq\\1"):
        assert lib.row(pid) is None and lib.thumb(pid) is None and lib.image(pid) is None and lib.plan(pid) is None
    assert lib.plan("colorful/7") is None  # listed in the index, but its ground truth does not read


def test_a_library_that_is_not_there_says_so(tmp_path):
    for folder in (None, tmp_path / "unplugged-drive"):
        gone = pl.PlanLibrary(folder, cache=tmp_path / "cache")
        got = gone.listing()
        assert got["state"] == "absent" and got["items"] == [] and got["folder"] == (str(folder) if folder else None)


def test_thumbnails_are_made_once_and_kept_and_without_pillow_the_image_itself_is_served(tmp_path):
    write_library(tmp_path / "datasets")
    made = []

    def thumbnailer(src, dest):
        made.append(src)
        dest.write_bytes(b"\xff\xd8 a jpeg")
        return True
    lib = pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache", thumbnailer=thumbnailer)
    lib.wait()
    assert lib.thumb("hq/1") == (b"\xff\xd8 a jpeg", "image/jpeg")
    assert lib.thumb("hq/1") == (b"\xff\xd8 a jpeg", "image/jpeg") and len(made) == 1
    plain = pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache2", thumbnailer=lambda src, dest: False)
    plain.wait()
    data, kind = plain.thumb("hq/2")
    assert kind == "image/png" and data == (tmp_path / "datasets/cubicasa5k/hq/2/F1_scaled.png").read_bytes()


def test_with_pillow_a_thumbnail_is_a_jpeg_320_px_wide(tmp_path):
    pytest.importorskip("PIL")
    write_library(tmp_path / "datasets")
    lib = pl.PlanLibrary(tmp_path / "datasets", cache=tmp_path / "cache")
    lib.wait()
    data, kind = lib.thumb("hq/1")
    assert kind == "image/jpeg" and data[:2] == b"\xff\xd8"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(data)).size == (320, 213)


def test_without_pillow_the_thumbnailer_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "_pillow", lambda: None)
    assert pl.pillow_thumbnail(tmp_path / "a.png", tmp_path / "a.jpg") is False


def test_a_two_floor_plan_is_built_from_the_floor_with_its_entrance():
    truth = parse_cubicasa_svg(TWO_FLOORS)
    plan, floor = pl.floor_plan(truth)
    assert floor == 0 and [r["type"] for r in plan["rooms"]] == ["hall", "living", "bedroom"]
    assert len(plan["doors"]) == 3 and "outside" in plan["doors"][plan["entrance"]]["rooms"]
    # the entrance upstairs: that floor is built, its doors kept, the entrance renumbered
    truth["room_floors"] = [1, 1, 1, 0, 0]
    up, floor = pl.floor_plan(truth)
    assert floor == 1 and [r["id"] for r in up["rooms"]] == ["r1", "r2", "r3"]
    one = parse_cubicasa_svg(ONE_FLOOR)
    assert pl.floor_plan(one) == (one["plan"], 0)


def test_a_scale_from_the_dimension_labels_is_measured_and_the_assumed_one_is_not():
    from vesper.lab.plan import measured_scale
    labelled = pl.library_plan(parse_cubicasa_svg(ONE_FLOOR))
    assert labelled["scale"] == {"length_m": pytest.approx(10.0), "from": [0, 0], "to": [1000, 0]}
    assert measured_scale(labelled)
    assumed = pl.library_plan(parse_cubicasa_svg(TWO_FLOORS))
    assert assumed["scale"] == {"m_per_px": pytest.approx(0.01)} and not measured_scale(assumed)


def test_the_plan_for_the_preview_says_its_floors_and_scale(lib):
    lib.wait()
    got = lib.plan("hq/2")
    assert got["name"] == "cubicasa5k-hq-2" and got["floors"] == 2 and got["floor"] == 0
    assert got["rooms_drawn"] == 5 and len(got["plan"]["rooms"]) == 3
    assert got["scale_source"] == "assumed 1 cm a unit" and got["size"] == [1200, 800]


def test_png_size_reads_the_header_only(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 640, 480))
    assert pl.png_size(p) == [640, 480]
    p.write_bytes(b"GIF89a" + b"\0" * 30)
    assert pl.png_size(p) is None


# -- the server ------------------------------------------------------------------------------------------------------
class NoClaude:
    """The image reader: never called when a plan comes from the library."""

    def __call__(self, *a):
        raise AssertionError("Claude was asked to read a library plan")


@pytest.fixture
def server(tmp_path, monkeypatch):
    from tests.lab.homes import catalog
    from vesper.lab.compose import ComposeServer
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {"validated_products": [],
                                                                                "matter": {"validated_products": []}}})
    write_library(tmp_path / "datasets")
    s = ComposeServer(catalog(), tmp_path / "vds", tmp_path / "results", tmp_path / "out", port=0,
                      stack_root=tmp_path / "stack", reader=NoClaude(), plan_library=tmp_path / "datasets",
                      library_cache=tmp_path / "cache")
    s.start()
    s.library().wait()
    yield s
    s.stop()


def get(s, path):
    from tests.lab.test_compose import call
    status, body = call(s, path)
    return status, body


def test_the_gallery_lists_and_filters_the_library_over_http(server):
    status, body = get(server, "/api/plan-library?rooms=3-4&floors=1")
    got = json.loads(body)
    assert status == 200 and got["state"] == "ready" and [i["id"] for i in got["items"]] == ["hq/1"]
    assert json.loads(get(server, "/api/plan-library?limit=1&offset=1")[1])["items"][0]["id"] == "hq/2"
    status, body = get(server, "/api/plan-library?rooms=lots")
    assert status == 400 and "rooms" in json.loads(body)["error"]
    assert get(server, "/api/plan-library?offset=x")[0] == 400


def test_thumbnails_images_and_plans_are_served_by_id_only(server):
    status, body = get(server, "/api/plan-library/thumb/hq/1")
    assert status == 200 and (body[:4] == b"\x89PNG" or body[:2] == b"\xff\xd8")  # the image, or a JPEG thumbnail
    assert get(server, "/api/plan-library/image/hq/1")[1][:4] == b"\x89PNG"
    for bad in ("/api/plan-library/thumb/hq/99", "/api/plan-library/thumb/../../etc/passwd",
                "/api/plan-library/thumb/hq/1/../2", "/api/plan-library/plan/colorful/7",
                "/api/plan-library/image/%2e%2e/hq/1", "/api/plan-library/plan/hq"):
        assert get(server, bad)[0] == 404, bad


def test_the_preview_s_plan_is_the_ground_truth_checked_as_vesper_would_build_it(server):
    status, body = get(server, "/api/plan-library/plan/hq/1")
    got = json.loads(body)
    assert status == 200 and got["id"] == "hq/1" and got["floors"] == 1
    assert [r["type"] for r in got["plan"]["rooms"]] == ["hall", "living", "kitchen", "bedroom", "bathroom", "bedroom"]
    assert got["problems"] == [] and got["scale_source"] == "labels" and got["m_per_px"] == pytest.approx(0.01)
    assert "storey" not in got["summary"]
    two = json.loads(get(server, "/api/plan-library/plan/hq/2")[1])
    assert two["problems"] == [] and "1 of 2 drawn" in two["summary"]


def test_use_this_plan_makes_the_import_that_plan_with_no_claude_and_generate_builds_it(server, tmp_path, capsys):
    from tests.lab.test_compose import call
    origin = server.url.rstrip("/")
    status, body = call(server, "/api/plan-library/use", {"id": "hq/1"}, origin=origin)
    job = json.loads(body)
    assert status == 200 and job["state"] == "done" and job["kind"] == "library" and job["problems"] == []
    assert job["name"] == "cubicasa5k-hq-1" and job["library"] == {"id": "hq/1", "scale_source": "labels",
                                                                   "m_per_px": pytest.approx(0.01), "floors": 1}
    assert len(job["plan"]["rooms"]) == 6 and "image" not in job
    assert json.loads(call(server, "/api/import")[1])["job"] == job["job"]  # what a reopened page follows
    status, image = call(server, f"/api/import/{job['job']}/image")
    assert status == 200 and image == (tmp_path / "datasets/cubicasa5k/hq/1/F1_scaled.png").read_bytes()
    req = {"format": "vesper-request/1", "seed": 1, "devices": [{"category": "light", "per": "room"}],
           "plan": job["plan"], "scale": "", "source": job["name"]}
    status, body = call(server, "/api/generate", req, origin=origin)
    assert status == 200, body
    assert json.loads(body)["summary"]["rooms"] >= 6
    from tests.lab.test_cli_lab import setup
    from vesper.cli import main
    home = tmp_path / "out" / "home.yaml"
    assert home.read_text().startswith("# Imported from cubicasa5k-hq-1")
    cat, _ = setup(tmp_path / "cli")
    capsys.readouterr()
    assert main(["lab", "layout", str(home), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out
    for bad in ({"id": "hq/99"}, {"id": "../../etc"}, {"id": "colorful/7"}, {}, {"id": 3}):
        status, body = call(server, "/api/plan-library/use", bad, origin=origin)
        assert status == 404, (bad, body)
    assert call(server, "/api/plan-library/use", {"id": "hq/1"}, origin="https://evil.example")[0] == 403


def test_a_two_floor_plan_used_says_its_storey_and_is_built_from_the_entrance_floor(server):
    from tests.lab.test_compose import call
    job = json.loads(call(server, "/api/plan-library/use", {"id": "hq/2"})[1])
    assert job["floors_drawn"] == 2 and [r["type"] for r in job["plan"]["rooms"]] == ["hall", "living", "bedroom"]
    assert "the floor with the main entrance (1 of 2 drawn)" in job["summary"]


def test_without_a_library_the_gallery_says_where_it_looked(tmp_path, monkeypatch):
    from tests.lab.homes import catalog
    from tests.lab.test_compose import call
    from vesper.lab.compose import ComposeServer
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))  # no plan_library configured
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {}})
    s = ComposeServer(catalog(), tmp_path / "vds", tmp_path / "results", tmp_path / "out", port=0,
                      stack_root=tmp_path / "stack", library_cache=tmp_path / "cache")
    s.start()
    try:
        got = json.loads(call(s, "/api/plan-library")[1])
        assert got["state"] == "absent" and got["folder"] is None
        assert call(s, "/api/plan-library/thumb/hq/1")[0] == 404
        write_library(tmp_path / "datasets")  # set up meanwhile: found on the next request
        from vesper.setup_tools import save_config
        save_config(plan_library=tmp_path / "datasets")
        assert json.loads(call(s, "/api/plan-library")[1])["state"] in ("indexing", "ready")
    finally:
        s.stop()


def test_the_unreadable_fixture_really_has_no_floor():
    with pytest.raises(ValueError):
        parse_cubicasa_svg(NO_FLOOR)
