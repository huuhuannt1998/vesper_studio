"""The scan import's evaluation on the scan test set (task B5): the hand truth's form, the scoring of a reading
against it, the aggregate by kind and reader, and a whole run on a hand-made dataset (task B1's RoomPlan fixture and
task B3's two-room box house, read by the geometric reader and by a fake Claude). No real Claude and no real scan."""
import json
import shutil
from pathlib import Path

import pytest

from vesper.validation import scans

FIXTURES = Path(__file__).parents[1] / "lab" / "fixtures" / "roomplan"


def two_rooms_truth(**over) -> dict:
    """The truth of task B1's fixture: a living room and a bedroom, a door between, the front door, two windows."""
    t = {"kind": "roomplan", "file": "two_rooms.json", "rooms": 2, "types": [["living"], ["bedroom"]],
         "doors_inner": 1, "doors_outside": 1, "windows": 2, "entrance": True, "storeys": 1, "ceiling": False,
         "scale": {"m_per_unit": 1.0, "min": 0.95, "max": 1.05}, "up": "y", "how": "the fixture's own comment",
         "certainty": "high"}
    t.update(over)
    return t


# --- counts and types ---------------------------------------------------------------------------------------------

def test_a_count_is_right_within_the_truths_range_and_its_error_is_the_distance_to_it():
    assert scans.count_score(2, 2) == {"pred": 2, "true": 2, "min": 2, "max": 2, "ok": True, "error": 0}
    s = scans.count_score(5, {"n": 7, "min": 6, "max": 8})
    assert s["ok"] is False and s["error"] == -1 and s["true"] == 7
    assert scans.count_score(9, {"n": 7, "min": 6, "max": 8})["error"] == 1
    assert scans.count_score(6, {"n": 7, "min": 6, "max": 8})["ok"] is True


def test_an_unknown_truth_is_not_scored_and_no_reading_counts_nothing():
    assert scans.count_score(3, None) == {"pred": 3, "true": None, "min": None, "max": None, "ok": None,
                                          "error": None}
    s = scans.count_score(None, 2)
    assert s["ok"] is False and s["pred"] is None and s["error"] == -2


def test_bad_truth_counts_are_refused():
    with pytest.raises(ValueError):
        scans.count_score(1, {"n": 3, "min": 4, "max": 5})
    with pytest.raises(ValueError):
        scans.count_score(1, -1)


def test_types_are_matched_one_to_one_with_each_true_rooms_acceptable_types():
    truth = [["living"], ["bedroom"], ["kitchen", "dining"]]
    assert scans.type_score(["bedroom", "dining", "living"], truth) == {"right": 3, "of": 3}
    assert scans.type_score(["bedroom", "bedroom", "living"], truth) == {"right": 2, "of": 3}
    # a type that two true rooms accept goes to the one that needs it (a maximum matching, not a greedy one)
    assert scans.type_score(["kitchen", "dining"], [["kitchen", "dining"], ["kitchen"]]) == {"right": 2, "of": 2}


def test_rooms_typed_other_score_only_where_the_truth_accepts_other():
    assert scans.type_score(["other", "other"], [["living"], ["bedroom"]]) == {"right": 0, "of": 2}
    assert scans.type_score(["other", "other"], [["other"], ["bedroom"]]) == {"right": 1, "of": 2}
    assert scans.type_score([], [["living"]]) == {"right": 0, "of": 1}
    assert scans.type_score(["living"], None) == {"right": None, "of": None}


# --- what a reading says -----------------------------------------------------------------------------------------

