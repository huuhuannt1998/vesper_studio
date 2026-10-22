#!/usr/bin/env python3
"""
Descriptive data-quality statistics for the CASAS HH homes converted by
casas_workloads.py.  NO attack replay and NO outcome analysis: this only
describes the traces (preregistration hygiene).

Writes results/revision/casas/{homes.csv, room_map.csv, days.csv, DATA_QUALITY.md}.

Usage:
  PYTHONPATH=. /usr/bin/python3 scripts/revision/casas_data_quality.py [--jobs 6]
"""
from __future__ import annotations

import argparse
import bisect
import csv
import statistics
import sys
from array import array
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import casas_workloads as C  # noqa: E402
from e1v2_workloads import DAY, ROOMS  # noqa: E402

ENTRY_WINDOW_S = 10
DEPART_SILENCE_S = 1800


def _q(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _door_open_hours(trace):
    tot, t_open = 0, None
    for (s, k, _) in trace:
        if k == "door_open" and t_open is None:
            t_open = s
        elif k == "door_close" and t_open is not None:
            tot += s - t_open
            t_open = None
    if t_open is not None:
        tot += DAY - t_open
    return tot / 3600.0


def analyse_home(home: str):
    hd = C._load_home(home)
    recs, on_counts = C._day_quality(home)
    ds = C._door_stream(home)
    usable = [r for r in recs if r.usable]
    n_us = len(usable)
    summ = C.home_summary(home)

    # ── raw location x sensor-type evidence ────────────────────────────────
    loc_rows = []
    state_counts = Counter()
    seqs = {}
    contact_close = Counter()
    contact_silent = Counter()
    all_on_abs = array("d")
    for d in hd.dates:
        o, t, lo, st = hd.ordinal[d], hd.t[d], hd.loc[d], hd.st[d]
        for i in range(len(t)):
            state_counts[(lo[i], st[i])] += 1
            if st[i] == C.ON:
                all_on_abs.append(C._abs(o, t[i]))
    for d in hd.dates:                                  # departure-like silence after CLOSE
        o, t, lo, st = hd.ordinal[d], hd.t[d], hd.loc[d], hd.st[d]
        for i in range(len(t)):
            if st[i] == C.CLOSE:
                a = C._abs(o, t[i])
                j = bisect.bisect_right(all_on_abs, a + 60)
                nxt = all_on_abs[j] if j < len(all_on_abs) else None
                if nxt is None:
                    continue
                contact_close[lo[i]] += 1
                contact_silent[lo[i]] += nxt > a + DEPART_SILENCE_S
    # repeated ON (multi-sensor indicator) per location, over all days
    for li in range(len(C.LOCATIONS)):
        seqs[li] = [0, 0]
    prev_state = {}
    for d in hd.dates:
        lo, st = hd.loc[d], hd.st[d]
        for i in range(len(lo)):
            if st[i] in (C.ON, C.OFF):
                p = prev_state.get(lo[i])
                seqs[lo[i]][0] += 1
                if p == C.ON and st[i] == C.ON:
                    seqs[lo[i]][1] += 1
                prev_state[lo[i]] = st[i]
    adj = C._adjacency(home)
    maps = {m: C.resolve_mapping(home, m) for m in ("default", "all_living", "adjacency")}
    for li, loc in enumerate(C.LOCATIONS):
        n_onoff = state_counts[(li, C.ON)] + state_counts[(li, C.OFF)]
        n_oc = state_counts[(li, C.OPEN)] + state_counts[(li, C.CLOSE)]
        n_num = hd.other_states.get((loc, "numeric"), 0)
        n_btn = hd.other_states.get((loc, "button"), 0)
        if not (n_onoff or n_oc or n_num or n_btn):
            continue
        cov = (sum(1 for r in usable if on_counts[r.date].get(loc, 0) > 0) / n_us) if n_us else 0.0
        nb = adj.get(loc, Counter())
        tot = sum(nb.values())
        top = "; ".join(f"{k} {v / tot:.0%}" for k, v in nb.most_common(2)) if tot else ""
        loc_rows.append(dict(
            home=home, location=loc, n_on_off=n_onoff, n_open_close=n_oc, n_numeric=n_num,
            n_button=n_btn,
            repeated_on_frac=round(seqs[li][1] / seqs[li][0], 3) if seqs[li][0] else "",
            multi_sensor=bool(seqs[li][0] and seqs[li][1] / seqs[li][0] > 0.01),
            close_then_30min_silence=(round(contact_silent[li] / contact_close[li], 3)
                                      if contact_close[li] else ""),
            motion_coverage_usable_days=round(cov, 3) if n_onoff else "",
            coactivation_top2=top,
            map_default=maps["default"].get(loc, "-") if n_onoff else "-",
            map_all_living=maps["all_living"].get(loc, "-") if n_onoff else "-",
            map_adjacency=maps["adjacency"].get(loc, "-") if n_onoff else "-",
            door_contact_used=(loc == C.DOOR_LOCATION and n_oc > 0),
        ))

    # ── per usable day trace statistics (default mapping) ───────────────────
    st = C._BuildStats()
    door_per_day, ev_per_day, rule_act_per_day, open_hours = [], [], [], []
    room_days = Counter()
    open_h_by_key = {}
    for key, tr in C.iter_casas_days(home, _stats=st):
        door_per_day.append(sum(1 for e in tr if e[1] == "door_open"))
        ev_per_day.append(len(tr))
        rule_act_per_day.append(sum(1 for e in tr if e[1] == "motion" and e[2] in ROOMS))
        open_hours.append(_door_open_hours(tr))
        open_h_by_key[key] = round(open_hours[-1], 3)
        for r in {e[2] for e in tr if e[1] == "motion"}:
            room_days[r] += 1

    # entry motion after a (debounced) door opening, usable days only
    eloc = C._LOC[C.ENTRY_LOCATION]
    entry_on = []
    for d in hd.dates:
        o, t, lo, s_ = hd.ordinal[d], hd.t[d], hd.loc[d], hd.st[d]
        for i in range(len(t)):
            if lo[i] == eloc and s_[i] == C.ON:
                entry_on.append(C._abs(o, t[i]))
    usable_ord = {hd.ordinal[r.date] for r in usable}
    n_open = n_after = n_around = 0
    for (o, s, c) in ds.events:
        if c != C.OPEN or o not in usable_ord:
            continue
        a = C._abs(o, s)
        n_open += 1
        j = bisect.bisect_left(entry_on, a)
        n_after += j < len(entry_on) and entry_on[j] <= a + ENTRY_WINDOW_S
        k = bisect.bisect_left(entry_on, a - ENTRY_WINDOW_S)
        n_around += k < len(entry_on) and entry_on[k] <= a + ENTRY_WINDOW_S

    # door episode shape (all days): raw closed intervals and debounced open durations
    raw_closed = [ds.raw_abs[k + 1] - ds.raw_abs[k] for k in range(len(ds.raw_abs) - 1)
                  if ds.raw_code[k] == C.CLOSE and ds.raw_code[k + 1] == C.OPEN]
    deb = [C._abs(o, s_) for (o, s_, c) in ds.events]
    open_durs = [deb[k + 1] - deb[k] for k in range(len(ds.events) - 1)
                 if ds.events[k][2] == C.OPEN and ds.events[k + 1][2] == C.CLOSE]
    reasons = Counter(x.split(":")[0] for r in recs for x in r.reasons)
    primary = Counter(r.reasons[0].split(":")[0] for r in recs if r.reasons)
    room_reasons = Counter(x.split(":")[1] for r in recs for x in r.reasons if ":" in x)
    first_ord, last_ord = hd.ordinal[hd.dates[0]], hd.ordinal[hd.dates[-1]]
    cov = {r: (room_days[r] / n_us if n_us else 0.0) for r in
           ROOMS + ["bathroom", "dining_room", "work_area", "office"]}
    home_row = dict(
        home=home, rules_version=C.RULES_VERSION, qualifies=summ["qualifies"],
        usable_days=n_us, door_contact_present=summ["door_contact_present"],
        dates_present=len(hd.dates), calendar_span_days=last_ord - first_ord + 1,
        file_first_date=hd.dates[0], file_last_date=hd.dates[-1],
        usable_first_date=usable[0].date if usable else "",
        usable_last_date=usable[-1].date if usable else "",
        dropped_days=len(recs) - n_us,
        drop_recording_boundary=reasons["recording_boundary"],
        drop_clock_jump=reasons["clock_jump"],
        drop_daytime_silence=reasons["daytime_silence"],
        drop_door_sensor_silent=reasons["door_sensor_silent"],
        drop_door_contact_fault=reasons["door_contact_fault"],
        drop_room_sensor_silent=reasons["room_sensor_silent"],
        room_sensor_silent_locations=";".join(f"{k}:{v}" for k, v in sorted(room_reasons.items())),
        primary_reason_counts=";".join(f"{k}:{v}" for k, v in sorted(primary.items())),
        door_openings_per_day_median=_q(door_per_day, .5),
        door_openings_per_day_q25=_q(door_per_day, .25),
        door_openings_per_day_q75=_q(door_per_day, .75),
        days_zero_door_openings=sum(1 for x in door_per_day if x == 0),
        door_open_hours_per_day_median=round(_q(open_hours, .5), 3) if open_hours else "",
        days_door_open_gt6h=sum(1 for x in open_hours if x > 6),
        days_door_open_gt12h=sum(1 for x in open_hours if x > 12),
        events_per_day_median=_q(ev_per_day, .5),
        rule_room_activations_per_day_median=_q(rule_act_per_day, .5),
        entry_motion_sensor=cov["entryway"] >= C.COVERAGE_MIN,
        hallway_sensor=cov["hallway"] >= C.COVERAGE_MIN,
        rule_rooms_present=";".join(summ["rule_rooms_present"]),
        n_rule_rooms_present=len(summ["rule_rooms_present"]),
        non_rule_rooms_present=";".join(r for r in ["bathroom", "dining_room", "work_area", "office"]
                                        if cov[r] >= C.COVERAGE_MIN),
        **{f"cov_{r}": round(cov[r], 3) for r in cov},
        door_openings_total=n_open,
        frac_open_entry_motion_within_10s_after=round(n_after / n_open, 3) if n_open else "",
        frac_open_entry_motion_within_10s_either_side=round(n_around / n_open, 3) if n_open else "",
        raw_lines=hd.n_lines, malformed_lines=hd.n_malformed, exact_duplicates_dropped=hd.n_exact_dup,
        ignored_numeric_msgs=sum(v for (l, k), v in hd.other_states.items() if k == "numeric"),
        ignored_button_msgs=sum(v for (l, k), v in hd.other_states.items() if k == "button"),
        door_msgs_raw=ds.n_raw, door_bounce_msgs_removed=ds.n_bounce_removed,
        frac_raw_closed_intervals_lt2s=round(sum(x < 2 for x in raw_closed) / len(raw_closed), 3)
        if raw_closed else "",
        n_debounced_open_episodes=len(open_durs),
        n_debounced_open_lt1s=sum(x < 1 for x in open_durs),
        door_repeat_msgs_removed=ds.n_repeat_removed,
        events_bumped_1s=st.bumped, door_events_bumped_1s=st.bumped_door,
        events_dropped_end_of_day=st.dropped_eod,
        clock_jump_days=sum(1 for v in hd.clock_jump.values() if v > C.CLOCK_JUMP_S),
    )
    day_rows = []
    door_by_date = C._door_by_date(home)
    for r in recs:
        o = hd.ordinal[r.date]
        day_rows.append(dict(
            home=home, date=r.date, key=r.key or "", usable=r.usable, reasons=";".join(r.reasons),
            binary_msgs=len(hd.t[r.date]),
            door_msgs_raw=sum(1 for i in range(len(hd.t[r.date]))
                              if hd.loc[r.date][i] == C._LOC[C.DOOR_LOCATION]
                              and hd.st[r.date][i] in (C.OPEN, C.CLOSE)),
            door_openings=sum(1 for (_, c) in door_by_date.get(o, []) if c == C.OPEN),
            motion_on=sum(on_counts[r.date].values()),
            door_open_hours_trace=open_h_by_key.get(r.key, ""),
        ))
    return home_row, loc_rows, day_rows


def _fmt(x):
    if isinstance(x, float):
        return f"{x:g}"
    return str(x)


def write_md(path: Path, homes, locs):
    q = [h for h in homes if h["qualifies"]]
    nq = [h for h in homes if not h["qualifies"]]
    loc_by_home = {}
    for r in locs:
        loc_by_home.setdefault(r["home"], []).append(r)
    L = []
    w = L.append
    w("# CASAS HH homes: ingestion data quality\n")
    w(f"Rules version `{C.RULES_VERSION}`. Generated by `scripts/revision/casas_data_quality.py` "
      "from `scripts/revision/casas_workloads.py`. Descriptive statistics only; no attack replay "
      "and no outcome analysis was run on CASAS data.\n")
    w("## 1. Qualifying homes\n")
    w(f"Qualification: exterior door contact present (debounced door opening on >= 50% of usable "
      f"days), >= {C.QUALIFY_MIN_ROOMS} canonical rule rooms with motion on >= 50% of usable days "
      f"(default mapping), >= {C.QUALIFY_MIN_DAYS} usable days.\n")
    w(f"**{len(q)} of {len(homes)} HH homes qualify:** " + ", ".join(h["home"] for h in q) + "\n")
    if nq:
        w("Not qualifying:\n")
        for h in nq:
            why = []
            if h["usable_days"] < C.QUALIFY_MIN_DAYS:
                why.append(f"{h['usable_days']} usable days")
            if h["n_rule_rooms_present"] < C.QUALIFY_MIN_ROOMS:
                why.append(f"{h['n_rule_rooms_present']} rule rooms")
            if not h["door_contact_present"]:
                why.append("exterior door contact not active on >= 50% of usable days")
            w(f"- {h['home']}: " + ", ".join(why))
        w("")
    tot_days = sum(h["usable_days"] for h in q)
    w(f"Qualifying homes contribute {tot_days} usable days in total "
      f"(median {statistics.median([h['usable_days'] for h in q]):g} per home).\n")

    w("## 2. Sensor semantics (evidence)\n")
    w("The HH files carry a location label instead of the original sensor ID, so sensor type is "
      "read from the state vocabulary. Counts per home and location are in `room_map.csv`.\n")
    w("- `ON`/`OFF`: PIR motion sensors. A location with one physical sensor alternates ON/OFF "
      "exactly (repeated-ON fraction ~0); locations with several sensors show repeated ON "
      "(`multi_sensor` column). They are the only motion source used.")
    w("- `OPEN`/`CLOSE`: contact sensors. `OutsideDoor` is the exterior door: after an OutsideDoor "
      "CLOSE the whole home is silent for >= 30 min in "
      + _range([r["close_then_30min_silence"] for r in locs if r["location"] == "OutsideDoor"
                and r["close_then_30min_silence"] != "" and r["n_open_close"] > 100])
      + " of cases (departures), versus "
      + _range([r["close_then_30min_silence"] for r in locs if r["location"] != "OutsideDoor"
                and r["close_then_30min_silence"] != "" and r["n_open_close"] > 100])
      + " for Kitchen/Bathroom/Bedroom contacts (homes with > 100 messages), so those are interior "
      "(cabinet/fridge/room doors) and are not used.")
    w("- Integer states 0-100 (mostly exactly 0 or 100): light/dimmer level messages; ignored.")
    w("- `TAP*`, `DEPRESS`, `RELEASE`, `HOLD_*`: push buttons; ignored.")
    w("- `OutsideDoor ON/OFF`: an entry-area motion sensor, present in every HH home; it is the "
      "real entry-presence source (no inferred presence is added, unlike ARAS).\n")

    w("## 3. Room mapping\n")
    w("| CASAS location | default (primary) | all_living | adjacency | rationale |")
    w("|---|---|---|---|---|")
    w("| OutsideDoor ON/OFF | entryway | entryway | entryway | entry-area motion sensor |")
    w("| OutsideDoor OPEN/CLOSE | door_open/door_close | same | same | exterior door contact |")
    w("| Hall | hallway | hallway | hallway | direct |")
    w("| Kitchen | kitchen | kitchen | kitchen | direct |")
    w("| LivingRoom | living_room | living_room | living_room | direct |")
    w("| Bedroom | bedroom | bedroom | bedroom | direct |")
    w("| Chair | living_room | living_room | per home | co-activates mostly with LivingRoom |")
    w("| DiningRoom | dining_room (non-rule) | living_room | per home | splits ~evenly between "
      "Kitchen and LivingRoom across homes |")
    w("| WorkArea | work_area (non-rule) | living_room | per home | Bedroom-adjacent in some homes, "
      "LivingRoom- or entry-adjacent in others |")
    w("| Office | office (non-rule) | living_room | per home | adjacency varies by home |")
    w("| Bathroom ON/OFF | bathroom (non-rule) | bathroom | bathroom | not a rule room (task spec) |")
    w("| Kitchen/Bathroom/Bedroom OPEN/CLOSE | dropped | dropped | dropped | interior contacts |\n")
    w("`adjacency` assigns each auxiliary location to the canonical room it most often "
      f"co-activates with (consecutive ONs at two locations <= {C.ADJ_WINDOW_S:g} s apart, usable "
      "days). Usable-day selection does not depend on the mapping. Non-rule labels stay in the "
      "trace; `casas_days(..., rule_rooms_only=True)` removes them (the e1v2 harness only creates "
      "devices for the five rule rooms).\n")
    w("### Per-home location table (motion locations; default / adjacency mapping; top co-activation)\n")
    w("| home | location | sensors | coverage | default | adjacency | co-activation top 2 |")
    w("|---|---|---|---|---|---|---|")
    for h in homes:
        for r in loc_by_home.get(h["home"], []):
            if not r["n_on_off"]:
                continue
            w(f"| {r['home']} | {r['location']} | {'multi' if r['multi_sensor'] else 'single'} | "
              f"{_fmt(r['motion_coverage_usable_days'])} | {r['map_default']} | {r['map_adjacency']} | "
              f"{r['coactivation_top2']} |")
    w("")

    w("## 4. Conversion semantics\n")
    n_ep = sum(h["n_debounced_open_episodes"] for h in homes)
    n_lt1 = sum(h["n_debounced_open_lt1s"] for h in homes)
    fr = [h["frac_raw_closed_intervals_lt2s"] for h in homes if h["frac_raw_closed_intervals_lt2s"] != ""]
    w(f"- Exterior door: a CLOSE followed by an OPEN < {C.DOOR_DEBOUNCE_S:g} s later is contact "
      "bounce and removed; repeated identical door states are then collapsed. Raw closed "
      f"intervals < 2 s make up {_range(fr)} of closed intervals per home (latch bounce, "
      "mostly < 0.5 s; > 15% in "
      + ", ".join(h["home"] for h in homes if h["frac_raw_closed_intervals_lt2s"] != ""
                  and h["frac_raw_closed_intervals_lt2s"] > 0.15)
      + "); after the debounce "
      f"{n_lt1} of {n_ep} door-open episodes ({n_lt1 / max(1, n_ep):.2%}) last < 1 s.")
    w("- Motion: every ON -> `motion`, every OFF -> `motion_off`, room-mapped, no room-level "
      "de-duplication (as ARAS, where several sensor columns share a room).")
    w("- Day = calendar date of the local timestamp, events never carried across midnight, no "
      "synthetic events at midnight (as ARAS). Exact duplicate lines dropped, lines sorted by time. "
      "Seconds floored; a same-stream event that tuple sorting would place before its predecessor "
      "in the same second is moved +1 s (`events_bumped_1s`).\n")

    w("## 5. Day rules (set from data-quality inspection only; no attack outcome has been computed on CASAS data)\n")
    w("A present calendar date is dropped if any rule fires (all reasons are kept in `days.csv`):\n")
    w("1. `recording_boundary`: first or last date of the file (partial day).")
    w(f"2. `clock_jump`: a backward timestamp jump > {C.CLOCK_JUMP_S:g} s in file order (DST "
      "fall-back repeats an hour of local time).")
    w(f"3. `daytime_silence`: a gap with no binary-sensor message whose overlap with "
      f"{C.DAYTIME[0] // 3600:02d}:00-{C.DAYTIME[1] // 3600:02d}:00 of that day exceeds "
      f"{C.SILENCE_MAX_S // 3600} h, unless an exterior-door message lies within "
      f"{C.BRACKET_S // 60} min before the gap and within {C.BRACKET_S // 60} min after it "
      "(an absence). Gaps run across midnight and missing dates.")
    w(f"4. `door_sensor_silent`: the date is in a run of >= {C.RUN_DAYS} consecutive calendar days "
      "with no OutsideDoor OPEN/CLOSE message (missing dates count as silent).")
    w(f"5. `door_contact_fault`: the raw (undebounced) exterior-door state, carried across "
      f"midnight, is OPEN for > {C.DOOR_FAULT_OPEN_S // 3600} h of the day (stuck or inverted "
      "contact; hh119 from 2014-10-09 rests OPEN and emits only ~0.2 s CLOSE pulses, which the "
      "debounce would otherwise turn into a day without door activity).")
    w(f"6. `room_sensor_silent:<loc>`: a core motion location (OutsideDoor, Hall, Kitchen, "
      f"LivingRoom, Bedroom) active on >= {C.COVERAGE_MIN:.0%} of the days passing rules 1-5 has "
      f"no ON for >= {C.RUN_DAYS} consecutive calendar days.\n")
    w("Dates absent from a file (no messages at all) are not days and are reported as "
      "`calendar_span_days - dates_present`.\n")

    w("## 6. Per-home statistics\n")
    w("Door openings are debounced; IQR = [q25, q75]; entry@10s = fraction of door openings "
      "followed by an entry (OutsideDoor) motion ON within 10 s (either side in parentheses).\n")
    w("| home | qual | usable days | usable span | door opens/day median [IQR] | events/day | "
      "rule-room activ./day | rule rooms present | entry sensor | hallway | entry@10s | "
      "dropped (silence/door-silent/door-fault/room/boundary/clock) |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for h in homes:
        w(f"| {h['home']} | {'Y' if h['qualifies'] else 'n'} | {h['usable_days']} | "
          f"{h['usable_first_date']}..{h['usable_last_date']} | "
          f"{_fmt(h['door_openings_per_day_median'])} [{_fmt(h['door_openings_per_day_q25'])}, "
          f"{_fmt(h['door_openings_per_day_q75'])}] | {_fmt(h['events_per_day_median'])} | "
          f"{_fmt(h['rule_room_activations_per_day_median'])} | {h['rule_rooms_present']} | "
          f"{'Y' if h['entry_motion_sensor'] else 'n'} | {'Y' if h['hallway_sensor'] else 'n'} | "
          f"{_fmt(h['frac_open_entry_motion_within_10s_after'])} "
          f"({_fmt(h['frac_open_entry_motion_within_10s_either_side'])}) | "
          f"{h['dropped_days']} ({h['drop_daytime_silence']}/{h['drop_door_sensor_silent']}/"
          f"{h['drop_door_contact_fault']}/{h['drop_room_sensor_silent']}/{h['drop_recording_boundary']}/{h['drop_clock_jump']}) |")
    w("")
    w("Reason counts overlap (a day can have several reasons). Full columns, including per-room "
      "coverage, debounce/duplicate/bump counts and door-open hours, are in `homes.csv`.\n")

    w("## 7. Facts a reviewer should weigh\n")
    if q:
        med_open = statistics.median([h["door_openings_per_day_median"] for h in q])
        e10 = [h["frac_open_entry_motion_within_10s_after"] for h in q
               if h["frac_open_entry_motion_within_10s_after"] != ""]
        w(f"- Door openings per usable day (median of home medians, qualifying homes): {med_open:g}; "
          f"home medians range {min(h['door_openings_per_day_median'] for h in q):g}-"
          f"{max(h['door_openings_per_day_median'] for h in q):g}.")
        w(f"- Every HH home has a real entry-area motion sensor; it fires within 10 s after a door "
          f"opening in {min(e10):.0%}-{max(e10):.0%} of openings (median {statistics.median(e10):.0%}).")
        hall = [h["home"] for h in q if h["hallway_sensor"]]
        w(f"- Only {len(hall)} qualifying home(s) have a hallway sensor ({', '.join(hall) or 'none'}); "
          "hallway-triggered rules have no input elsewhere.")
        long_open = [h["home"] for h in q if h["days_door_open_gt6h"] > 0.1 * h["usable_days"]]
        w(f"- Door left open > 6 h on > 10% of usable days in: {', '.join(long_open) or 'none'}. "
          f"Days with > 12 h open: {sum(h['days_door_open_gt12h'] for h in q)} across qualifying "
          "homes. Some are single OPEN...CLOSE episodes with no intermediate message (e.g. hh102 "
          "2013-12-05, 05:45-20:57, in December), i.e. possibly a misaligned contact; they are NOT "
          "dropped (not an obvious outage), but `days.csv:door_open_hours_trace` lets the "
          "preregistration exclude them.")
        w(f"- Usable days are long multi-year stretches with gaps; homes contribute very unequal "
          f"numbers of days ({min(h['usable_days'] for h in q)}-{max(h['usable_days'] for h in q)}), "
          "so per-home caps or weighting must be preregistered.")
    w("- The e1v2 ARAS-B and LLM conversions map bathroom activity to `hallway`; here bathroom is a "
      "non-rule label (task specification). This differs from the e1v2 convention.")
    w("- Occupancy (number of residents, pets, visitors/caregivers) is not documented in any file "
      "on disk; multi-person activity cannot be separated in these location-level logs. Verify "
      "per-home occupancy from CASAS metadata before assuming a single resident.")
    path.write_text("\n".join(L) + "\n")


def _range(xs):
    xs = [float(x) for x in xs if x != ""]
    if not xs:
        return "n/a"
    return f"{min(xs):.0%}-{max(xs):.0%}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--homes", default=",".join(C.HH_HOMES))
    a = ap.parse_args()
    homes = a.homes.split(",")
    with Pool(a.jobs) as pool:
        res = pool.map(analyse_home, homes, chunksize=1)
    out = C.OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    home_rows = [r[0] for r in res]
    loc_rows = [x for r in res for x in r[1]]
    day_rows = [x for r in res for x in r[2]]
    for name, rows in (("homes.csv", home_rows), ("room_map.csv", loc_rows), ("days.csv", day_rows)):
        with open(out / name, "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
    write_md(out / "DATA_QUALITY.md", home_rows, loc_rows)
    q = [h["home"] for h in home_rows if h["qualifies"]]
    print(f"qualifying ({len(q)}): {', '.join(q)}")


if __name__ == "__main__":
    main()
