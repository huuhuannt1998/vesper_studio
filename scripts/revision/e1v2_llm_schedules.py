#!/usr/bin/env python3
"""
Generate and PERSIST persona-conditioned LLM daily schedules for the E1v2 LLM
workload arm (the INFOCOM-era cache kept only counts, so it was not reusable).

Each output JSON holds the model, persona, date and the full task list
[{name, category, room, start_s, duration_s}] so the replay is reproducible
without calling the LLM again.

Usage (LM Studio serving on :1234):
  OPENWEBUI_URL=http://localhost:1234/v1/chat/completions PYTHONPATH=. \
  python3 scripts/revision/e1v2_llm_schedules.py --model qwen2.5-7b-instruct \
      --out results/revision/llm_schedules
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from vesper.agents.llm_client import LLMClient, LLMConfig
from vesper.simulation.task_generator import TaskGenerator, HumanoidPersona

ROOMS = ["entryway", "hallway", "kitchen", "living_room", "bedroom"]

PERSONAS = [
    dict(name="Alex", age=30, occupation="Remote Worker", works_from_home=True),
    dict(name="Maria", age=42, occupation="Office Manager", works_from_home=False,
         work_start="08:30", work_end="17:30", wake_time="06:30", sleep_time="22:30"),
    dict(name="Joe", age=71, occupation="Retired Teacher", works_from_home=True, work_days=[],
         wake_time="06:00", sleep_time="21:30", exercise_frequency=0.7, social_frequency=0.5),
    dict(name="Priya", age=21, occupation="University Student", works_from_home=False,
         work_start="10:00", work_end="15:00", wake_time="08:30", sleep_time="01:00", tv_time=0.8),
    dict(name="Sam", age=35, occupation="Night-shift Nurse", works_from_home=False,
         work_start="19:00", work_end="07:00", wake_time="14:00", sleep_time="08:00"),
    dict(name="Lena", age=38, occupation="Parent working part-time", works_from_home=True,
         work_start="09:30", work_end="14:00", social_frequency=0.6),
    dict(name="Tom", age=28, occupation="Software Engineer (hybrid)", works_from_home=False,
         work_start="09:00", work_end="18:00", exercise_frequency=0.8),
    dict(name="Ana", age=55, occupation="Freelance Designer", works_from_home=True,
         wake_time="08:00", sleep_time="00:30", tv_time=0.4),
    dict(name="Raj", age=46, occupation="Delivery Driver", works_from_home=False,
         work_start="06:00", work_end="15:00", wake_time="05:00", sleep_time="21:00"),
    dict(name="Ella", age=64, occupation="Semi-retired Consultant", works_from_home=True,
         work_days=[0, 2], wake_time="07:00", sleep_time="22:00", social_frequency=0.4),
]


def schedule_json(model, persona, date, sched):
    tasks = []
    for t in sched.tasks:
        st = t.scheduled_time
        if st is None:
            continue
        tasks.append(dict(
            name=t.name,
            category=getattr(t.category, "name", str(t.category)),
            room=(t.location.room_name if t.location else ""),
            start_s=st.hour * 3600 + st.minute * 60 + st.second,
            duration_s=int(t.duration.total_seconds()) if t.duration else 900,
        ))
    return dict(model=model, persona=persona, date=str(date.date()),
                works_from_home=persona.get("works_from_home", True), tasks=tasks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default="results/revision/llm_schedules")
    ap.add_argument("--date", default="2026-10-14")  # a Wednesday
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = LLMClient(LLMConfig(model=a.model))
    date = datetime.fromisoformat(a.date)
    for i, p in enumerate(PERSONAS):
        f = out / f"{a.model.replace('/', '_')}_p{i:02d}_{date.date()}.json"
        if f.exists():
            continue
        t0 = time.time()
        try:
            gen = TaskGenerator(persona=HumanoidPersona(**p), llm_client=client, available_rooms=ROOMS)
            sched = gen.generate_daily_schedule(date=date)
            d = schedule_json(a.model, p, date, sched)
            f.write_text(json.dumps(d, indent=1))
            print(f"[{a.model}] persona {i} {p['name']}: {len(d['tasks'])} tasks ({time.time()-t0:.0f}s)",
                  flush=True)
        except Exception as e:  # recorded, not hidden
            (out / f"{f.stem}.FAILED.txt").write_text(f"{type(e).__name__}: {e}\n")
            print(f"[{a.model}] persona {i} FAILED: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
