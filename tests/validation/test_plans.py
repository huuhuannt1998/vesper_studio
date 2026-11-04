"""The floor-plan import's evaluation on CubiCasa5K (task A5): the ground truth read from a model.svg, the metrics on a
hand-made ground truth, the sample, the saved readings. No network and no real Claude: the reader is a fake runner."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from vesper.lab.plan import load_plan
from vesper.validation import plans

# A model.svg in CubiCasa5K's shape: 1 unit = 1 cm, a floor transform, two floors drawn side by side in the one image
# (both are read), spaces with their class, a dimension label, doors and windows as rectangles in the walls.
SVG = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="900" height="700">
<g id="Model" class="Model v1-1"><g class="Floor">
 <g id="Floor-1" transform="matrix(1,0,0,1,100,0)" class="Floorplan Floor-1">
  <g id="a" class="Space LivingRoom"><polygon points="0,0 500,0 500,400 0,400 0,0 "/>
   <g class="SpaceDimensionsLabel"><g class="TextLabel DimensionMeasureLabel">
    <text>16'5" x 13'1"</text><text>5.00 m x 4.00 m</text></g></g></g>
  <g id="b" class="Space Entry Lobby"><polygon points="500,0 700,0 700,400 500,400"/></g>
  <g id="c" class="Space Closet WalkIn"><polygon points="500,400 700,400 700,500 500,500"/></g>
  <g id="d" class="Space Outdoor"><polygon points="0,400 500,400 500,600 0,600"/></g>
  <g class="Door Swing Beside"><polygon points="495,150 505,150 505,240 495,240"/><g class="Threshold"/></g>
  <g class="Door Swing Beside"><polygon points="695,100 705,100 705,190 695,190"/></g>
  <g class="Door Swing Beside"><polygon points="560,395 640,395 640,405 560,405"/></g>
  <g class="Door Swing Beside"><polygon points="200,395 290,395 290,405 200,405"/></g>
  <g class="Window Regular"><polygon points="-5,100 5,100 5,300 -5,300"/></g>
  <g class="Window Regular"><polygon points="340,395 460,395 460,405 340,405"/></g>
  <g class="Wall"><polygon points="0,0 1,0 1,1"/></g>
 </g>
 <g id="Floor-2" class="Floorplan Floor-2">
  <g class="Space Bedroom"><polygon points="1000,0 1300,0 1300,300 1000,300"/></g></g>
</g></g></svg>"""


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_the_ground_truth_is_every_floors_spaces_in_image_pixels_with_their_mapped_types():
    gt = plans.parse_cubicasa_svg(SVG)
    plan = gt["plan"]
    load_plan(plan)  # a valid vesper-plan/1
    assert plan["units"] == "px"
    assert [(r["name"], r["type"]) for r in plan["rooms"]] == [
        ("LivingRoom", "living"), ("Entry Lobby", "hall"), ("Closet WalkIn", "other"), ("Outdoor", "balcony"),
        ("Bedroom", "bedroom")]
    assert plan["rooms"][0]["polygon"] == rect(100, 0, 600, 400)  # the floor's transform applied, the repeat dropped
    assert gt["classes"] == ["LivingRoom", "Entry", "Closet", "Outdoor", "Bedroom"]
    assert plan["rooms"][4]["polygon"] == rect(1000, 0, 1300, 300) and gt["floors"] == 2


def test_the_scale_comes_from_the_dimension_labels_else_a_centimetre_a_unit():
    gt = plans.parse_cubicasa_svg(SVG)
    assert gt["m_per_px"] == pytest.approx(0.01) and gt["scale_source"] == "labels"
    assert gt["plan"]["scale"] == {"m_per_px": pytest.approx(0.01)}
    bare = SVG.replace("5.00 m x 4.00 m", "")
    assert plans.parse_cubicasa_svg(bare)["scale_source"] == "assumed 1 cm a unit"
    wide = SVG.replace("5.00 m x 4.00 m", "10.00 m x 8.00 m")  # the drawing at half size
    assert plans.parse_cubicasa_svg(wide)["m_per_px"] == pytest.approx(0.02)


