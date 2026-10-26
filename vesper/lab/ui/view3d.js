// The 3D view (spec §2, §2a, §3; plan W1b): the home drawn with three.js. A home made from an HSSD scene is drawn
// from the scene's own files, each placed with the matrix Habitat gives it (/world/render.json); a home without one
// is drawn from its map: floor, walls and doors. The session stays the authority: people, devices and doors are
// drawn where its messages put them, and a door swings when the session opens it. A person is Habitat's humanoid
// for their avatar (its own model, walking with Habitat's walk as fast as they move, standing in Habitat's standing
// pose), or a plain figure when Habitat's humanoids are not installed. Third person by default, first person for V;
// the crosshair is the screen's centre, and pick() says which device or door it is on.
// The arithmetic is in scene3d.js, tested under node; this file needs a browser with WebGL (the looks check it).
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";
import { colorFor, decodeGrid, gridBounds } from "./mapview.js";
import {
  BACK_M, PIVOT_M, RAISE_M, cameraPose, devicePoint, frameAt, hinge, pullIn, roomAt, walkPhase, wallBoxes, wallRuns,
} from "./scene3d.js";

const SKY = 0xbfd6ea, WALL = 0xe9e4da, FLOOR = 0xd2c9b8, OUTSIDE = 0x9fb98e, DOOR = 0x8a5a2b, LOCKED = 0xc0392b;
const WALL_M = 2.5, DOOR_M = 2.05, PICK_M = 8, SWING = Math.PI / 2, SWING_S = 0.35;
const PLAYER = 0x2f6fed, RESIDENTS = [0xd9822b, 0x3fb37f, 0xb36bd9, 0xd94f4f];
const LAMP = 0xffd59a, SEEN = 0xff8c32;
const WALKING_MS = 0.15; // slower than this, a person stands