def plan_m(entrance=1) -> dict:
    sq = lambda x0, z0, x1, z1: [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]  # noqa: E731
    return {"format": "vesper-plan/1", "units": "m", "scale": None,
            "rooms": [{"id": "r1", "name": "A", "type": "living", "polygon": sq(0, 0, 5, 4)},
                      {"id": "r2", "name": "B", "type": "other", "polygon": sq(5, 0, 8, 4)}],
            "doors": [{"at": [5, 2], "width": 0.9, "rooms": ["r1", "r2"]},
                      {"at": [2, 4], "width": 0.9, "rooms": ["r1", "outside"]},
                      {"at": [8, 2], "width": 0.9, "rooms": ["outside", "r2"]}],
            "windows": [{"at": [2.5, 0], "width": 1.2, "room": "r1"}], "entrance": entrance}


def test_a_readings_facts_split_inner_doors_from_doors_to_the_outside():
    f = scans.reading_facts(plan_m(), {"up": "y", "scale": 1.0, "ceiling_y": 2.6, "reader": "geometric"}, [])
    assert f["rooms"] == 2 and f["types"] == ["living", "other"]
    assert f["doors_inner"] == 1 and f["doors_outside"] == 2 and f["windows"] == 1
    assert f["entrance"] == "real" and f["up"] == "y" and f["scale"] == 1.0 and f["ceiling"] is True


def test_an_entrance_is_assumed_when_claude_put_it_in_a_drawn_wall_or_the_reader_fell_back():
    meta = {"up": "y", "scale": 1.0, "ceiling_y": None, "reader": "claude", "entrance_drawn": False}
    assert scans.reading_facts(plan_m(), meta, [])["entrance"] == "assumed"
    geo = {"up": "y", "scale": 1.0, "ceiling_y": None, "reader": "geometric"}
    said = ["no door to the outside found: the widest door out of the rooms found (0.90 m, into a space too small or "
            "open to be a room) is taken as the entrance"]
    assert scans.reading_facts(plan_m(), geo, said)["entrance"] == "assumed"
    assert scans.reading_facts(plan_m(entrance=None), geo, [])["entrance"] == "none"


def test_a_roomplan_reading_is_in_metres_with_y_up_and_no_ceiling():
    f = scans.reading_facts(plan_m(), None, [])
    assert f["up"] == "y" and f["scale"] == 1.0 and f["ceiling"] is False


# --- the score ---------------------------------------------------------------------------------------------------

def test_a_reading_that_matches_the_truth_scores_right_everywhere():
    facts = scans.reading_facts(plan_m(), {"up": "y", "scale": 1.0, "ceiling_y": None}, [])
    s = scans.score(facts, two_rooms_truth(doors_outside=2, windows=1, types=[["living"], ["other", "bedroom"]]))
    assert s["rooms"]["ok"] and s["doors_inner"]["ok"] and s["doors_outside"]["ok"] and s["windows"]["ok"]
    assert s["types"] == {"right": 2, "of": 2}
    assert s["entrance_ok"] is True and s["scale_ok"] is True and s["up_ok"] is True and s["ceiling_ok"] is True


def test_the_wrong_up_axis_scale_ceiling_or_an_invented_entrance_are_each_wrong():
    facts = scans.reading_facts(plan_m(), {"up": "-y", "scale": 2.0, "ceiling_y": 3.0}, [])
    s = scans.score(facts, two_rooms_truth(entrance=False))
    assert s["up_ok"] is False and s["scale_ok"] is False and s["ceiling_ok"] is False
    assert s["entrance_ok"] is False  # the scan has no door out, and the reader gave one as real
    honest = scans.reading_facts(plan_m(), {"up": "y", "scale": 1.0, "ceiling_y": None, "entrance_drawn": False}, [])
    assert scans.score(honest, two_rooms_truth(entrance=False))["entrance_ok"] is True


def test_an_unknown_truth_field_is_not_scored():
    facts = scans.reading_facts(plan_m(), {"up": "y", "scale": 1.0, "ceiling_y": None}, [])
    s = scans.score(facts, two_rooms_truth(windows=None, entrance=None, types=None, scale=None, up=None,
                                           ceiling=None))
    assert s["windows"]["ok"] is None and s["entrance_ok"] is None and s["types"]["right"] is None
    assert s["scale_ok"] is None and s["up_ok"] is None and s["ceiling_ok"] is None


