"""Reading a floor-plan image into a `vesper-plan/1` with Claude (floor-plan import, task A3).

`read_plan_image(path)` runs the Claude Code CLI headless (`claude -p`) on the image (or PDF), with the Read tool
allowed for that one file, and parses the plan out of its answer. A first answer that is not a valid plan is sent back
once with the reasons; a second invalid answer raises a PlanError carrying Claude's last text, so a picture that is not
a floor plan fails with Claude's own words. The image goes to Anthropic through the user's Claude Code session; this
module makes no other network call, and `vesper lab import` never calls it for a `.json` plan."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from vesper.lab.plan import ROOM_TYPES, PlanError, load_plan

TIMEOUT_S = 600  # one reading of a large image can take a few minutes

PROMPT = f"""You are reading a floor-plan image for VESPER, a smart-home simulator. The image is the file given below
(open it with the Read tool). Turn it into a floor plan in the `vesper-plan/1` format, in the image's own pixels.

The format (these keys exactly, no others):

{{"format": "vesper-plan/1",
 "units": "px",
 "scale": {{"m_per_px": float}} | {{"length_m": float, "from": [x, y], "to": [x, y]}} | {{"area_m2": float}} | null,
 "rooms": [{{"id": str, "name": str, "type": one of {', '.join(ROOM_TYPES)},
            "polygon": [[x, y], ...]}}],
 "doors": [{{"at": [x, y], "width": float, "rooms": [room id, room id | "outside"]}}],
 "windows": [{{"at": [x, y], "width": float, "room": room id}}],
 "entrance": index into "doors"}}

How to fill it:
- Coordinates are image pixels, "units": "px". The image's y grows downward (the origin is the top-left corner);
  x grows to the right. Give every point as [x, y] numbers.
- Every room in the plan gets an entry: a unique short "id" (such as "r1"), the "name" as the plan labels it, and a
  "type" from the allowed list above (use "corridor" or "hall" for passages, "other" for a room that is none of the
  types, such as a closet or a balcony). "polygon" is the room's floor outline along the INSIDE of its walls, one
  [x, y] per corner, not repeating the first corner at the end. Walls are drawn thick: use the wall's inner face and
  keep two neighbouring rooms' shared wall at the same position. Rooms must not overlap.
- Every door: "at" is the middle of the doorway in the wall, "width" its opening in pixels, "rooms" the two rooms it
  joins, or a room and "outside" for a door to the outdoors (a front door, a patio door). Do not leave out a door that
  joins two rooms; the rooms must be joined so that every room can be reached from the entrance.
- Every window: "at" its middle in the wall, "width" in pixels, "room" the room it lights. Leave "windows" empty if
  the plan shows none.
- "entrance" is the index (counting from 0) of the main door to the outdoors in your "doors" list.
- The scale: if the image prints dimension text for rooms or walls, or a scale bar, or the home's total floor area,
  put it into "scale": a scale bar or a measured length as {{"length_m": metres, "from": [x, y], "to": [x, y]}} (the two
  pixel points the length spans, along a straight line), a printed total area as {{"area_m2": number}}. If the plan
  states none of these, answer "scale": null and do NOT guess one.

Answer with ONLY the JSON object: no explanation before or after it, no comments. If the image is not a floor plan
(a photograph, a drawing of something else, a plan too unclear to read), answer only {{"error": "<why>"}}.
"""


def _claude(args: list[str]) -> str:
    """The default runner: run the Claude Code CLI and return what it printed."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise PlanError("Claude Code is not installed: reading a floor-plan image needs the `claude` command "
                        "(https://claude.com/claude-code) signed in to your account; or write the plan as a "
                        "vesper-plan/1 .json file") from None
    except subprocess.TimeoutExpired:
        raise PlanError(f"Claude did not answer within {TIMEOUT_S // 60} minutes; try again, or write the plan as a "
                        "vesper-plan/1 .json file") from None
    if done.returncode != 0 and not done.stdout.strip():
        detail = done.stderr.strip() or f"exit code {done.returncode}"
        raise PlanError(f"the `claude` command failed (is it signed in? run `claude` once to log in): {detail}")
    return done.stdout


def _answer_text(out: str) -> str:
    """The model's answer in the CLI's JSON envelope (`result`); output that is not an envelope is the answer."""
    try:
        env = json.loads(out)
    except ValueError:
        return out
    if not isinstance(env, dict) or "result" not in env and "is_error" not in env:
        return out
    text = env.get("result")
    text = text if isinstance(text, str) else ""
    if env.get("is_error"):
        raise PlanError(f"the `claude` command reported an error: {text.strip() or 'no details'}")
    return text


def _json_in(text: str) -> object:
    """The JSON object in an answer: all of it, a ```json fence, or from the first `{` to the last `}`."""
    for candidate in (text.strip(), *re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        try:
            return json.loads(text[first:last + 1])
        except ValueError:
            pass
    raise ValueError("the answer holds no JSON object")


def _check(text: str) -> tuple[dict | None, str]:
    """(the plan, "") for a valid answer, else (None, what is wrong with it)."""
    try:
        raw = _json_in(text)
    except ValueError:
        return None, "the answer holds no JSON object (answer with only the JSON)"
    if isinstance(raw, dict) and "error" in raw and "rooms" not in raw:
        return None, f"you said this is not a readable floor plan: {raw['error']}"
    try:
        return load_plan(raw), ""
    except PlanError as e:
        return None, str(e)


def read_plan_image(path: Path, runner=None) -> dict:
    """A validated `vesper-plan/1` (in pixels) read from the floor-plan image or PDF at `path`.

    `runner(args) -> str` runs the `claude` argument list and returns its stdout; the default runs the CLI. Raises a
    PlanError when the CLI is missing, fails or reports an error, or when two answers in a row are not a valid plan
    (the message carries Claude's last text)."""
    path = Path(path)
    if not path.is_file():
        raise PlanError(f"{path}: no such file")
    run = runner or _claude
    full = path.resolve()
    prompt = f"{PROMPT}\nThe image: {full}\n"
    # Only the Read tool exists for this run, and it may read only this file (`//` marks an absolute path).
    base = ["--tools", "Read", "--allowedTools", f"Read(/{full})", "--add-dir", str(full.parent),
            "--output-format", "json"]
    text, problem = "", ""
    for attempt in range(2):
        if attempt:
            prompt = (f"{PROMPT}\nThe image: {full}\n\nYour previous answer was:\n{text}\n\nVESPER cannot use it: "
                      f"{problem}\n\nLook at the image again and answer with the corrected JSON only.\n")
        text = _answer_text(run(["claude", "-p", prompt, *base]))
        plan, problem = _check(text)
        if plan is not None:
            return plan
    shown = text.strip() if len(text.strip()) <= 2000 else text.strip()[:2000] + " ..."
    raise PlanError(f"could not read a floor plan from {path.name}: {problem}. Claude's last answer: {shown}")
