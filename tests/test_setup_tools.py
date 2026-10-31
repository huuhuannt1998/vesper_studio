"""Setting VESPER up (Lab spec §7, plan W5): the configuration, the doctor, the Habitat environment and HSSD scenes,
with the outside tools faked."""
import json
import subprocess

import pytest

from vesper import setup_tools as st


@pytest.fixture(autouse=True)
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("VESPER_HSSD", raising=False)
    monkeypatch.delenv("VESPER_HABITAT_PYTHON", raising=False)


def never(*a, **k):
    raise AssertionError("a test ran a real command")


def done(code=0, out=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


def test_the_configuration_is_kept_and_a_flag_then_the_environment_win(tmp_path, monkeypatch):
    from vesper.lab.scenes import habitat_python, hssd_dir
    st.save_config(hssd=tmp_path / "hssd", habitat_python=tmp_path / "py")
    assert hssd_dir(None) == tmp_path / "hssd" and habitat_python(None) == tmp_path / "py"
    monkeypatch.setenv("VESPER_HSSD", "/env")
    assert str(hssd_dir(None)) == "/env" and str(hssd_dir("/flag")) == "/flag"


def test_the_doctor_names_what_is_missing_and_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv("VESPER_HABITAT_PYTHON", str(tmp_path / "no-python"))
    checks = {c.name: c for c in st.doctor(tmp_path / "root", tmp_path / "catalog", which=lambda _: None,
                                           run=lambda *a, **k: done(1), ports=())}
    assert not checks["catalog"].ok and checks["catalog"].required
    assert not checks["home assistant"].ok and checks["home assistant"].fix == "vesper setup ha"
    assert not checks["habitat"].ok and "setup habitat" in checks["habitat"].fix


def test_habitat_is_created_once_and_its_python_recorded(tmp_path):
    prefix, calls, made = tmp_path / "envs" / "vesper-habitat", [], []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "create":
            made.append(prefix)
        if cmd[1:3] == ["env", "list"]:
            return done(0, json.dumps({"envs": [str(p) for p in made]}))
        return done(0, "0.3.3")
    cmd, python = st.setup_habitat(which=lambda t: "/bin/conda" if t == "conda" else None, run=run, homes=())
    assert "habitat-sim=0.3.3" in cmd and python == prefix / "bin" / "python"
    assert st.configured("habitat_python") == str(python)
    calls.clear()
    st.setup_habitat(which=lambda t: "/bin/conda" if t == "conda" else None, run=run, homes=())
    assert not any(c[1] == "create" for c in calls)  # already there


def test_without_conda_the_error_says_what_to_install():
    with pytest.raises(st.SetupError, match="Miniforge"):
        st.setup_habitat(which=lambda _: None, run=never, homes=())


def test_a_scene_is_fetched_round_by_round_until_nothing_is_missing(tmp_path, monkeypatch):
    hssd = tmp_path / "hssd"
    tree = {"scenes-articulated/1.scene_instance.json": {"stage_instance": {"template_name": "stages/1"},
                                                        "object_instances": []},
            "semantics/scenes/1.semantic_config.json": {"region_annotations": [
                {"label": "kitchen", "poly_loop": [[0, 0, 0], [4, 0, 0], [4, 0, 4], [0, 0, 4]], "floor_height": 0,
                 "extrusion_height": 2.5}]},
            "stages/1.stage_config.json": {"render_asset": "1.glb"}, "stages/1.glb": "x",
            "metadata/fpmodels-with-decomposed.csv": "id,main_category\n"}
    fetched = []

    def run(cmd, **kw):
        for f in cmd[cmd.index("--local-dir") + 2:]:
            fetched.append(f)
            (hssd / f).parent.mkdir(parents=True, exist_ok=True)
            v = tree[f]
            (hssd / f).write_text(v if isinstance(v, str) else json.dumps(v))
        return done()
    got = st.setup_scenes(hssd, ["1"], which=lambda t: "/bin/hf" if t == "hf" else None, run=run, say=lambda _: None)
    assert set(fetched) == set(tree) and got == {"1": len(tree)}
    assert st.configured("hssd") == str(hssd.resolve())