def test_doors_join_the_rooms_either_side_of_them_and_the_entrance_is_the_halls_door_out():
    gt = plans.parse_cubicasa_svg(SVG)
    doors = gt["plan"]["doors"]
    ids = {r["name"]: r["id"] for r in gt["plan"]["rooms"]}
    sides = [sorted(d["rooms"]) for d in doors]
    assert sorted([ids["LivingRoom"], ids["Entry Lobby"]]) in sides
    assert sorted([ids["Entry Lobby"], "outside"]) in sides
    assert sorted([ids["Entry Lobby"], ids["Closet WalkIn"]]) in sides
    assert sorted([ids["LivingRoom"], ids["Outdoor"]]) in sides
    assert doors[0]["at"] == [600, 195] and doors[0]["width"] == 90
    ent = doors[gt["plan"]["entrance"]]
    assert sorted(ent["rooms"]) == sorted([ids["Entry Lobby"], "outside"])
    assert gt["door_points"] == [[600, 195], [800, 145], [700, 400], [345, 400]]
    w = gt["plan"]["windows"]
    assert w[0] == {"at": [100, 200], "width": 200, "room": ids["LivingRoom"]}


def test_a_window_between_a_room_and_a_balcony_lights_the_room():
    gt = plans.parse_cubicasa_svg(SVG)
    ids = {r["name"]: r["id"] for r in gt["plan"]["rooms"]}
    assert gt["plan"]["windows"][1] == {"at": [500, 400], "width": 120, "room": ids["LivingRoom"]}


def test_two_spaces_touching_with_no_wall_between_them_are_joined_by_an_opening():
    gt = plans.parse_cubicasa_svg(SVG)
    ids = {r["name"]: r["id"] for r in gt["plan"]["rooms"]}
    living_outdoor = [d for d in gt["plan"]["doors"] if sorted(d["rooms"]) == sorted([ids["LivingRoom"],
                                                                                       ids["Outdoor"]])]
    assert len(living_outdoor) == 1  # they have a door already: no opening is added
    corner = [d for d in gt["plan"]["doors"] if sorted(d["rooms"]) == sorted([ids["LivingRoom"],
                                                                              ids["Closet WalkIn"]])]
    assert corner == []  # they touch at a corner only
    opening = [d for d in gt["plan"]["doors"] if sorted(d["rooms"]) == sorted([ids["Outdoor"], ids["Closet WalkIn"]])]
    assert len(opening) == 1  # a metre of boundary in common, no wall drawn between them
    assert opening[0]["at"] == [600, 450] and opening[0]["width"] == pytest.approx(80)
    assert gt["openings"] == 1 and len(gt["door_points"]) == 4  # an opening is no drawn door


# --- the metrics, on a hand-made ground truth ---------------------------------------------------------------------

def truth():
    rooms = [{"id": "t1", "name": "a", "type": "living", "polygon": rect(0, 0, 400, 400)},
             {"id": "t2", "name": "b", "type": "kitchen", "polygon": rect(400, 0, 700, 400)},
             {"id": "t3", "name": "c", "type": "bedroom", "polygon": rect(0, 400, 400, 700)},
             {"id": "t4", "name": "d", "type": "corridor", "polygon": rect(400, 400, 700, 700)}]
    doors = [{"at": [400, 200], "width": 90, "rooms": ["t1", "t2"]},
             {"at": [200, 400], "width": 90, "rooms": ["t1", "t3"]},
             {"at": [700, 550], "width": 90, "rooms": ["t4", "outside"]}]
    plan = {"format": "vesper-plan/1", "units": "px", "scale": {"m_per_px": 0.01}, "rooms": rooms, "doors": doors,
            "windows": [], "entrance": 2}
    return {"plan": plan, "m_per_px": 0.01, "door_points": [d["at"] for d in doors]}