def test_no_reading_scores_wrong_on_what_the_truth_knows():
    s = scans.score(None, two_rooms_truth())
    assert s["rooms"]["ok"] is False and s["types"] == {"right": 0, "of": 2}
    assert s["entrance_ok"] is False and s["scale_ok"] is False and s["up_ok"] is False


# --- the truth file ----------------------------------------------------------------------------------------------

def test_the_truth_file_is_checked(tmp_path):
    good = {"schema": scans.TRUTH_SCHEMA, "models": {"a": two_rooms_truth()}}
    (tmp_path / "truth.json").write_text(json.dumps(good))
    assert scans.load_truth(tmp_path / "truth.json")["models"]["a"]["kind"] == "roomplan"
    for bad in ({"kind": "photo"}, {"file": None}, {"rooms": {"n": 2, "min": 3, "max": 4}},
                {"types": [["living"], ["castle"]]}, {"up": "w"}):
        broken = {"schema": scans.TRUTH_SCHEMA, "models": {"a": two_rooms_truth(**bad)}}
        (tmp_path / "truth.json").write_text(json.dumps(broken))
        with pytest.raises(ValueError):
            scans.load_truth(tmp_path / "truth.json")
    (tmp_path / "truth.json").write_text(json.dumps({"schema": "other/1", "models": {}}))
    with pytest.raises(ValueError):
        scans.load_truth(tmp_path / "truth.json")


# --- the aggregate -----------------------------------------------------------------------------------------------

def row(kind, reader, rooms_ok, layout_errors, stage="done", types=(1, 2)):
    return {"model": "m", "kind": kind, "reader": reader, "stage": stage, "error": None,
            "score": {"rooms": {"ok": rooms_ok, "error": 0 if rooms_ok else 1},
                      "types": {"right": types[0], "of": types[1]},
                      "doors_inner": {"ok": True, "error": 0}, "doors_outside": {"ok": None, "error": None},
                      "windows": {"ok": False, "error": -2}, "entrance_ok": True, "scale_ok": True, "up_ok": None,
                      "ceiling_ok": True},
            "build": {"stage": stage, "check_house": stage in ("build", "layout", "done"),
                      "layout_errors": layout_errors}}


def test_the_aggregate_is_by_kind_and_reader_with_shares_out_of_every_model():
    rows = [row("mesh_scan", "geometric", True, 0), row("mesh_scan", "geometric", False, None, stage="check_house"),
            row("mesh_scan", "claude", True, 2), row("roomplan", "roomplan", True, 0)]
    agg = scans.aggregate(rows)
    g = agg["mesh_scan/geometric"]
    assert g["models"] == 2 and g["rooms_ok"] == 0.5 and g["rooms_abs_error"] == 0.5
    assert g["types_right"] == 0.5 and g["windows_abs_error"] == 2.0 and g["doors_outside_ok"] is None
    assert g["check_house"] == 0.5 and g["layout_zero_errors"] == 0.5 and g["stages"] == {"done": 1,
                                                                                         "check_house": 1}
    assert agg["mesh_scan/claude"]["layout_zero_errors"] == 0.0
    assert agg["roomplan/roomplan"]["models"] == 1
    assert agg["all/geometric"]["models"] == 2


# --- a whole run on a hand-made dataset --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    from vesper.lab import plan_read

    def refuse(*a, **k):
        raise AssertionError("a test ran the real claude")
    monkeypatch.setattr(plan_read, "_claude", refuse)


def roomplan_dataset(tmp_path) -> Path:
    ds = tmp_path / "scans"
    (ds / "rp").mkdir(parents=True)
    shutil.copy(FIXTURES / "two_rooms.json", ds / "rp" / "two_rooms.json")
    truth = {"schema": scans.TRUTH_SCHEMA, "models": {"rp": two_rooms_truth()}}
    (ds / "truth.json").write_text(json.dumps(truth))
    return ds


