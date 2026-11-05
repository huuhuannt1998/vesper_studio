// The devices of the 3D view (play-page redesign, 2026-11-01): each category a small model of the hardware at its
// real size, built from three.js primitives (no asset files, no licences), showing its state as the hardware does: a
// light glows and lights the room, a sensor's LED flashes, a lock's bolt and status light change, a display shows
// its reading. A model's origin is its mount point; its front faces +z, toward the room (facingYaw turns it).
// THREE is passed in, so node tests can build the models without a browser.

export const CATEGORIES = ["light", "motion_sensor", "contact_sensor", "lock", "plug", "switch", "button", "thermostat",
  "climate_sensor", "smoke_sensor", "leak_sensor", "air_quality_sensor", "gas_sensor", "vibration_sensor",
  "light_sensor", "energy_meter", "cover", "fan", "other"];

const WHITE = 0xf3f2ee, GREY = 0xb9bec2, DARK = 0x2b3136, BRASS = 0xb08d57, GLASS = 0x1d2a33;
const WARM = 0xffd7a0, LED_OFF = 0x3a3f44, RED = 0xc23b2e, GREEN = 0x2e8a57, AMBER = 0xe0a030, BLUE = 0x2f7fd1;

// The rotation about y that turns a model's front (+z) to `facing` degrees (0 is +x, 90 is +z, as home.yaml's).
export const facingYaw = (facing) => Math.PI / 2 - ((facing || 0) * Math.PI) / 180;

const on = (state, effect) => effect && "on" in effect ? effect.on === true
  : (state || {})["On/Off.OnOff"] === true || String((state || {}).state || "").toUpperCase() === "ON";

// How a light looks (play-view environment design 2026-11-05, §1): off, nothing; on, a glow under its fixture, a warm
// pool on the floor and a warm light, each by its brightness (0..254, or a Matter light's level), so a dimmed light
// is visibly dimmer and an on/off light is full. A light that is on always shows a little (`shown`); one switched on
// whose level reads 0 (a twin that never reported one) is at full, as the bulb would be.
export function lightLook(state, effect) {
  const s = state || {};
  if (!on(s, effect || {})) return { on: false, level: 0, glow: 0, pool: 0, intensity: 0, emissive: 0.05 };
  const given = [s.brightness, s["Level Control.CurrentLevel"]].find((v) => typeof v === "number" && v > 0);
  const level = Math.max(0, Math.min(1, (given ?? 254) / 254)), shown = Math.max(0.15, level);
  return { on: true, level, glow: 0.9 * shown, pool: 0.6 * shown, intensity: 16 * shown, emissive: 1.2 + 2.3 * shown };
}