def predicted():
    rooms = [{"id": "p1", "name": "x", "type": "living", "polygon": rect(0, 0, 400, 400)},  # exact
             {"id": "p2", "name": "y", "type": "dining", "polygon": rect(420, 0, 700, 400)},  # IoU 0.933, wrong type
             {"id": "p3", "name": "z", "type": "hall", "polygon": rect(400, 400, 700, 700)},  # hall ~ corridor
             {"id": "p4", "name": "w", "type": "other", "polygon": rect(0, 700, 300, 800)}]  # nothing there
    doors = [{"at": [410, 230], "width": 80, "rooms": ["p1", "p2"]},  # 31.6 px = 0.32 m from a true door
             {"at": [280, 400], "width": 80, "rooms": ["p1", "p4"]},  # 80 px = 0.8 m: too far
             {"at": [700, 560], "width": 80, "rooms": ["p3", "outside"]}]
    return {"format": "vesper-plan/1", "units": "px", "scale": None, "rooms": rooms, "doors": doors, "windows": [],
            "entrance": 2}


def test_rooms_are_found_at_iou_one_half_one_to_one():
    s = plans.score(predicted(), truth(), res=5)
    assert (s["rooms_true"], s["rooms_pred"], s["rooms_matched"]) == (4, 4, 3)
    assert s["room_precision"] == pytest.approx(0.75) and s["room_recall"] == pytest.approx(0.75)
    assert s["room_f1"] == pytest.approx(0.75)
    assert [m["iou"] for m in s["matches"]] == pytest.approx([1.0, 280 / 300, 1.0], abs=0.01)


def test_two_predicted_rooms_on_one_true_room_match_it_once():
    p = predicted()
    p["rooms"].append({"id": "p5", "name": "v", "type": "living", "polygon": rect(0, 0, 400, 390)})  # IoU 0.975
    s = plans.score(p, truth(), res=5)
    assert s["rooms_matched"] == 3 and s["rooms_pred"] == 5
    assert [m["pred"] for m in s["matches"]].count("p1") + [m["pred"] for m in s["matches"]].count("p5") == 1


def test_a_room_overlapping_by_less_than_half_is_not_found():
    p = predicted()
    p["rooms"][0]["polygon"] = rect(0, 0, 400, 180)  # IoU 0.45 with t1
    assert plans.score(p, truth(), res=5)["rooms_matched"] == 2


def test_type_accuracy_is_over_the_matched_rooms_and_the_lenient_one_takes_hall_for_corridor():
    s = plans.score(predicted(), truth(), res=5)
    assert s["type_correct"] == 1 and s["type_accuracy"] == pytest.approx(1 / 3)
    assert s["type_accuracy_lenient"] == pytest.approx(2 / 3)


def test_a_true_door_is_found_when_a_predicted_door_is_within_half_a_metre_by_the_true_scale():
    s = plans.score(predicted(), truth(), res=5)
    assert (s["doors_true"], s["doors_pred"], s["doors_found"]) == (3, 3, 2)
    assert s["door_recall"] == pytest.approx(2 / 3)
    t = truth()
    t["m_per_px"] = 0.02  # a pixel is 2 cm: half a metre is 25 px, so the door 31.6 px away is too far now
    assert plans.score(predicted(), t, res=5)["doors_found"] == 1
    t["m_per_px"] = 0.005  # half a metre is now 100 px: the 80 px door counts too
    assert plans.score(predicted(), t, res=5)["doors_found"] == 3


def test_one_predicted_door_finds_one_true_door_only():
    p = predicted()
    p["doors"] = [{"at": [300, 300], "width": 80, "rooms": ["p1", "p2"]}]
    t = truth()
    t["door_points"] = [[300, 290], [300, 310]]
    assert plans.score(p, t, res=5)["doors_found"] == 1


