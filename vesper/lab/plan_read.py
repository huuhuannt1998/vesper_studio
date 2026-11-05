"""Reading a floor-plan image into a `vesper-plan/1` with Claude (floor-plan import, task A3).

`read_plan_image(path)` checks that the file is a PNG, JPEG or PDF by its first bytes, copies it into a fresh temporary
directory as `plan.png|jpg|pdf` and runs the Claude Code CLI headless (`claude -p`) there with only the Read tool,
allowed for that one copy: the file's own name, folder and links never reach the prompt or the permissions. It parses
the plan out of the answer. A first answer that is not a valid plan is sent back once with the reasons; a second
invalid answer raises a PlanError carrying Claude's last text or stated reason, so a picture that is not a floor plan
fails with Claude's own words. The image goes to Anthropic through the user's Claude Code session; this module makes
no other network call, and `vesper lab import` never calls it for a `.json` plan."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from vesper.lab.plan import ROOM_TYPES, PlanError, door_name, load_plan

TIMEOUT_S = 900  # one reading of a large image can take several minutes
MAX_BYTES = 50 * 2**20  # no picture larger than this is sent
MAGIC = ((b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"%PDF", "pdf"))  # what the first bytes say a picture is

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
- The plan is one storey: if several floors are drawn, read only the floor that has the main entrance (floors may
  stand side by side in one image, or one per page) and leave the other floors out.
- Every room in the plan gets an entry: a unique short "id" (such as "r1"), the "name" as the plan labels it, and a
  "type" from the allowed list above (use "corridor" or "hall" for passages, "balcony" for an outdoor space of the
  home (a balcony, terrace, patio or loggia), "other" for a room that is none of the types, such as a closet or a
  storage room). "polygon" is the room's floor outline along the INSIDE of its walls, one [x, y] per corner, not
  repeating the first corner at the end. Walls are drawn thick: use the wall's inner face and keep two neighbouring
  rooms' shared wall at the same position. Rooms must not overlap.
- Every door: "at" is the middle of the doorway in the wall, "width" its opening in pixels, "rooms" the two rooms it
  joins, or a room and "outside" for a door to the outdoors (a front door, a patio door). Do not leave out a door that
  joins two rooms; the rooms must be joined so that every room can be reached from the entrance.
- Every window: "at" its middle in the wall, "width" in pixels, "room" the room it lights (a window between a room
  and a balcony lights the room). Leave "windows" empty if the plan shows none.
- "entrance" is the index (counting from 0) of the main door to the outdoors in your "doors" list. An entrance porch
  at the main door (a kuisti, a veranda, a mudroom open to the outside) is a "balcony", and the entrance is its door
  to the outside; give its door into the home too. If no outside door is drawn, do not refuse: add a door to
  "outside" at the most plausible front-door position (on an outside wall of the hall, living room or corridor,
  whichever a visitor would enter by) and make that the entrance.
- The scale: if the image prints dimension text for rooms or walls, or a scale bar, or the home's total floor area,
  put it into "scale": a scale bar or a measured length as {{"length_m": metres, "from": [x, y], "to": [x, y]}} (the two
  pixel points the length spans, along a straight line), a printed total area as {{"area_m2": number}}. Lengths are
  in METRES: convert feet and inches (1 ft = 0.3048 m). Prefer the longest dimension line, measuring between the
  points it spans. If the plan states none of these, answer "scale": null and do NOT guess one.

Answer with ONLY the JSON object {{"floors_drawn": N, "plan": <the floor plan>}}: N is how many floors (storeys)
the image draws (1 for most plans), "plan" the floor plan above, of the one floor read. No explanation before or
after it, no comments. If the image is not a floor plan (a photograph, a drawing of something else, a plan too
unclear to read), answer only {{"error": "<why>"}}.
"""


class ReadingStopped(PlanError):
    """The reading was stopped by the user (the lobby's Stop reading): Claude's process was killed."""


