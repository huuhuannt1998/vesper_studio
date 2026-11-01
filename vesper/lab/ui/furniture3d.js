// The furniture of the 3D view (PI 2026-11-01): each piece the furnisher places (vesper/lab/furnishing.py), built
// from three.js primitives at the size the walking map gives it (no asset files, no licences, any computer). A
// model's origin is the middle of its footprint on the floor; w runs along x, d along z, and its front faces +z,
// toward the room (the view turns it by the piece's facing). THREE is passed in, so node tests can build it.

export const KINDS = ["sofa", "coffee_table", "rug", "tv_stand", "armchair", "bookshelf", "plant", "counter", "fridge",
  "dining_table", "chair", "bed", "nightstand", "wardrobe", "bath", "vanity", "toilet", "desk", "office_chair",
  "sideboard", "washer", "dryer", "cabinet", "console"];

const OAK = 0xb48a5a, WALNUT = 0x6e4b33, WHITE = 0xf4f3ef, CERAMIC = 0xfbfbfa, STEEL = 0xc9ccce, DARK = 0x2a2e33;
const FABRIC = 0x7d8b8f, LINEN = 0xe8e2d6, CUSHION = 0x96a3a6, GREEN = 0x4f7a4a, STONE = 0x4a4d50, BLUE = 0x5d7389;