def test_area_errors_use_the_true_scale_for_both_plans():
    s = plans.score(predicted(), truth(), res=5)
    true_px, pred_px = 700 * 700, 160000 + 280 * 400 + 90000 + 30000
    assert s["area_true_m2"] == pytest.approx(true_px * 1e-4)
    assert s["area_pred_m2"] == pytest.approx(pred_px * 1e-4)
    assert s["area_error_total"] == pytest.approx(abs(pred_px - true_px) / true_px)
    assert [m["area_error"] for m in s["matches"]] == pytest.approx([0.0, 20 / 300, 0.0])
    assert s["area_error_rooms_mean"] == pytest.approx((20 / 300) / 3)


def test_no_reading_finds_nothing():
    s = plans.score(None, truth(), res=5)
    assert s["rooms_matched"] == 0 and s["room_f1"] == 0.0 and s["door_recall"] == 0.0
    assert s["type_accuracy"] is None and s["area_error_total"] is None


def test_the_scale_the_reader_gave_is_compared_with_the_true_one():
    p = predicted()
    p["scale"] = {"length_m": 4.4, "from": [0, 0], "to": [400, 0]}
    assert plans.score(p, truth(), res=5)["scale_error"] == pytest.approx(0.1)
    assert plans.score(predicted(), truth(), res=5)["scale_error"] is None


def test_the_aggregate_pools_rooms_and_doors_over_the_plans():
    a = plans.score(predicted(), truth(), res=5)
    b = plans.score(None, truth(), res=5)
    agg = plans.aggregate([{"score": a}, {"score": b}])
    assert agg["plans"] == 2
    assert agg["room_precision"] == pytest.approx(3 / 4) and agg["room_recall"] == pytest.approx(3 / 8)
    assert agg["room_f1"] == pytest.approx(2 * 0.75 * 0.375 / 1.125)
    assert agg["door_recall"] == pytest.approx(2 / 6)
    assert agg["type_accuracy"] == pytest.approx(1 / 3)


# --- the sample, the build check and the saved readings -----------------------------------------------------------

def test_the_seeded_sample_of_n_is_the_start_of_the_sample_of_more():
    ids = [f"/high_quality/{k}/" for k in range(100)]
    ten, fifty = plans.sample(ids, 10, seed=0), plans.sample(ids, 50, seed=0)
    assert fifty[:10] == ten and len(set(fifty)) == 50 and ten == plans.sample(ids, 10, seed=0)
    assert ten != plans.sample(ids, 10, seed=1)
    assert ten[0] == "high_quality/" + ten[0].split("/")[1] and not ten[0].endswith("/")


def test_a_plan_id_that_is_not_a_dataset_folder_is_refused():
    with pytest.raises(ValueError):
        plans.sample(["/high_quality/../../etc/"], 1, seed=0)


def connected():
    gt = truth()
    gt["plan"]["doors"].append({"at": [550, 400], "width": 90, "rooms": ["t2", "t4"]})
    return gt


def test_the_true_plan_scaled_by_its_own_scale_passes_check_house():
    gt = connected()
    out = plans.build_check(gt["plan"], gt["m_per_px"], catalog=None)
    assert out["scale_from"] == "the plan's own scale"
    assert out["check_house"] is True and out["problems"] == []
    assert out["layout_errors"] is None  # no catalog: no home is built


def test_a_plan_that_cannot_be_straightened_does_not_pass_and_says_where_it_stopped():
    p = predicted()
    p["doors"][0]["at"] = [200, 200]  # 2 m from the wall p1 and p2 share
    out = plans.build_check(p, 0.01, catalog=None)  # no scale: the true one is used, as if the user gave it
    assert out["scale_from"] == "the true scale"
    assert out["check_house"] is False and out["stage"] == "rectify"
    assert "from their shared wall" in out["problems"][0]