def _stoppable(args: list[str], cwd: str | None, timeout: float, stop) -> subprocess.CompletedProcess:
    """subprocess.run's answer, but the process is killed as soon as `stop` (a threading.Event) is set."""
    import time
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, cwd=cwd)
    end = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.2)
            return subprocess.CompletedProcess(args, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if stop.is_set() or time.monotonic() >= end:
                proc.kill()
                proc.communicate()
                if stop.is_set():
                    raise ReadingStopped("the reading was stopped: Claude's process was ended") from None
                raise


def _claude(args: list[str], cwd: str | None = None, timeout: float = TIMEOUT_S, stop=None) -> str:
    """The default runner: run the Claude Code CLI in `cwd` and return what it printed. With `stop` (a
    threading.Event), setting it kills the CLI at once (ReadingStopped)."""
    try:
        done = (subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                               cwd=cwd) if stop is None else _stoppable(args, cwd, timeout, stop))
    except FileNotFoundError:
        raise PlanError("Claude Code is not installed: reading a floor-plan image needs the `claude` command "
                        "(https://claude.com/claude-code) signed in to your account; or write the plan as a "
                        "vesper-plan/1 .json file") from None
    except subprocess.TimeoutExpired:
        raise PlanError(f"Claude did not answer within {timeout:g} s (raise it with --timeout); try again, or write "
                        "the plan as a vesper-plan/1 .json file") from None
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
    if not isinstance(env, dict) or not ("result" in env or "is_error" in env or env.get("type") == "result"):
        return out
    text = env.get("result")
    text = text if isinstance(text, str) else ""
    subtype = env.get("subtype") if isinstance(env.get("subtype"), str) else ""
    if env.get("is_error") or subtype.startswith("error"):
        kind = f" ({subtype})" if subtype else ""
        raise PlanError(f"the `claude` command reported an error{kind}: {text.strip() or 'no details'}")
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


MAX_FLOORS = 50  # a floors_drawn above this is no count of storeys


def _floors(value) -> int | None:
    """The number of floors an answer says the image draws, or None when it is no count."""
    ok = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_FLOORS
    return value if ok else None


def _check(text: str) -> tuple[dict | None, str, str | None, int | None]:
    """(the plan, "", None, floors drawn) for a valid answer, else (None, what is wrong with it, Claude's stated reason
    when it answered {"error": ...}, None). The answer is {"floors_drawn": N, "plan": plan} (the prompt's) or a bare
    plan (an older reading's); a floors_drawn that is no count is dropped, never the plan."""
    try:
        raw = _json_in(text)
    except ValueError:
        return None, "the answer holds no JSON object (answer with only the JSON)", None, None
    if isinstance(raw, dict) and "error" in raw and "rooms" not in raw and "plan" not in raw:
        return None, f"you said this is not a readable floor plan: {raw['error']}", str(raw["error"]), None
    floors = None
    if isinstance(raw, dict) and "plan" in raw and "rooms" not in raw:
        floors, raw = _floors(raw.get("floors_drawn")), raw["plan"]
    try:
        return load_plan(raw), "", None, floors
    except PlanError as e:
        return None, str(e), None, None


def _label(name: str) -> str:
    """A file name safe to print: control characters shown as ?."""
    return "".join(c if c.isprintable() else "?" for c in name)