def test_a_roomplan_scan_is_read_scored_built_and_its_layout_checked(tmp_path):
    from tests.lab.test_cli_lab import setup
    from vesper.twins.home import load_catalog
    cat = load_catalog(setup(tmp_path)[0])
    result = scans.evaluate(roomplan_dataset(tmp_path), readers=("geometric", "claude"), catalog=cat)
    [r] = result["rows"]  # a RoomPlan file has its own reader, whichever readers are asked for
    assert r["reader"] == "roomplan" and r["kind"] == "roomplan" and r["error"] is None
    s = r["score"]
    assert s["rooms"]["ok"] and s["types"] == {"right": 2, "of": 2} and s["doors_inner"]["ok"]
    assert s["doors_outside"]["ok"] and s["windows"]["ok"] and s["entrance_ok"] and s["scale_ok"] and s["up_ok"]
    assert r["build"]["stage"] == "done" and r["build"]["layout_errors"] == 0
    assert result["aggregate"]["roomplan/roomplan"]["layout_zero_errors"] == 1.0


def test_without_a_catalog_the_run_stops_after_check_house(tmp_path):
    [r] = scans.evaluate(roomplan_dataset(tmp_path))["rows"]
    assert r["build"]["stage"] == "check_house" and r["build"]["check_house"] is True
    assert r["build"]["layout_errors"] is None


def mesh_dataset(tmp_path, claude_dir=None) -> Path:
    pytest.importorskip("trimesh")
    pytest.importorskip("scipy")
    from tests.lab.test_scan_mesh import house, save
    ds = tmp_path / "scans"
    (ds / "box").mkdir(parents=True)
    save(house(), ds / "box" / "house.glb")
    entry = two_rooms_truth(kind="web_model", file="house.glb", types=[["bedroom"], ["living"]], windows=1,
                            ceiling=True)
    if claude_dir:
        entry["claude_dir"] = claude_dir
    (ds / "truth.json").write_text(json.dumps({"schema": scans.TRUTH_SCHEMA, "models": {"box": entry}}))
    return ds


def test_a_mesh_is_read_by_each_reader_and_claudes_reading_is_kept_and_reused(tmp_path):
    from tests.lab.test_plan_read import Fake, envelope
    from tests.lab.test_scan_render import answer_in_pixels
    from vesper.lab.scan_render import render_plan_view
    ds = mesh_dataset(tmp_path)
    view = render_plan_view(ds / "box" / "house.glb", tmp_path / "probe.png")
    fake = Fake(envelope(json.dumps(answer_in_pixels(view))))
    result = scans.evaluate(ds, readers=("geometric", "claude"), runner=fake)
    geo, cl = sorted(result["rows"], key=lambda r: r["reader"] != "geometric")
    assert geo["reader"] == "geometric" and geo["score"]["rooms"]["ok"] and geo["score"]["types"]["right"] == 0
    assert cl["reader"] == "claude" and cl["reading"] == "new" and cl["score"]["types"] == {"right": 2, "of": 2}
    assert cl["score"]["windows"]["ok"] and cl["score"]["entrance_ok"] and cl["score"]["up_ok"]
    folder = ds / "_claude" / "box"
    assert json.loads((folder / "reading.json").read_text())["format"] == "vesper-scan-reading/1"
    assert (folder / "render.png").is_file() and (folder / "envelope-1.json").is_file()
    assert json.loads((folder / "meta.json").read_text())["calls"] == 1
    # again, offline: the kept reading is used and Claude is not run
    again = scans.evaluate(ds, readers=("claude",), offline=True)
    [r] = again["rows"]
    assert r["reading"] == "saved" and r["score"] == cl["score"] and len(fake.calls) == 1


def test_offline_a_mesh_with_no_saved_reading_is_listed_not_read(tmp_path):
    ds = mesh_dataset(tmp_path)
    result = scans.evaluate(ds, readers=("claude",), offline=True)
    assert result["rows"] == [] and result["not_read"] == ["box/claude"]


