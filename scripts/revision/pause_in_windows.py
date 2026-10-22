"""Pause a job tree (SIGSTOP) during the shared Mac's protected windows or sustained memory pressure.

Usage: python3 pause_in_windows.py ROOT_PID [--margin-min 5]

Re-reads BUSY_WINDOWS.json every cycle, so window edits by the other session take effect.
Pauses from `margin` minutes before a window until its end; also pauses after memory
pressure (kern.memorystatus_vm_pressure_level) has been >= 2 for 10 minutes, and resumes
once it is back to normal. Exits when ROOT_PID is gone. Free swap is logged but not used:
macOS resizes its swap files, so low free swap occurs under normal pressure
(the other session, 2026-10-14).
"""
import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import time

BUSY = os.environ["BUSY_WINDOWS"]  # the shared Mac's protected-windows file (JSON)
PERIOD_S = 30
PRESSURE_HOLD_S = 600


def log(msg):
    print(f"{dt.datetime.utcnow():%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def tree(root):
    pids, todo = [], [root]
    while todo:
        p = todo.pop()
        pids.append(p)
        out = subprocess.run(["pgrep", "-P", str(p)], capture_output=True, text=True).stdout
        todo += [int(x) for x in out.split()]
    return pids


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def in_window(now, margin):
    try:
        windows = json.load(open(BUSY))["windows"]
    except Exception as e:  # unreadable file: be conservative and keep the last state
        log(f"cannot read {BUSY}: {e}")
        return None
    for w in windows:
        a = dt.datetime.fromisoformat(w["from_utc"].replace("Z", "+00:00")) - margin
        b = dt.datetime.fromisoformat(w["to_utc"].replace("Z", "+00:00"))
        if a <= now < b:
            return f"{w['from_utc']}..{w['to_utc']} ({w['why']})"
    return ""


def pressure():
    lvl = int(subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                             capture_output=True, text=True).stdout.strip() or 1)
    swap = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
    m = re.search(r"free = ([\d.]+)([MG])", swap)
    free_gb = (float(m.group(1)) / (1024 if m.group(2) == "M" else 1)) if m else 99.0
    return lvl, free_gb


def signal_tree(root, sig):
    for p in tree(root):
        try:
            os.kill(p, sig)
        except ProcessLookupError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root_pid", type=int)
    ap.add_argument("--margin-min", type=float, default=5)
    a = ap.parse_args()
    margin = dt.timedelta(minutes=a.margin_min)
    paused, high_since, last_win = False, None, ""
    log(f"watching pid {a.root_pid}")
    while alive(a.root_pid):
        now = dt.datetime.now(dt.timezone.utc)
        win = in_window(now, margin)
        win = last_win if win is None else win
        last_win = win
        lvl, free_gb = pressure()
        high_since = (high_since or time.time()) if lvl >= 2 else None
        mem_bad = high_since is not None and time.time() - high_since >= PRESSURE_HOLD_S
        want = bool(win) or mem_bad
        if want and not paused:
            signal_tree(a.root_pid, signal.SIGSTOP)
            paused = True
            log(f"PAUSED window={win or '-'} pressure={lvl} swap_free_gb={free_gb:.1f}")
        elif not want and paused:
            signal_tree(a.root_pid, signal.SIGCONT)
            paused = False
            log(f"RESUMED pressure={lvl} swap_free_gb={free_gb:.1f}")
        time.sleep(PERIOD_S)
    log("root gone; watchdog exiting")


if __name__ == "__main__":
    main()
