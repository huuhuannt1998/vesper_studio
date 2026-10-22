from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Tracked: present in every checkout.
KEEP_TRACKED = [
    "vesper/core/event_bus.py", "vesper/automation/tap_engine.py",
    "vesper/simulation/event_stream.py", "vesper/simulation/task_database.py",
    "vesper/simulation/task_generator.py", "vesper/simulation/task_system.py",
    "vesper/simulation/time_manager.py", "vesper/agents/llm_client.py",
    "scripts/revision/e2_embodied.py", "scripts/revision/e1v2_workloads.py",
    "scripts/revision/casas_workloads.py", "scripts/revision/casas_data_quality.py",
    "scripts/revision/e1v2_llm_schedules.py", "scripts/revision/rule_corpus_prevalence.py",
    "scripts/revision/pause_in_windows.py", "scripts/extract_scene_info.py",
    "scripts/cleanup/archive_then_delete.py", "tests/test_event_bus.py",
]

# Untracked research data and assets: present only in the original working copy.
KEEP_LOCAL = [
    "results/revision/llm_schedules", "results/revision/casas",
    "results/revision/e2/embodied", "results/revision/rule_corpus",
    "results/revision/novelty/hssd_regions.json",  # read by e2_embodied.py `scenes`
    "data", "habitat-lab-official",
]

GONE = [
    "vesper/attacks", "vesper/network", "vesper/protocol", "vesper/hub", "vesper/integrations",
    "vesper/firmware", "vesper/devices", "vesper/dashboard", "vesper/matter", "vesper/habitat",
    "vesper/evaluation", "vesper/utils", "vesper/logs", "vesper/engine.py", "vesper/platform.py",
    "vesper/config.py", "vesper/__main__.py", "vesper/core/environment.py", "vesper/core/registry.py",
    "vesper/simulation/autonomous_simulation.py", "vesper/agents/base.py",
    "vesper/agents/controller.py", "vesper/agents/llm_controller.py", "vesper/agents/smart_agent.py",
    "docker", "build", "esp32_demo", "configs", "typescript", "requirements.txt", "vesper.egg-info",
    "INFOCOM-2027-manuscript", "IoTJ-manuscript", "logs", "$OUTDIR", "IoT-Phantom-Delay-Attack-main",
    "scratchpad_out", "simulator_screenshots", "gpt-feedback.md", "vesper-sh.zip",
    "docs/3D_SENSOR_INTEGRATION.md", "docs/WIFI_3D_SETUP.md",
    "results/iotj_seccampaign", "results/pcap_analysis", "results/e1_coupling",
    "results/revision/e2/casas", "results/revision/e1v2", "results/revision/e_a_ha",
    "scripts/run_autonomous_eval.py", "scripts/attacks", "scripts/dataset",
    "scripts/revision/e2_replay.py", "scripts/revision/e1v2_paired_replay.py",
    "scripts/revision/e_a_ha_driver.py", "tests/test_network.py", "tests/test_devices.py",
]


def test_tracked_kept_paths_exist():
    missing = [p for p in KEEP_TRACKED if not (ROOT / p).exists()]
    assert missing == []


@pytest.mark.skipif(not (ROOT / "results/revision").is_dir(),
                    reason="local research data exists only in the original working copy")
def test_local_kept_paths_exist():
    missing = [p for p in KEEP_LOCAL if not (ROOT / p).exists()]
    assert missing == []


def test_legacy_paths_removed():
    present = [p for p in GONE if (ROOT / p).exists()]
    assert present == []
