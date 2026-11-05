# The play view's environment: lights you can see, a yard, sensors at work, the place and time on screen (design, 2026-11-05)

## Requirements

The PI's words, from playing a generated 3-room home sited in Charlotte, NC:

> 1. I cannot tell if the lights are on or off
> 2. I cannot see outside view. Let's create some yard view to check for the sun, shadow. Also the texture of the
>    furnitures.
> 3. I installed a illuminal light but I cannot see how it works
> 4. In this screenshot, I do not see the enviroment context or location.
>
> For example, the sun will make the room darker or brighter, so the lights can adjust the illumium of the light

The PI's choice of time control: **a time-of-day preview, for the view only.** Devices, Home Assistant and automations
keep running in real time.

## What was wrong

These are the causes found in the code.

1. **Lights.** A light's glow sits on its ceiling fixture's diffuser, which is out of view at eye level. Its point
   light (intensity 4.5) is small against the hemisphere fill, so on and off look alike.
2. **No outdoors.** The scene's background is a flat sky colour, and nothing outside the walls exists: no ground, no
   yard, nothing for the sun to shadow. The tan panels in the windows are roller shades a resident closed; that is
   correct, but nothing says so.
3. **Sensor readings.** An illuminance (or temperature, or humidity) sensor's reading appears only in the side Room
   panel. Nothing in the 3D view shows the value or how it changes.
4. **Place and time.** The site, date, time, sun and weather appear only as one muted line in the Room panel.

## Design

### 1. Lights you can see

- A light that is on has three visible marks:
  - a soft glow sprite under its fixture, visible at eye level;
  - a warm pool of light on the floor beneath it: a decal or spotlight, not a full shadow-casting light, to keep
    frame time;
  - a stronger warm point light, scaled by brightness.
- A light that is off shows none of these.
- The room tour bar's room buttons show a small bulb when any light in that room is on.
- The prompt keeps saying "switch … on/off".
- A dimmed light (brightness < 254) shows a proportionally weaker glow and pool, so dimming is visible.

### 2. A yard and real furniture textures

**The yard.**
- A lawn plane around the house, at least 12 m beyond the home's bounds on each side, with a canvas-painted grass
  texture.
- A low fence at the yard's edge, a path from the entrance (porch) to the fence, and 4–8 trees and a few shrubs:
  simple procedural meshes, a trunk and a canopy, seeded by the home's seed so the yard is stable.
- No new asset downloads are required. A CC0 tree from the existing Poly Haven setup may be used if one is already
  installed.

**The sky.**
- A gradient sky (zenith to horizon) from the sun's elevation (`skyColor` today).
- A visible sun disc in the sun's direction when it is up.

**Shadows.** The sun (a DirectionalLight) casts shadows from:
- the walls;
- the trees;
- the window frames, through the glass onto the floor.

The shadow camera covers the yard as well as the home. Trees and fence cast; the lawn and floors receive.

**Shades.**
- A closed shade shows a hint on hover or focus: "Shade down: click to raise". Clicking a window already toggles it.
- The tour bar does not list shades.

**Furniture textures.**
- Every furniture kind with a Poly Haven CC0 model (`vesper setup assets`, 14 kinds) uses that model with its PBR
  textures.
- Kinds without one, such as fridge, sink, counter, TV stand, washer, toilet and bath, get painted canvas textures
  (wood grain, fabric weave, brushed steel, ceramic) chosen per kind, in place of flat colours.
- No downloads at play time.

### 3. Sensors at work

**Live badges.**
- Every illuminance, temperature and humidity sensor shows a small badge above it in the 3D view: "☀ 312 lx",
  "21.4 °C", "45 %".
- The badges update with the environment ticks.
- They are on by default, with a toggle beside "Device names in the view".

**The device dialog.** An illuminance sensor's dialog shows:
- a sparkline of the session's readings so far;
- the thresholds the home's automations compare it with, read from the home's automations: "dark below 50 lx".

**Daylight-following lights**, the PI's example.
- A generated home may include, per lit room that has an illuminance sensor, one automation: when the room's
  illuminance changes and someone is home, set the room's lights' brightness to make up the gap to a target. The
  default target is 300 lx.
- The rule: brightness = clamp((target − daylight) / lamp_lux_at_full, 0, 1) × 254, off below 5 %.
- It is a normal Home Assistant automation, written to the home's automations, so it runs live in Home Assistant and
  is editable there.
- Compose shows it as a checkbox, "Lights follow daylight", on by default for generated homes.
- Existing homes are unchanged.

### 4. Place and time on screen, with the time-of-day preview

**The panel.** An always-visible panel over the 3D view, top right, compact, shows:
- the place (city and coordinates on hover), the date, and the local time;
- the sun's elevation and its compass direction on a small dial;
- outdoor temperature and cloud cover;
- this room's illuminance, temperature and humidity.

It is hidden for a home with no site, where it says "No place: indoor light and warmth follow a fixed day".

**The preview.**
- A "Preview the day" slider (00:00–24:00, 15-minute steps) with a Live button. Moving it previews that time of the
  home's date:
  - the sun and sky, and the shadows on the lawn and through the windows;
  - each room's daylight, from the indoor model's own `daylight()` at that time, with the shades as they are now;
  - each illuminance sensor's badge, showing the previewed daylight lux, marked "preview";
  - each daylight-following light's brightness, showing what its rule would set at that daylight, marked "preview".
- While previewing, a clear band says "Previewing 15:30. Devices and Home Assistant stay live at 07:42." **Live**
  returns to the session's time.
- Nothing is sent to devices or Home Assistant while previewing.

**Server.** A new read-only endpoint on the play server, `GET /api/daylight?step=900`, returns for the home's date one
row per step. Each row has:
- the time, sun azimuth and elevation;
- outdoor illuminance and the direct fraction;
- each room's daylight lux (with the current shades);
- outdoor temperature and cloud cover.

It is computed from the cached weather and the same `vesper.env` code the session uses; no network. A home without a
site returns 404.

## Non-goals

- Changing the session's own clock speed. The PI chose the preview.
- Occupancy-driven behaviour beyond the existing residents.
- New asset downloads.
- Changes to EDT or the paper.

## Acceptance

- In the 3D view, with lights on and off, a person can tell each light's state at eye level from the doorway of its
  room. The test is a screenshot pair checked by eye, plus a unit test of the light's visual state.
- Looking out a window with its shade up shows lawn, trees and fence. At a sunny previewed hour, shadows of trees and
  window frames are visible on the lawn and floor.
- An illuminance sensor's badge shows a number that changes as the preview slider moves from 06:00 to 13:00.
- With "Lights follow daylight", previewing noon dims the room's lights and previewing 18:00 brightens them.
- The place and time panel shows "Charlotte, NC" and the session's local time on the PI's home.
- The existing UI and play tests pass. New behaviour has tests: the endpoint, the automation template and the rule's
  arithmetic in Python; the badges, panel formatting and preview state as pure functions tested in Node.
- Frame rate stays usable on this Mac: the yard and shadows add no more than one shadow-casting light.