// `looks`: {glowMap, poolMap}, the soft round textures a light's glow and floor pool are drawn with (view3d.js paints
// them; without them, as under node, the two are plain shapes).
export function buildDevice(T, d, looks = {}) {
  const group = new T.Group();
  group.name = d.name || "device";
  const mat = (color, extra = {}) => new T.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0, ...extra });
  const add = (geometry, material, name, [x, y, z] = [0, 0, 0]) => {
    const mesh = new T.Mesh(geometry, material);
    mesh.position.set(x, y, z);
    if (name) mesh.name = name;
    group.add(mesh);
    return mesh;
  };
  const led = (r, at, color = LED_OFF) => add(new T.SphereGeometry(r, 12, 8),
    mat(color, { emissive: color, emissiveIntensity: 0 }), "led", at);
  const setLed = (mesh, lit, color = GREEN, intensity = 2.5) => {
    mesh.material.color.setHex(lit ? color : LED_OFF);
    mesh.material.emissive.setHex(lit ? color : LED_OFF);
    mesh.material.emissiveIntensity = lit ? intensity : 0;
  };
  const display = (w, h, at) => { // a screen whose text is drawn on a canvas, when there is a document to draw on
    const material = mat(GLASS, { roughness: 0.2 });
    const mesh = add(new T.PlaneGeometry(w, h), material, "display", at);
    mesh.userData.text = "";
    mesh.userData.show = (text) => {
      if (text === mesh.userData.text) return;
      mesh.userData.text = text;
      if (typeof document === "undefined") return;
      const c = document.createElement("canvas");
      c.width = 256;
      c.height = Math.round((256 * h) / w);
      const g = c.getContext("2d");
      g.fillStyle = "#1d2a33";
      g.fillRect(0, 0, c.width, c.height);
      g.fillStyle = "#e9f2f5";
      g.font = `600 ${Math.round(c.height * 0.42)}px system-ui, sans-serif`;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(text, c.width / 2, c.height / 2);
      if (material.map) material.map.dispose();
      material.map = new T.CanvasTexture(c);
      material.map.colorSpace = T.SRGBColorSpace;
      material.color.setHex(0xffffff);
      material.emissive.setHex(0xffffff);
      material.emissiveMap = material.map;
      material.emissiveIntensity = 0.6;
      material.needsUpdate = true;
    };
    return mesh;
  };
  const fmt = (v, digits = 1) => (typeof v === "number" ? (Math.round(v * 10 ** digits) / 10 ** digits).toString() : "");
  let update = () => {};
  let light = null;
  const aura = new T.Group(); // what a light casts beyond its hardware: drawn at the same place, not part of its size
  aura.name = `${group.name} light`;
  const c = d.category;

  if (c === "light") { // a flush ceiling fixture: canopy, stem, shade and a diffuser that glows
    add(new T.CylinderGeometry(0.05, 0.05, 0.02, 24), mat(WHITE), "canopy", [0, 0.13, 0]);
    add(new T.CylinderGeometry(0.006, 0.006, 0.12, 8), mat(GREY), "stem", [0, 0.06, 0]);
    add(new T.CylinderGeometry(0.11, 0.15, 0.07, 32, 1, true), mat(WHITE, { side: T.DoubleSide }), "shade", [0, -0.02, 0]);
    const diffuser = add(new T.CylinderGeometry(0.145, 0.145, 0.012, 32),
      mat(0xfaf6ee, { emissive: WARM, emissiveIntensity: 0.05, roughness: 0.9 }), "diffuser", [0, -0.06, 0]);
    light = new T.PointLight(WARM, 0, 9, 2);
    light.position.set(0, -0.2, 0);
    group.add(light);
    const soft = (map) => ({ color: WARM, transparent: true, opacity: 0, depthWrite: false, blending: T.AdditiveBlending,
      ...(map ? { map } : {}) });
    const glow = new T.Sprite(new T.SpriteMaterial(soft(looks.glowMap))); // under the fixture, seen at eye level
    glow.name = "glow";
    glow.position.set(0, -0.16, 0);
    glow.scale.set(0.7, 0.7, 1);
    aura.add(glow);
    const pool = new T.Mesh(new T.PlaneGeometry(3.2, 3.2).rotateX(-Math.PI / 2), // light on the floor below it
      new T.MeshBasicMaterial({ ...soft(looks.poolMap), toneMapped: false }));
    pool.name = "pool";
    pool.position.set(0, -(d.drop ?? 2.35) + 0.012, 0);
    pool.renderOrder = 1;
    aura.add(pool);
    update = (state, effect) => {
      const look = lightLook(state, effect);
      light.intensity = look.intensity;
      diffuser.material.emissiveIntensity = look.emissive;
      glow.visible = pool.visible = look.on;
      glow.material.opacity = look.glow;
      pool.material.opacity = look.pool;
    };
  } else if (c === "motion_sensor" || c === "light_sensor") { // a puck with a lens dome and an LED
    add(new T.CylinderGeometry(0.03, 0.032, 0.03, 24), mat(WHITE), "body", [0, -0.015, 0]);
    add(new T.SphereGeometry(0.02, 20, 12, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2),
      mat(0xe9edf0, { roughness: 0.15, transparent: true, opacity: 0.85 }), "lens", [0, -0.03, 0]);
    const l = led(0.0035, [0.022, -0.026, 0.012]);
    update = (state) => setLed(l, (state || {}).occupancy === true || (state || {}).presence === true, RED, 3);
  } else if (c === "contact_sensor") { // the sensor on the frame, its magnet on the leaf, a gap between
    add(new T.BoxGeometry(0.022, 0.07, 0.012), mat(WHITE), "sensor", [-0.014, 0, 0.006]);
    add(new T.BoxGeometry(0.012, 0.05, 0.01), mat(WHITE), "magnet", [0.012, 0, 0.005]);
    const l = led(0.003, [-0.014, 0.025, 0.013]);
    update = (state) => setLed(l, (state || {}).contact === false, AMBER, 3);
  } else if (c === "lock") { // a deadbolt's interior: escutcheon, thumb-turn, status light, the bolt in the edge
    add(new T.BoxGeometry(0.07, 0.16, 0.012), mat(DARK, { metalness: 0.5, roughness: 0.35 }), "escutcheon", [0, 0, 0.006]);
    add(new T.CylinderGeometry(0.017, 0.017, 0.012, 20), mat(BRASS, { metalness: 0.8, roughness: 0.3 }), "rose",
      [0, -0.03, 0.016]).rotation.x = Math.PI / 2;
    const turn = add(new T.BoxGeometry(0.012, 0.04, 0.012), mat(BRASS, { metalness: 0.8, roughness: 0.3 }), "turn",
      [0, -0.03, 0.026]);
    const status = add(new T.SphereGeometry(0.005, 12, 8), mat(GREEN, { emissive: GREEN, emissiveIntensity: 1.5 }),
      "status", [0, 0.055, 0.013]);
    const bolt = add(new T.BoxGeometry(0.03, 0.02, 0.02), mat(GREY, { metalness: 0.8, roughness: 0.3 }), "bolt",
      [0.035, -0.03, 0.006]);
    update = (state, effect) => {
      const locked = effect && "locked" in effect ? effect.locked === true
        : String((state || {}).state || "").toUpperCase() === "LOCK";
      status.material.color.setHex(locked ? RED : GREEN);
      status.material.emissive.setHex(locked ? RED : GREEN);
      bolt.position.x = locked ? 0.055 : 0.035;
      turn.rotation.z = locked ? Math.PI / 2 : 0;
    };
  } else if (c === "plug" || c === "energy_meter") { // a plug-in socket on the wall, its button ring lit when on
    add(new T.BoxGeometry(0.06, 0.06, 0.07), mat(WHITE), "body", [0, 0, 0.035]);
    add(new T.CylinderGeometry(0.022, 0.022, 0.004, 24), mat(0xe2e2dd), "face", [0, 0, 0.071]).rotation.x = Math.PI / 2;
    const l = add(new T.TorusGeometry(0.009, 0.002, 8, 24), mat(LED_OFF, { emissive: LED_OFF, emissiveIntensity: 0 }),
      "led", [0, 0.022, 0.071]);
    update = (state, effect) => setLed(l, on(state, effect), BLUE, 3);
  } else if (c === "switch" || c === "button") { // a wall plate with a rocker paddle (a button: a round key)
    add(new T.BoxGeometry(0.086, 0.086, 0.008), mat(WHITE), "plate", [0, 0, 0.004]);
    const paddle = c === "switch"
      ? add(new T.BoxGeometry(0.05, 0.06, 0.008), mat(0xfbfaf7), "paddle", [0, 0, 0.011])
      : add(new T.CylinderGeometry(0.02, 0.02, 0.008, 24), mat(0xfbfaf7), "paddle", [0, 0, 0.011]);
    if (c === "button") paddle.rotation.x = Math.PI / 2;
    update = (state, effect) => { if (c === "switch") paddle.rotation.x = on(state, effect) ? -0.12 : 0.12; };
  } else if (c === "thermostat") { // a round thermostat with its display
    add(new T.CylinderGeometry(0.045, 0.045, 0.022, 32), mat(WHITE), "body", [0, 0, 0.011]).rotation.x = Math.PI / 2;
    const screen = display(0.05, 0.03, [0, 0, 0.0225]);
    update = (state) => {
      const s = state || {}, t = s.local_temperature ?? s.temperature;
      screen.userData.show(t === undefined ? "--" : `${fmt(t)}°`);
    };
  } else if (c === "climate_sensor" || c === "air_quality_sensor" || c === "gas_sensor") { // a square with a display
    add(new T.BoxGeometry(0.045, 0.045, 0.012), mat(WHITE), "body", [0, 0, 0.006]);
    const screen = display(0.036, 0.024, [0, 0.002, 0.0125]);
    update = (state) => {
      const s = state || {};
      const parts = [s.temperature !== undefined ? `${fmt(s.temperature)}°` : null,
        s.humidity !== undefined ? `${fmt(s.humidity, 0)}%` : null,
        s.co2 !== undefined ? `${fmt(s.co2, 0)} ppm` : null].filter(Boolean);
      screen.userData.show(parts.join(" ") || "--");
    };
  } else if (c === "smoke_sensor") { // a ceiling disc with vents and an alarm LED
    add(new T.CylinderGeometry(0.06, 0.055, 0.04, 32), mat(WHITE), "body", [0, -0.02, 0]);
    add(new T.TorusGeometry(0.045, 0.003, 6, 32), mat(GREY), "vents", [0, -0.041, 0]).rotation.x = Math.PI / 2;
    const l = led(0.004, [0.02, -0.042, 0]);
    update = (state) => {
      const s = state || {};
      setLed(l, s.smoke === true || s.alarm === true || s.gas === true, RED, 3);
    };
  } else if (c === "leak_sensor") { // a disc on the floor with its probes
    add(new T.CylinderGeometry(0.025, 0.027, 0.012, 24), mat(WHITE), "body", [0, 0.006, 0]);
    const l = led(0.003, [0.012, 0.013, 0.012]);
    update = (state) => setLed(l, (state || {}).water_leak === true, RED, 3);
  } else { // vibration, cover, fan, other: a plain unit with a state LED
    add(new T.BoxGeometry(0.05, 0.05, 0.02), mat(WHITE), "body", [0, 0, 0.01]);
    const l = led(0.003, [0.016, 0.016, 0.021]);
    update = (state, effect) => setLed(l, on(state, effect) || (state || {}).vibration === true, GREEN, 2);
  }
  group.traverse((o) => { if (o.isMesh) o.castShadow = false; });
  return { group, light, aura, update: (state, effect) => update(state || {}, effect || {}) };
}