export async function createView3d(container, hello, onProgress = () => {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); // the looks read it
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.8;
  container.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY); // what a window shows
  scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.55;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7f70, 1.0));
  const sun = new THREE.DirectionalLight(0xfff4e0, 0.8);
  sun.position.set(4, 10, 2);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(65, 1, 0.05, 200);
  const floorY = hello.world3d ? hello.world3d.floor_y : 0;
  const solids = []; // what the camera and the crosshair do not pass: the stage, or the map's walls
  const pickables = []; // device markers and doors; their meshes carry userData.target
  const doors = {}; // doorway id -> {pivot, angle}
  const devices = {}; // name -> {marker, light, base}
  const actors = {}; // actor -> {group, x, z, yaw}
  const status = { source: hello.world3d ? "scene" : "map", total: 0, loaded: 0, failed: 0, missing: [] };
  const ktx2 = new KTX2Loader().setTranscoderPath("./vendor/three/examples/jsm/libs/basis/").detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx2);
  const humanoids = {}; // avatar -> a promise of {model, motion}: each loaded once, cloned per person

  const tag = (obj, target, kind) => obj.traverse((o) => { o.userData.target = target; o.userData.kind = kind; });

  function addDoor(id, obj, matrix) { // obj placed by matrix; it turns about its hinge when the door opens
    obj.matrixAutoUpdate = false;
    obj.matrix.copy(matrix);
    const box = new THREE.Box3().setFromObject(obj);
    const [hx, hz] = hinge(box.min.x, box.min.z, box.max.x, box.max.z);
    const pivot = new THREE.Group();
    pivot.position.set(hx, 0, hz);
    obj.matrix.premultiply(new THREE.Matrix4().makeTranslation(-hx, 0, -hz));
    pivot.add(obj);
    scene.add(pivot);
    tag(obj, id, "door");
    pickables.push(obj);
    doors[id] = { pivot, angle: 0 };
  }

  async function loadScene() {
    const manifest = await (await fetch(hello.world3d.render)).json();
    status.total = manifest.items.length;
    await Promise.all(manifest.items.map(async (item) => {
      try {
        const gltf = await loader.loadAsync(`/hssd/${item.asset.split("/").map(encodeURIComponent).join("/")}`);
        const matrix = new THREE.Matrix4().set(...item.matrix); // row by row
        if (item.doorway) {
          addDoor(item.doorway, gltf.scene, matrix);
        } else {
          gltf.scene.matrixAutoUpdate = false;
          gltf.scene.matrix.copy(matrix);
          scene.add(gltf.scene);
        }
        if (item.kind === "stage") solids.push(gltf.scene);
        status.loaded += 1;
      } catch (e) {
        status.failed += 1;
        status.missing.push(item.asset);
      }
      onProgress(status);
    }));
    for (const door of hello.doors) if (!doors[door.id]) addSlab(door.id); // a door the scene has no mesh for
  }

  function addSlab(id) { // a plain door leaf in the doorway's box
    const dw = hello.map.doorways.find((d) => d.id === id);
    if (!dw) return;
    const [x0, z0, x1, z1] = dw.box, alongX = x1 - x0 >= z1 - z0;
    const geo = new THREE.BoxGeometry(alongX ? x1 - x0 : 0.05, DOOR_M, alongX ? 0.05 : z1 - z0);
    const leaf = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: DOOR, roughness: 0.7 }));
    addDoor(id, leaf, new THREE.Matrix4().makeTranslation((x0 + x1) / 2, floorY + DOOR_M / 2, (z0 + z1) / 2));
  }

  function buildFromMap() { // the floor, the walls (blocked cells next to floor) and the doors, from the map
    const g = hello.map.grid, cells = decodeGrid(g.walkable, g.width, g.height), [ox, oz] = g.origin, c = g.cell;
    const inside = [], outside = [], walls = [];
    for (let j = 0; j < g.height; j++) {
      let i = 0;
      while (i < g.width) {
        if (!cells[j * g.width + i]) { i++; continue; }
        const start = i;
        while (i < g.width && cells[j * g.width + i]) i++;
        const x = ox + ((start + i) / 2) * c, z = oz + (j + 0.5) * c, room = roomAt(x, z, hello.map.rooms);
        const box = new THREE.BoxGeometry((i - start) * c, 0.02, c).translate(x, floorY - 0.01, z);
        (room && room.outdoor ? outside : inside).push(box);
      }
    }
    for (const [i0, i1, j0, j1] of wallBoxes(wallRuns(cells, g.width, g.height))) {
      walls.push(new THREE.BoxGeometry((i1 - i0) * c, WALL_M, (j1 - j0) * c)
        .translate(ox + ((i0 + i1) / 2) * c, floorY + WALL_M / 2, oz + ((j0 + j1) / 2) * c));
    }
    const mesh = (parts, color) => {
      const m = new THREE.Mesh(mergeGeometries(parts), new THREE.MeshStandardMaterial({ color, roughness: 0.9 }));
      scene.add(m);
      return m;
    };
    if (inside.length) mesh(inside, FLOOR);
    if (outside.length) mesh(outside, OUTSIDE);
    if (walls.length) solids.push(mesh(walls, WALL));
    const b = gridBounds(g), ceiling = new THREE.Mesh(new THREE.PlaneGeometry(b.maxX - b.minX, b.maxZ - b.minZ),
      new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    ceiling.rotation.x = Math.PI / 2;
    ceiling.position.set((b.minX + b.maxX) / 2, floorY + WALL_M, (b.minZ + b.maxZ) / 2);
    ceiling.visible = false; // not drawn (the house stays open to the sky), but the camera stops under it
    scene.add(ceiling);
    solids.push(ceiling);
    for (const door of hello.doors) addSlab(door.id);
  }

  function label(text, height = 0.12) { // a name tag above a device or a person, `height` metres tall
    const pad = 12, font = "600 28px system-ui, sans-serif", probe = document.createElement("canvas").getContext("2d");
    probe.font = font;
    const w = Math.ceil(probe.measureText(text).width) + 2 * pad, h = 44;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "rgba(18, 22, 31, 0.78)";
    ctx.beginPath();
    ctx.roundRect(0, 0, w, h, 10);
    ctx.fill();
    ctx.font = font;
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, pad, h / 2 + 1);
    const map = new THREE.CanvasTexture(canvas);
    map.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map, transparent: true }));
    sprite.scale.set((height * w) / h, height, 1);
    return sprite;
  }

  for (const d of hello.devices) { // a marker, a name and, for a lamp, its light
    const [x, y, z] = devicePoint(d, floorY), base = new THREE.Color(colorFor(d.category));
    const marker = new THREE.Mesh(new THREE.SphereGeometry(0.07, 20, 14),
      new THREE.MeshStandardMaterial({ color: base, emissive: base, emissiveIntensity: 0.3, roughness: 0.35 }));
    marker.position.set(x, y, z);
    const reach = new THREE.Mesh(new THREE.SphereGeometry(0.25, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    reach.position.set(x, y, z); // a larger target for the crosshair than the marker itself
    tag(reach, d.name, "device");
    pickables.push(reach);
    const name = label(d.name, 0.08);
    name.position.set(x, y + 0.16, z);
    scene.add(marker, reach, name);
    let light = null;
    if (d.category === "light") {
      light = new THREE.PointLight(LAMP, 0, 7, 2);
      light.position.set(x, y - 0.1, z);
      scene.add(light);
    }
    devices[d.name] = { marker, light, base };
  }

  function plainFigure(color) { // facing +x: body, head, and a nose that shows its heading
    const skin = new THREE.MeshStandardMaterial({ color, roughness: 0.6 });
    const figure = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.19, 0.95, 6, 14), skin);
    body.position.y = 0.72;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 14), skin);
    head.position.y = 1.52;
    const nose = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.04, 0.05), new THREE.MeshStandardMaterial({ color: 0xffffff }));
    nose.position.set(0.12, 1.53, 0);
    figure.add(body, head, nose);
    return figure;
  }

  function humanoid(model) {
    const h = hello.humanoids && hello.humanoids[model];
    if (!h) return null;
    return humanoids[model] ||= Promise.all([loader.loadAsync(h.model), fetch(h.motion).then((r) => r.json())])
      .then(([gltf, motion]) => ({ model: gltf.scene, motion }));
  }

  function pose(p, quaternions) { // each bone of the person's humanoid to its rotation (x, y, z, w)
    p.bones.forEach((bone, j) => { if (bone) bone.quaternion.fromArray(quaternions[j]); });
  }

  // A person: a plain figure at once, and Habitat's humanoid for their avatar once it has loaded. The promise is
  // settled when the person is as they will look.
  const shadowTexture = (() => { // a soft dark disc: where a person stands
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d"), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, "rgba(0, 0, 0, 0.45)");
    r.addColorStop(1, "rgba(0, 0, 0, 0)");
    g.fillStyle = r;
    g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  })();

  function person(a, isPlayer, k) {
    const group = new THREE.Group();
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 0.8),
      new THREE.MeshBasicMaterial({ map: shadowTexture, transparent: true, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = 0.01;
    group.add(shadow);
    const p = { group, x: a.x, z: a.z, yaw: a.yaw, figure: plainFigure(isPlayer ? PLAYER : RESIDENTS[k % RESIDENTS.length]),
      bones: null, motion: null, phase: 0, walking: false };
    group.add(p.figure);
    if (!isPlayer) { // as in PUBG, the player's own figure carries no name
      const tagged = label(a.actor);
      tagged.position.y = 1.85;
      group.add(tagged);
    }
    scene.add(group);
    const asset = humanoid(a.model);
    p.ready = !asset ? Promise.resolve() : asset.then(({ model, motion }) => {
      const body = cloneSkinned(model);
      body.rotation.y = Math.PI / 2; // Habitat's humanoids face +z; VESPER's figures face +x
      body.traverse((o) => { if (o.isSkinnedMesh) o.frustumCulled = false; }); // its bounds are the rest pose's
      group.remove(p.figure);
      group.add(body);
      p.figure = body;
      p.motion = motion;
      p.bones = motion.joints.map((name) => body.getObjectByName(name) || null);
      pose(p, motion.stop);
    }).catch(() => {}); // the plain figure stays
    return p;
  }

  hello.actors.forEach((a, k) => { actors[a.actor] = person(a, a.actor === hello.player, k); });
  const people = Promise.all(Object.values(actors).map((p) => p.ready));

  if (hello.world3d) await loadScene();
  else buildFromMap();
  await people;
  status.ready = true;
  onProgress(status);

  // -- each frame --------------------------------------------------------------------------------------
  const ray = new THREE.Raycaster(), back = new THREE.Raycaster();
  ray.far = PICK_M;
  let distance = BACK_M, lastProbe = 0;

  function targetOf(obj) {
    for (let o = obj; o; o = o.parent) if (o.userData.target) return { target: o.userData.target, kind: o.userData.kind };
    return null;
  }

  const blend = new THREE.Quaternion();
  function animate(p, moved, dt) { // Habitat's walk while moving, its standing pose when still
    const walking = dt > 0 && moved / dt > WALKING_MS;
    if (!walking) {
      if (p.walking) pose(p, p.motion.stop);
      p.walking = false;
      return;
    }
    p.walking = true;
    p.phase = walkPhase(p.phase, moved, p.motion.cycle_m);
    const walk = p.motion.walk, [k0, k1, t] = frameAt(p.phase, walk.length);
    p.bones.forEach((bone, j) => {
      if (bone) bone.quaternion.fromArray(walk[k0][j]).slerp(blend.fromArray(walk[k1][j]), t);
    });
  }

  function update(state, cam, dt) {
    const k = 1 - Math.exp(-12 * dt); // people glide to where the session put them
    for (const a of Object.values(state.actors)) {
      const p = actors[a.actor] ||= person(a, a.actor === state.player, Object.keys(actors).length);
      const x0 = p.x, z0 = p.z;
      p.x += (a.x - p.x) * k;
      p.z += (a.z - p.z) * k;
      p.group.position.set(p.x, floorY, p.z);
      p.group.rotation.y = (-a.yaw * Math.PI) / 180;
      if (p.bones) animate(p, Math.hypot(p.x - x0, p.z - z0), dt);
    }
    for (const [id, door] of Object.entries(doors)) { // a door swings open and shut over SWING_S
      const e = state.effects[id] || {}, want = e.open ? SWING : 0, step = (SWING / SWING_S) * dt;
      door.angle += Math.max(-step, Math.min(step, want - door.angle));
      door.pivot.rotation.y = door.angle;
      if (door.pivot.children[0] && door.pivot.children[0].material) {
        door.pivot.children[0].material.color.setHex(e.locked ? LOCKED : DOOR); // only the plain leaves
      }
    }
    for (const [name, dev] of Object.entries(devices)) {
      const d = state.devices[name], e = state.effects[name] || {}, m = dev.marker.material;
      const seen = d && d.state && (d.state.occupancy === true || d.state.presence === true);
      if ("locked" in e) m.color.setHex(e.locked ? LOCKED : 0x3fb37f);
      m.emissive.copy(seen ? new THREE.Color(SEEN) : m.color);
      m.emissiveIntensity = e.on === true ? 2.2 : seen ? 1.2 : e.on === false ? 0.05 : 0.3;
      dev.marker.scale.setScalar(seen ? 1.5 : 1);
      if (dev.light) dev.light.intensity = e.on === true ? 4 : 0;
    }
    const me = actors[state.player];
    if (!me) return;
    me.group.visible = cam.mode !== "first";
    const now = performance.now();
    if (cam.mode === "third" && now - lastProbe > 80) { // a wall behind pulls the camera in
      lastProbe = now;
      const pose = cameraPose({ x: me.x, z: me.z, floorY, yaw: cam.yaw, pitch: cam.pitch, mode: "third" });
      const from = new THREE.Vector3(me.x, floorY + PIVOT_M + RAISE_M, me.z);
      const toward = new THREE.Vector3(...pose.position).sub(from).normalize();
      back.set(from, toward);
      back.far = BACK_M + 0.3;
      const hit = back.intersectObjects(solids, true)[0];
      distance = pullIn(BACK_M, hit ? hit.distance : Infinity);
    }
    const pose = cameraPose({ x: me.x, z: me.z, floorY, yaw: cam.yaw, pitch: cam.pitch, mode: cam.mode, distance });
    camera.position.set(...pose.position);
    camera.lookAt(...pose.target);
  }

  return {
    status, camera,
    floorY,
    frame(state, cam, dt) {
      update(state, cam, dt);
      renderer.render(scene, camera);
    },
    resize() {
      const w = container.clientWidth || 1, h = container.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    },
    pick() { // the device or door under the crosshair, unless a wall is nearer
      ray.setFromCamera(new THREE.Vector2(0, 0), camera);
      for (const hit of ray.intersectObjects([...pickables, ...solids], true)) {
        const t = targetOf(hit.object);
        if (t) return t;
        return null; // a wall or the floor is in the way
      }
      return null;
    },
    pointOf(target) { // where the crosshair should be to use `target`
      if (devices[target]) return devices[target].marker.position.toArray();
      const dw = hello.map.doorways.find((d) => d.id === target);
      return dw ? [dw.center[0], floorY + 1.1, dw.center[1]] : null;
    },
    colors() { // how many colours the 3D view shows (1 is a blank view)
      const c = document.createElement("canvas");
      c.width = 64;
      c.height = 40;
      const ctx = c.getContext("2d");
      ctx.drawImage(renderer.domElement, 0, 0, 64, 40);
      const data = ctx.getImageData(0, 0, 64, 40).data, seen = new Set();
      for (let k = 0; k < data.length; k += 4) seen.add(data[k] << 16 | data[k + 1] << 8 | data[k + 2]);
      return seen.size;
    },
  };
}