def test_what_straightening_left_out_is_kept_with_the_import_s_outcome():
    out = plans.build_check(predicted(), 0.01, catalog=None)
    assert out["check_house"] is False and out["stage"] == "check_house"  # p1, p2 and p4: no way from the entrance
    assert any("not reachable" in p for p in out["problems"])
    assert out["warnings"] == ["door 2 between 'x' and 'w': the rooms do not touch (they have no wall in common): "
                               "left out (no room needs it to be reached from the entrance)"]


@pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")
def test_a_plan_that_is_a_house_is_built_and_its_layout_checked(tmp_path):
    from vesper.twins.home import load_catalog
    gt = connected()
    out = plans.build_check(gt["plan"], gt["m_per_px"], catalog=load_catalog(Path("catalog/vds")))
    assert out["stage"] == "done" and isinstance(out["layout_errors"], int) and out["devices"] > 0


def zip_with(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_fetch_takes_only_the_named_members_from_the_archive_and_records_their_checksums(tmp_path):
    blob = zip_with({"cubicasa5k/test.txt": "/high_quality/7/\n", "cubicasa5k/high_quality/7/model.svg": SVG,
                     "cubicasa5k/high_quality/7/F1_scaled.png": b"\x89PNG....", "cubicasa5k/other.bin": b"x" * 9})
    got = plans.fetch(tmp_path, ["test.txt", "high_quality/7/model.svg", "high_quality/7/F1_scaled.png"],
                      opener=lambda: io.BytesIO(blob))
    assert (tmp_path / "high_quality/7/model.svg").read_text() == SVG
    assert not (tmp_path / "other.bin").exists()
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text())
    assert set(manifest["files"]) == {"test.txt", "high_quality/7/model.svg", "high_quality/7/F1_scaled.png"}
    assert manifest["url"] == plans.SOURCE_URL and len(manifest["files"]["test.txt"]) == 64
    assert got == ["test.txt", "high_quality/7/model.svg", "high_quality/7/F1_scaled.png"]
    again = plans.fetch(tmp_path, ["test.txt"], opener=lambda: pytest.fail("nothing to fetch"))
    assert again == []


def envelope(plan: dict, cost=1.5) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(plan),
                       "total_cost_usd": cost, "duration_ms": 1000, "modelUsage": {"claude-x": {}}})


def dataset(tmp_path) -> Path:
    d = tmp_path / "ds"
    (d / "high_quality/7").mkdir(parents=True)
    (d / "test.txt").write_text("/high_quality/7/\n")
    (d / "high_quality/7/model.svg").write_text(SVG)
    (d / "high_quality/7/F1_scaled.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return d


def test_a_reading_is_saved_with_its_envelopes_and_reused_without_running_claude_again(tmp_path):
    d = dataset(tmp_path)
    gt = plans.parse_cubicasa_svg(SVG)
    calls = []

    def runner(args, cwd=None, timeout=None):
        calls.append(args)
        return envelope(gt["plan"], cost=2.25)
    first = plans.evaluate(d, 1, 0, runner=runner, catalog=None)
    folder = d / "readings" / "high_quality-7"
    assert len(calls) == 1 and (folder / "plan.json").is_file() and (folder / "envelope-1.json").is_file()
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["cost_usd"] == 2.25 and meta["calls"] == 1 and meta["image_sha256"]
    row = first["plans"][0]
    assert row["score"]["room_f1"] == pytest.approx(1.0) and row["cost_usd"] == 2.25
    again = plans.evaluate(d, 1, 0, runner=lambda *a, **k: pytest.fail("paid twice"), catalog=None)
    assert again["plans"][0]["score"] == row["score"]


def test_a_reading_keeps_the_floors_claude_says_the_image_draws_beside_the_plan(tmp_path):
    d = dataset(tmp_path)
    gt = plans.parse_cubicasa_svg(SVG)

    def runner(args, cwd=None, timeout=None):
        return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                           "result": json.dumps({"floors_drawn": 2, "plan": gt["plan"]}), "total_cost_usd": 0.5})
    plans.evaluate(d, 1, 0, runner=runner, catalog=None)
    folder = d / "readings" / "high_quality-7"
    assert json.loads((folder / "plan.json").read_text()) == gt["plan"]  # a vesper-plan/1, unwrapped
    assert json.loads((folder / "meta.json").read_text())["floors_drawn"] == 2