export function buildFurniture(T, item) {
  const { kind, w, d, h } = item;
  const g = new T.Group();
  g.name = kind;
  const mat = (color, extra = {}) => new T.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0, ...extra });
  const box = (bw, bh, bd, x, y, z, color, name, extra) => { // a box by its size and the centre of its base
    const m = new T.Mesh(new T.BoxGeometry(bw, bh, bd), typeof color === "object" ? color : mat(color, extra));
    m.position.set(x, y + bh / 2, z);
    if (name) m.name = name;
    g.add(m);
    return m;
  };
  const cyl = (r, ch, x, y, z, color, name, extra) => {
    const m = new T.Mesh(new T.CylinderGeometry(r, r, ch, 20), mat(color, extra));
    m.position.set(x, y + ch / 2, z);
    if (name) m.name = name;
    g.add(m);
    return m;
  };
  const legs = (lw, ld, lh, inset, color, r = 0.025) => {
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) cyl(r, lh, sx * (lw / 2 - inset), 0, sz * (ld / 2 - inset), color);
  };
  const front = d / 2; // the front face's z

  switch (kind) {
    case "sofa": case "armchair": {
      const seat = 0.42, armW = Math.min(0.18, w * 0.15);
      box(w, 0.1, d, 0, 0, 0, DARK, "plinth");
      box(w - 2 * armW, seat - 0.1, d - 0.22, 0, 0.1, 0.11, FABRIC, "base");
      box(w, h - 0.1, 0.22, 0, 0.1, -d / 2 + 0.11, FABRIC, "back");
      for (const s of [-1, 1]) box(armW, 0.62 - 0.1, d, s * (w / 2 - armW / 2), 0.1, 0, FABRIC, "arm");
      const n = kind === "armchair" ? 1 : Math.max(2, Math.round((w - 2 * armW) / 0.7));
      const cw = (w - 2 * armW) / n;
      for (let k = 0; k < n; k++) box(cw - 0.03, 0.12, d - 0.26, -w / 2 + armW + cw * (k + 0.5), seat, 0.12, CUSHION, "cushion");
      break;
    }
    case "coffee_table": case "dining_table": case "desk": {
      const top = kind === "coffee_table" ? 0.04 : 0.035;
      box(w, top, d, 0, h - top, 0, kind === "desk" ? WHITE : OAK, "top");
      legs(w, d, h - top, 0.06, kind === "desk" ? DARK : WALNUT, kind === "coffee_table" ? 0.025 : 0.03);
      if (kind === "desk") { // a monitor and a keyboard
        box(Math.min(0.6, w * 0.45), 0.36, 0.03, 0, h + 0.12, -d / 2 + 0.15, DARK, "monitor");
        box(0.06, 0.12, 0.06, 0, h, -d / 2 + 0.16, DARK);
        box(0.42, 0.02, 0.14, 0, h, 0.05, 0x3b3f44, "keyboard");
      }
      break;
    }
    case "rug": {
      box(w, 0.01, d, 0, 0, 0, 0xb9a88f, "rug", { roughness: 1 });
      box(w - 0.16, 0.012, d - 0.16, 0, 0, 0, 0xcbbca4, null, { roughness: 1 });
      break;
    }
    case "tv_stand": case "sideboard": case "console": case "cabinet": case "nightstand": {
      const body = kind === "console" ? 0.06 : h;
      if (kind === "console") { box(w, 0.04, d, 0, h - 0.04, 0, OAK, "top"); legs(w, d, h - 0.04, 0.04, WALNUT, 0.02); }
      else {
        box(w, body - 0.05, d, 0, 0.05, 0, kind === "cabinet" ? WHITE : kind === "tv_stand" ? WALNUT : OAK, "body");
        box(w - 0.04, 0.05, d - 0.04, 0, 0, 0, DARK, "plinth");
        const doors = Math.max(1, Math.round(w / 0.45));
        for (let k = 1; k < doors; k++) box(0.006, body - 0.12, 0.004, -w / 2 + (w * k) / doors, 0.08, front, DARK);
      }
      if (kind === "tv_stand") { // the TV on it
        const tw = Math.min(1.4, w - 0.1);
        box(0.3, 0.02, 0.2, 0, h, -0.05, DARK);
        box(0.05, 0.08, 0.05, 0, h + 0.02, -0.05, DARK);
        box(tw, tw * 0.57, 0.04, 0, h + 0.1, -0.05, 0x111418, "tv", { roughness: 0.25, metalness: 0.3 });
      }
      if (kind === "nightstand") { // a lamp
        cyl(0.05, 0.22, 0, h, -0.05, WALNUT);
        const shade = new T.Mesh(new T.CylinderGeometry(0.08, 0.11, 0.14, 20, 1, true),
          mat(LINEN, { side: T.DoubleSide, emissive: 0xfff1d6, emissiveIntensity: 0.25 }));
        shade.position.set(0, h + 0.29, -0.05);
        g.add(shade);
      }
      break;
    }
    case "bookshelf": case "wardrobe": {
      const t = 0.025, wood = kind === "wardrobe" ? WHITE : OAK;
      box(t, h, d, -w / 2 + t / 2, 0, 0, wood);
      box(t, h, d, w / 2 - t / 2, 0, 0, wood);
      box(w, t, d, 0, h - t, 0, wood, "top");
      box(w, 0.06, d, 0, 0, 0, wood);
      box(w, h, 0.01, 0, 0, -d / 2 + 0.005, wood, "back");
      if (kind === "wardrobe") {
        for (const s of [-1, 1]) {
          box(w / 2 - 0.01, h - 0.08, 0.02, s * (w / 4), 0.06, d / 2 - 0.01, WHITE, "door");
          box(0.015, 0.3, 0.012, s * 0.04, h * 0.45, d / 2 + 0.003, STEEL);
        }
      } else {
        const shelves = Math.max(3, Math.round(h / 0.38)), colors = [0x8c3b2e, 0x2f5d7c, 0xd9b44a, 0x55704d, 0x6c4b7a];
        let seed = 3;
        const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
        for (let k = 1; k < shelves; k++) box(w - 2 * t, t, d - 0.02, 0, (h * k) / shelves, 0.01, wood);
        for (let k = 0; k < shelves; k++) { // books on each shelf
          let x = -w / 2 + t + 0.02;
          const y = (h * k) / shelves + (k ? t : 0.06), room = h / shelves - 0.06;
          while (x < w / 2 - t - 0.06 && rnd() < 0.93) {
            const bw = 0.025 + rnd() * 0.03, bh = room * (0.6 + rnd() * 0.3);
            box(bw, bh, d * 0.7, x + bw / 2, y, 0.02, colors[Math.floor(rnd() * colors.length)]);
            x += bw + 0.004;
          }
        }
      }
      break;
    }
    case "plant": {
      const r = Math.min(w, d) / 2;
      const pot = new T.Mesh(new T.CylinderGeometry(r * 0.75, r * 0.6, h * 0.3, 20), mat(0xc8b9a6));
      pot.position.set(0, h * 0.15, 0);
      g.add(pot);
      for (const [x, y, z, s] of [[0, 0.55, 0, 0.55], [0.3, 0.66, 0.1, 0.4], [-0.25, 0.7, -0.1, 0.42], [0.05, 0.78, 0.2, 0.35]]) {
        const leaf = new T.Mesh(new T.SphereGeometry(r * s * 1.6, 14, 10), mat(GREEN, { roughness: 0.85 }));
        leaf.position.set(x * r, h * y, z * r);
        leaf.scale.y = 1.15;
        g.add(leaf);
      }
      break;
    }
    case "counter": {
      box(w, 0.08, d - 0.05, 0, 0, -0.025, DARK, "plinth");
      box(w, h - 0.12, d - 0.02, 0, 0.08, -0.01, WHITE, "cabinets");
      const doors = Math.max(1, Math.round(w / 0.6));
      for (let k = 1; k < doors; k++) box(0.006, h - 0.16, 0.004, -w / 2 + (w * k) / doors, 0.1, front - 0.01, 0xbfbfbb);
      box(w, 0.04, d, 0, h - 0.04, 0, STONE, "worktop", { roughness: 0.35 });
      box(0.5, 0.012, 0.4, -w * 0.22, h, 0.03, STEEL, "sink", { metalness: 0.6, roughness: 0.3 });
      box(0.58, 0.012, 0.5, w * 0.24, h, 0.02, 0x15171a, "hob", { roughness: 0.2 });
      cyl(0.012, 0.25, -w * 0.22, h, -d / 2 + 0.06, STEEL, "tap", { metalness: 0.7, roughness: 0.3 });
      box(w, 0.6, 0.01, 0, h, -d / 2 + 0.005, 0xe6e3dc, "splashback", { roughness: 0.4 });
      box(w, 0.7, 0.35, 0, h + 0.55, -d / 2 + 0.175, WHITE, "wall_cabinets");
      break;
    }
    case "fridge": {
      box(w, h, d - 0.04, 0, 0, -0.02, STEEL, "body", { metalness: 0.5, roughness: 0.35 });
      box(w - 0.01, 0.006, 0.01, 0, h * 0.62, d / 2 - 0.035, DARK);
      for (const y of [h * 0.66, h * 0.2]) box(0.02, 0.35, 0.03, w / 2 - 0.08, y, d / 2 - 0.02, DARK, "handle");
      break;
    }
    case "chair": {
      const seat = 0.46;
      box(w, 0.04, d * 0.85, 0, seat - 0.04, 0.03, OAK, "seat");
      legs(w, d * 0.85, seat - 0.04, 0.04, WALNUT, 0.018);
      box(w, h - seat, 0.04, 0, seat, -d / 2 + 0.05, OAK, "back");
      break;
    }
    case "office_chair": {
      const seat = 0.48;
      for (let k = 0; k < 5; k++) { // the star base
        const leg = new T.Mesh(new T.BoxGeometry(0.03, 0.03, w / 2 - 0.02), mat(DARK));
        const a = (k / 5) * Math.PI * 2;
        leg.position.set(Math.sin(a) * (w / 4), 0.015, Math.cos(a) * (w / 4));
        leg.rotation.y = a;
        g.add(leg);
      }
      cyl(0.025, seat - 0.12, 0, 0.06, 0, STEEL);
      box(w * 0.8, 0.08, d * 0.8, 0, seat - 0.08, 0.02, 0x30363c, "seat");
      box(w * 0.75, h - seat - 0.05, 0.06, 0, seat + 0.05, -d / 2 + 0.08, 0x30363c, "back");
      break;
    }
    case "bed": {
      box(w, 0.28, d, 0, 0, 0, OAK, "frame");
      box(w - 0.06, 0.2, d - 0.1, 0, 0.28, 0.03, CERAMIC, "mattress", { roughness: 0.9 });
      box(w - 0.02, 0.07, d * 0.62, 0, 0.47, d * 0.18, BLUE, "duvet", { roughness: 0.95 });
      for (const s of (w > 1.1 ? [-1, 1] : [0])) box(w > 1.1 ? w / 2 - 0.12 : w - 0.2, 0.11, 0.38, s * (w / 4), 0.48,
        -d / 2 + 0.3, LINEN, "pillow", { roughness: 0.95 });
      box(w, 1.0, 0.08, 0, 0, -d / 2 + 0.04, WALNUT, "headboard");
      break;
    }
    case "bath": {
      box(w, h, d, 0, 0, 0, CERAMIC, "body", { roughness: 0.3 });
      box(w - 0.14, 0.012, d - 0.14, 0, h, 0, 0xdfe7ea, "water", { roughness: 0.1 });
      cyl(0.015, 0.18, -w / 2 + 0.12, h, -d / 2 + 0.06, STEEL, "tap", { metalness: 0.7, roughness: 0.3 });
      break;
    }
    case "vanity": {
      box(w, h - 0.12, d, 0, 0, 0, OAK, "cabinet");
      box(w, 0.12, d, 0, h - 0.12, 0, CERAMIC, "basin", { roughness: 0.3 });
      cyl(0.015, 0.18, 0, h, -d / 2 + 0.07, STEEL, "tap", { metalness: 0.7, roughness: 0.3 });
      box(w * 0.85, 0.7, 0.02, 0, h + 0.35, -d / 2 + 0.01, 0xcfe0e6, "mirror", { roughness: 0.05, metalness: 0.6 });
      break;
    }
    case "toilet": {
      box(w * 0.55, 0.4, d * 0.55, 0, 0, 0.08, CERAMIC, "base", { roughness: 0.3 });
      box(w, 0.06, d * 0.7, 0, 0.4, 0.06, CERAMIC, "seat", { roughness: 0.3 });
      box(w, h - 0.4, 0.2, 0, 0.4, -d / 2 + 0.1, CERAMIC, "tank", { roughness: 0.3 });
      break;
    }
    case "washer": case "dryer": {
      box(w, h, d, 0, 0, 0, WHITE, "body", { roughness: 0.4 });
      const door = new T.Mesh(new T.CylinderGeometry(0.17, 0.17, 0.03, 32), mat(kind === "washer" ? 0x9fb3c1 : DARK,
        { roughness: 0.2, metalness: 0.2 }));
      door.rotation.x = Math.PI / 2;
      door.position.set(0, h * 0.45, d / 2);
      door.name = "door";
      g.add(door);
      box(w - 0.04, 0.1, 0.01, 0, h - 0.13, d / 2, 0xdedcd6, "panel");
      break;
    }
    default:
      box(w, h, d, 0, 0, 0, 0xb7b0a3, "piece");
  }
  return g;
}