// The hub (Home Assistant's box, with the Zigbee coordinator beside it, as on a shelf in a real home): a small white
// box with a status LED on a slim stand at table height, so it can be seen and clicked from across the room. Its LED
// is green while every device reaches it, amber when one does not.
export function buildHub(T) {
  const group = new T.Group();
  group.name = "hub";
  const mat = (color, extra = {}) => new T.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0, ...extra });
  const add = (geometry, material, name, [x, y, z]) => {
    const mesh = new T.Mesh(geometry, material);
    mesh.position.set(x, y, z);
    mesh.name = name;
    group.add(mesh);
    return mesh;
  };
  add(new T.CylinderGeometry(0.16, 0.18, 0.02, 24), mat(GREY), "foot", [0, 0.01, 0]);
  add(new T.CylinderGeometry(0.025, 0.025, 0.66, 12), mat(GREY), "stand", [0, 0.35, 0]);
  add(new T.BoxGeometry(0.34, 0.03, 0.26), mat(0xd9d3c7), "shelf", [0, 0.695, 0]);
  add(new T.BoxGeometry(0.12, 0.05, 0.12), mat(WHITE), "body", [-0.06, 0.735, 0]);
  add(new T.BoxGeometry(0.06, 0.018, 0.09), mat(DARK), "coordinator", [0.1, 0.719, 0]);
  const led = add(new T.SphereGeometry(0.008, 10, 8), mat(GREEN, { emissive: GREEN, emissiveIntensity: 2 }), "led",
    [-0.06, 0.745, 0.061]);
  return {
    group,
    update({ up = 0, devices = 0 } = {}) {
      const color = up >= devices ? GREEN : AMBER;
      led.material.color.setHex(color);
      led.material.emissive.setHex(color);
    },
  };
}