def _picture_kind(path: Path) -> str:
    """png, jpg or pdf by the file's first bytes (a symlink is followed: what it points at is what is read)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise PlanError(f"{_label(path.name)}: larger than {MAX_BYTES // 2**20} MB")
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError as e:
        raise PlanError(f"{_label(path.name)}: cannot read it: {e.strerror}") from None
    for magic, ext in MAGIC:
        if head.startswith(magic):
            return ext
    raise PlanError(f"{_label(path.name)}: not a PNG, JPEG or PDF file")


def summary(plan_m: dict, numbers: list[int] | None = None, floors_drawn: int | None = None) -> str:
    """What was read, for the user to check against the image: each room's name, type and area, the doors, and the
    entrance. `plan_m` is a plan in metres (scaled); a plan still in pixels (no scale yet) is shown in pixels. Doors are
    named as `vesper.lab.plan.door_name` names them: from 1, in the plan as read (`numbers`: each door's place there,
    when doors were left out since). With `floors_drawn` above 1 (the reading's count), a line says which storey
    was read: the one with the main entrance."""
    from vesper.lab.geometry import polygon_area
    px = plan_m.get("units") == "px"
    area = (lambda a: f"{a:.0f} px²") if px else (lambda a: f"{a:.1f} m²")
    width = (lambda w: f"{w:.0f} px") if px else (lambda w: f"{w:.2f} m")
    rooms, doors = plan_m["rooms"], plan_m.get("doors", [])
    names = {r["id"]: r.get("name") or r["id"] for r in rooms}
    names["outside"] = "outside"
    head = f"Claude read {len(rooms)} room{'s' if len(rooms) != 1 else ''} and {len(doors)} door" \
           f"{'s' if len(doors) != 1 else ''}"
    lines = [head + ("; scale missing, so areas are in pixels:" if px else "; check them against your image:")]
    if floors_drawn is not None and floors_drawn > 1:
        lines.append(f"  storey: the floor with the main entrance (1 of {floors_drawn} drawn)")
    lines += [f"  {names[r['id']]} ({r['type']}): {area(polygon_area([tuple(p) for p in r['polygon']]))}"
              for r in rooms]
    lines += [f"  {door_name(k, numbers)}: {names[d['rooms'][0]]} - {names[d['rooms'][1]]} ({width(d['width'])})"
              for k, d in enumerate(doors)]
    ent = plan_m.get("entrance")
    if ent is None or not 0 <= ent < len(doors):
        lines.append("  entrance: none given")
    else:
        inside = [names[x] for x in doors[ent]["rooms"] if x != "outside"]
        lines.append(f"  entrance: {door_name(ent, numbers)} between {' and '.join(inside + ['outside'])}")
    return "\n".join(lines)


def read_plan_image(path: Path, runner=None, model: str | None = None, timeout: float = TIMEOUT_S,
                    stop=None, meta: dict | None = None, context: str | None = None) -> dict:
    """A validated `vesper-plan/1` (in pixels) read from the floor-plan image or PDF at `path`.

    The file must be a PNG, JPEG or PDF by its first bytes. A copy of it goes into a fresh temporary directory as
    `plan.png|jpg|pdf`; Claude runs there, with only the Read tool, allowed for that copy alone, and no MCP servers,
    and the temporary directory is deleted afterwards. `model` is passed as `--model` (an alias such as "sonnet" or
    "opus", or a full model name); None, the default, passes none, so the user's own Claude Code model reads the plan
    (the most exact; "sonnet" is faster and cheaper but read doors less exactly in the one comparison made).
    `runner(args, cwd=..., timeout=...) -> str` runs the `claude` argument list and returns its stdout; the default
    runs the CLI. Raises a PlanError when the file is no picture, the CLI is missing, fails or reports an error, or
    when two answers in a row are not a valid plan (the message carries Claude's last text or stated reason).
    `stop` (a threading.Event, the lobby's Stop reading): once set, no further answer is asked for and the default
    runner kills the CLI at once; ReadingStopped is raised. `meta` (a dict, when given) gets "floors_drawn": how many
    floors Claude says the image draws (None when the answer gave no count); the plan returned is one floor.
    `context` (a short text, when given) is added to the prompt after the instructions, saying what kind of picture
    this is (the top-down render of a 3D scan, scan_render.py); the instructions themselves are unchanged."""
    path = Path(path)
    if not path.is_file():
        raise PlanError(f"{_label(str(path))}: no such file")
    ext = _picture_kind(path)
    run = runner or (_claude if stop is None else lambda args, cwd=None, timeout=TIMEOUT_S: _claude(
        args, cwd=cwd, timeout=timeout, stop=stop))
    tmp = Path(tempfile.mkdtemp(prefix="vesper-plan-")).resolve()
    try:
        copy = tmp / f"plan.{ext}"
        shutil.copyfile(path, copy)
        # Only the Read tool exists for this run, and it may read only the copy (`//` marks an absolute path).
        base = ["--tools", "Read", "--allowedTools", f"Read(/{copy})", "--strict-mcp-config",
                "--setting-sources", "",  # no user or project settings, hooks or permission rules
                "--output-format", "json", *(["--model", model] if model else [])]
        text, problem, said = "", "", None
        for attempt in range(2):
            if stop is not None and stop.is_set():
                raise ReadingStopped("the reading was stopped")
            about = f"\nAbout this image: {context.strip()}\n" if context else ""
            prompt = f"{PROMPT}{about}\nThe image: {copy}\n"
            if attempt:
                prompt += (f"\nYour previous answer was:\n{text}\n\nVESPER cannot use it: {problem}\n"
                           "(\"door N\" is the N-th entry of \"doors\", counting from 1.)\n\n"
                           "Look at the image again and answer with the corrected JSON only.\n")
            text = _answer_text(run(["claude", "-p", prompt, *base], cwd=str(tmp), timeout=timeout))
            plan, problem, said, floors = _check(text)
            if plan is not None:
                if meta is not None:
                    meta["floors_drawn"] = floors
                return plan
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if said is not None:
        raise PlanError(f"Claude could not read a floor plan in {_label(path.name)}: {said}")
    shown = text.strip() if len(text.strip()) <= 2000 else text.strip()[:2000] + " ..."
    raise PlanError(f"could not read a floor plan from {_label(path.name)}: {problem}. Claude's last answer: {shown}")