def test_a_failed_reading_is_saved_too_and_scores_nothing(tmp_path):
    d = dataset(tmp_path)

    def runner(args, cwd=None, timeout=None):
        return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                           "result": '{"error": "not a floor plan"}', "total_cost_usd": 0.5})
    out = plans.evaluate(d, 1, 0, runner=runner, catalog=None)
    row = out["plans"][0]
    assert "not a floor plan" in row["error"] and row["score"]["rooms_matched"] == 0 and row["cost_usd"] == 1.0
    folder = d / "readings" / "high_quality-7"
    assert (folder / "error.txt").is_file() and (folder / "envelope-2.json").is_file()


def test_offline_a_plan_with_no_reading_is_left_out_not_read(tmp_path):
    d = dataset(tmp_path)
    out = plans.evaluate(d, 1, 0, runner=lambda *a, **k: pytest.fail("ran claude"), catalog=None, offline=True)
    assert out["plans"] == [] and out["not_read"] == ["high_quality/7"]


def test_a_failed_reading_is_not_retried_on_the_next_run(tmp_path):
    d = dataset(tmp_path)
    folder = d / "readings" / "high_quality-7"
    folder.mkdir(parents=True)
    (folder / "error.txt").write_text("could not read a floor plan")
    (folder / "meta.json").write_text(json.dumps({"seconds": 5.0, "cost_usd": 0.75, "calls": 2}))
    out = plans.evaluate(d, 1, 0, runner=lambda *a, **k: pytest.fail("paid twice"), catalog=None)
    assert out["plans"][0]["error"] == "could not read a floor plan" and out["plans"][0]["cost_usd"] == 0.75


def test_the_cli_writes_the_results_and_prints_the_metrics(tmp_path, capsys):
    from vesper import cli
    d = dataset(tmp_path)
    gt = plans.parse_cubicasa_svg(SVG)
    folder = d / "readings" / "high_quality-7"
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text(json.dumps(gt["plan"]))  # a saved reading: no Claude is run
    (folder / "meta.json").write_text(json.dumps({"seconds": 12.0, "cost_usd": 3.0, "calls": 1, "models": ["m"]}))
    out = tmp_path / "res.json"
    assert cli.main(["validate", "plans", "--dataset", str(d), "--n", "1", "--seed", "0", "--out", str(out),
                     "--offline", "--catalog", str(tmp_path / "no-catalog")]) == 0
    res = json.loads(out.read_text())
    assert res["aggregate"]["room_f1"] == pytest.approx(1.0) and res["protocol"]["n"] == 1
    printed = capsys.readouterr().out
    assert "high_quality/7" in printed and "room F1" in printed


def test_a_ranged_read_waits_and_retries_when_the_server_says_too_many_requests(monkeypatch):
    from vesper.validation import plans

    class Resp:
        def __init__(self, status, content=b"", headers=None):
            self.status_code, self.content, self.headers = status, content, headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class Http:
        def __init__(self):
            self.calls = 0

        def get(self, url, headers):
            self.calls += 1
            return Resp(429, headers={"retry-after": "2"}) if self.calls < 3 else Resp(206, b"abcd")

    slept = []
    monkeypatch.setattr(plans.time, "sleep", slept.append)
    r = plans._Ranged.__new__(plans._Ranged)
    r._http, r._url, r._size, r._pos, r.fetched = Http(), "u", 4, 0, 0
    buf = bytearray(4)
    assert r.readinto(buf) == 4 and bytes(buf) == b"abcd"
    assert r._http.calls == 3 and slept[:2] == [2.0, 2.0]