def test_a_b3b_reading_beside_its_render_is_used(tmp_path):
    """Task B3b kept Claude's plan (claude-plan.json, metres) and meta (claude-meta.json) per model: read as a saved
    reading, the model under them from the truth's file."""
    from tests.lab.test_plan_read import Fake, envelope
    from tests.lab.test_scan_render import answer_in_pixels
    from vesper.lab.scan_render import read_mesh_with_claude, render_plan_view
    ds = mesh_dataset(tmp_path, claude_dir="b3b-box")
    view = render_plan_view(ds / "box" / "house.glb", tmp_path / "probe.png")
    plan, meta, _ = read_mesh_with_claude(ds / "box" / "house.glb",
                                          runner=Fake(envelope(json.dumps(answer_in_pixels(view)))))
    folder = ds / "_claude" / "b3b-box"
    folder.mkdir(parents=True)
    meta.pop("entrance_drawn")  # B3b's meta has none: it is worked out again from the render
    (folder / "claude-plan.json").write_text(json.dumps(plan))
    (folder / "claude-meta.json").write_text(json.dumps(meta))
    shutil.copy(tmp_path / "probe.png", folder / "claude-render.png")
    [r] = scans.evaluate(ds, readers=("claude",), offline=True)["rows"]
    assert r["reading"] == "saved (B3b)" and r["score"]["rooms"]["ok"] and r["facts"]["entrance"] == "real"


def test_a_failed_claude_reading_is_kept_and_not_paid_for_again(tmp_path):
    from tests.lab.test_plan_read import Fake, envelope
    ds = mesh_dataset(tmp_path)
    fake = Fake(envelope("I cannot read this."), envelope("Still cannot."))
    [r] = scans.evaluate(ds, readers=("claude",), runner=fake)["rows"]
    assert r["stage"] == "read" and "Still cannot" in r["error"]
    assert (ds / "_claude" / "box" / "error.txt").is_file()
    [again] = scans.evaluate(ds, readers=("claude",), runner=Fake())["rows"]
    assert again["stage"] == "read" and again["reading"] == "saved"


def test_vesper_validate_scans_writes_the_report_and_prints_the_table(tmp_path, capsys):
    from vesper import cli
    ds, out = roomplan_dataset(tmp_path), tmp_path / "out.json"
    assert cli.main(["validate", "scans", "--dataset", str(ds), "--offline", "--catalog", str(tmp_path / "none"),
                     "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "RoomPlan/roomplan" in printed and "100%" in printed
    report = json.loads(out.read_text())
    assert report["rows"][0]["model"] == "rp" and report["run"]["command"] == "validate scans"
    assert cli.main(["validate", "scans", "--dataset", str(ds), "--only", "nope", "--out", str(out)]) == 1


def test_a_mesh_no_plan_was_read_from_is_scored_on_the_up_axis_and_scale_it_was_found_to_have():
    s = scans.score({"up": "y", "scale": 1.0, "ceiling": False}, two_rooms_truth())
    assert s["up_ok"] and s["scale_ok"] and s["ceiling_ok"]
    assert s["rooms"]["ok"] is False and s["entrance_ok"] is False and s["types"] == {"right": 0, "of": 2}


def test_a_failed_geometric_reading_keeps_what_was_detected(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("scipy")
    from tests.lab.test_scan_mesh import house, save
    ds = mesh_dataset(tmp_path)
    save(house(doors=False), ds / "box" / "house.glb")  # two rooms and no door between them: refused
    [r] = scans.evaluate(ds, readers=("geometric",))["rows"]
    assert r["stage"] == "read" and "no door between" in r["error"] and r["facts"] is None
    assert r["detected"] == {"up": "y", "scale": pytest.approx(1.0), "ceiling": True}
    assert r["score"]["up_ok"] and r["score"]["scale_ok"]
