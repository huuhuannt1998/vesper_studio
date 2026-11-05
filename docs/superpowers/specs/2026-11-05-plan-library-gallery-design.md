# Choosing a floor plan from a picture gallery (design, 2026-11-05)

The PI's words:

> Do we have 2D layout house database?
> Let's do this. When we select the layout, can we have the icon view so it's easier for users

"This" is the full CubiCasa5K, about 5,000 real floor plans (Zenodo record 2613548, licensed CC BY-NC-SA 4.0). It is
downloaded to a local folder, never committed. The gallery lets a user pick a layout by its picture.

## What the user does

1. In compose, under **Rooms**, choose **Import a plan**. Beside the existing "drop an image" control there is
   **Choose from the plan library**.
2. A gallery opens over the page.
   - **The grid:** thumbnails of the plans, about 24 at a time, loading more on scroll. Each card shows the plan
     image, "5 rooms · 82 m²", and a "2 floors" badge where there are two.
   - **Filters:** rooms (any, 1–2, 3–4, 5–6, 7+), floors (one, any), and a search by plan id.
   - **The note:** the library's licence and that it stays on this computer.
3. Clicking a card opens a larger preview, with the plan's room outlines and names drawn over the image from its
   ground truth. Two buttons:
   - **Use this plan**, the default: the home is built from the plan's own drawn ground truth, through
     `vesper.validation.plans.parse_cubicasa_svg` → `vesper-plan/1` (its scale from the dimension labels, or 1 cm
     a unit, said in the preview), then the existing rectify → check → generate path. It is instant and needs no
     Claude.
   - **Read the image with Claude instead**: the existing image import, for comparison.
4. The import's existing review, with the problems listed and rooms left out or put back, then Generate, work as for
   any plan.

A plan that draws two floors is built from its entrance floor, as the image import already does. Say so in the
preview. If the ground truth cannot build a house (the existing checks refuse it), the preview says why and offers the
Claude reading.

## Where the library lives

- **Configuration:** `plan_library` in VESPER's configuration (`vesper.setup_tools`) is the folder holding
  `cubicasa5k/`, or the `cubicasa5k/` folder itself.
- **On the PI's Mac:** a folder on an external SSD, named only in the local configuration.
- **The command:** `vesper setup plan-library [--path DIR]` downloads it there, from the same Zenodo source
  `vesper.validation.plans` uses, resumable, and records the path. If the library is absent, the gallery says how to
  get it.

## Speed

- **The index.** The first time the gallery opens, VESPER indexes the library in the background, with progress shown
  in the gallery. Per plan it records: id, subset (colorful, high_quality, high_quality_architectural), image size,
  room count, floors, total area in m² (from the ground truth and its scale), and whether the ground truth parses.
  - The index is cached, keyed by the library's path and file count, in VESPER's cache folder: the same place as
    other caches.
  - Rebuilt only when the library changes.
- **Thumbnails.** Generated on demand at about 320 px wide and cached on disk, so the grid stays fast. Use Pillow if
  it is installed; otherwise serve the original image and let the browser scale it, with lazy loading. Prefer adding
  Pillow to an optional extra, not a main dependency.

## Server

These go on the compose and lobby server, `vesper.lab.compose` / `studio`, loopback only, like the rest:

- `GET /api/plan-library?offset=&limit=&rooms=&floors=&q=` returns `{state: indexing|ready|absent, progress,
  total, items: [...]}`.
- `GET /api/plan-library/thumb/<id>` returns a cached thumbnail.
- `GET /api/plan-library/plan/<id>` returns the ground-truth `vesper-plan/1` and scale, for the preview overlay.
- `POST /api/plan-library/use {id}` sets compose's import to that plan, the same state as an imported and reviewed
  plan.

Ids are validated against the index, so no path from the request reaches the file system.

## Non-goals

- Shipping any plan in the repo.
- Editing plans in the gallery (the existing review does that).
- Other datasets: the code stays general enough for another folder of `F1_scaled.png` + `model.svg` plans, but only
  CubiCasa5K is wired.

## Acceptance

- With the library present, the gallery opens and shows thumbnails within a few seconds; the first index runs in the
  background with progress.
- Filtering by "3–4 rooms, one floor" shows only such plans.
- "Use this plan" builds a home from a CubiCasa ground truth with no Claude call, and `vesper lab layout` reports it.
- Tests:
  - Python: index, filters, id validation, the use endpoint on two fixture plans (hand-made SVG and PNG, not dataset
    files).
  - Node: the gallery's pure formatting and filter logic.
  - The existing compose and UI tests stay green.
- A screenshot check of the gallery and the preview.
