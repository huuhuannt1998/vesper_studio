// VESPER's Matter host (spec §2 Matter tier): every Matter twin as a real matter.js node, all in this process.
//
// Protocol (vesper/exposure/matter_tier.py): JSON lines. A request {id, op, ...} gets {id, ok, ...} or
// {id, ok: false, error}. Events carry no id: {event: "ready"} once, then {event: "changed", twin, feature,
// value} whenever a controller (Home Assistant's Matter Server) changes a twin's attribute. Values on the wire
// are VDS values; codec.mjs maps them to matter.js and back.
//
// Environment: MATTER_STORAGE_PATH (node storage), MATTER_MDNS_NETWORKINTERFACE (the interface nodes announce
// on; Matter cannot use loopback), VESPER_MATTER_OFFLINE=1 (build nodes but never start them: tests only).
import { send } from "./stdout-guard.mjs"; // first: keeps matter.js's console logging off the protocol stream
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { ServerNode } from "@matter/main";
import * as behaviors from "@matter/main/behaviors";
import * as clusters from "@matter/main/clusters";
import * as devices from "@matter/main/devices";
import { codecFor, norm, resolveFeature } from "./codec.mjs";
import { RECIPES, buildType } from "./recipes.mjs";

const OFFLINE = process.env.VESPER_MATTER_OFFLINE === "1";
const MODELS = Object.fromEntries(Object.entries(clusters).filter(([, c]) => c?.Complete?.attributes)
  .map(([name, c]) => [norm(name.replace(/Cluster$/, "")), { attributes: c.Complete.attributes }]));
const nodes = new Map(); // twin id -> {node, endpoint, features: Map(name -> {cluster, attribute, codec})}
const message = (e) => String(e?.message ?? e).slice(0, 500);
const storageId = (twin) => "n" + Buffer.from(twin).toString("hex").slice(0, 120); // a file-safe, unique name

function nodeOf(twin) {
  const n = nodes.get(twin);
  if (!n) throw new Error(`no node for twin ${twin}`);
  return n;
}

function featureOf(n, twin, feature) {
  const f = n.features.get(feature);
  if (!f) throw new Error(`${twin} has no exposed feature ${feature}`);
  return f;
}

async function add(r) {
  const recipe = RECIPES[r.device_type];
  if (!recipe) throw new Error(`no recipe for device type ${JSON.stringify(r.device_type)}`);
  if (nodes.has(r.twin)) throw new Error(`twin ${r.twin} already has a node`);
  const node = await ServerNode.create({
    id: storageId(r.twin),
    network: { port: r.port },
    commissioning: { passcode: r.passcode, discriminator: r.discriminator },
    productDescription: { name: r.product_name, deviceType: devices[recipe.device].deviceType },
    basicInformation: {
      vendorName: r.vendor_name, vendorId: r.vendor_id, productName: r.product_name, productLabel: r.product_name,
      productId: r.product_id, serialNumber: r.serial, nodeLabel: r.label, hardwareVersion: 1, softwareVersion: 1,
    },
  });
  try {
    const endpoint = await node.add(buildType(recipe, devices, behaviors), { id: "ep1", ...(recipe.init ?? {}) });
    const features = new Map();
    const unresolved = [];
    for (const f of r.features) {
      const where = resolveFeature(endpoint.state, MODELS, f.name);
      if (!where || ((f.type === "enum" || f.type === "bitmap") && !where.schema)) {
        unresolved.push(f.name);
        continue;
      }
      features.set(f.name, { cluster: where.cluster, attribute: where.attribute, codec: codecFor(f.type, where.schema) });
    }
    const initialErrors = [];
    for (const [name, value] of Object.entries(r.state ?? {})) {
      const f = features.get(name);
      if (!f || value === null || value === undefined) continue;
      try {
        await endpoint.set({ [f.cluster]: { [f.attribute]: f.codec.encode(value) } });
      } catch (e) {
        initialErrors.push(`${name}: ${message(e)}`);
      }
    }
    for (const [name, f] of features) {
      endpoint.events[f.cluster]?.[`${f.attribute}$Changed`]?.on((value, _old, context) => {
        if (context?.offline) return; // VESPER's own write (op "set"), not a controller's
        send({ event: "changed", twin: r.twin, feature: name, value: f.codec.decode(value) });
      });
    }
    if (!OFFLINE) await node.start(); // resolves once online and commissionable (announced by mDNS)
    nodes.set(r.twin, { node, endpoint, features });
    const codes = node.state.commissioning.pairingCodes;
    return { resolved: [...features.keys()], unresolved, initial_errors: initialErrors,
             pairing: { manual: codes.manualPairingCode, qr: codes.qrPairingCode } };
  } catch (e) {
    await node.close().catch(() => {});
    throw e;
  }
}

async function set({ twin, feature, value }) {
  const n = nodeOf(twin);
  const f = featureOf(n, twin, feature);
  await n.endpoint.set({ [f.cluster]: { [f.attribute]: f.codec.encode(value) } });
  return {};
}

function get({ twin }) {
  const n = nodeOf(twin);
  const values = {};
  for (const [name, f] of n.features) values[name] = f.codec.decode(n.endpoint.state[f.cluster][f.attribute]);
  return { values };
}

async function remove({ twin }) {
  const n = nodeOf(twin);
  nodes.delete(twin);
  await n.node.close();
  return {};
}

/** Ask the Matter Server at `url` to commission a node by its pairing code (its WebSocket API). */
function commission({ url, code, seconds }) {
  return new Promise((resolve, reject) => {
    let ws;
    const fail = (why) => { clearTimeout(timer); try { ws?.close(); } catch {} reject(new Error(`commission: ${why}`)); };
    const timer = setTimeout(() => fail(`no answer from ${url} within ${seconds} s`), seconds * 1000);
    try {
      ws = new WebSocket(url);
    } catch (e) {
      fail(message(e));
      return;
    }
    ws.onerror = (e) => fail(`cannot reach the Matter Server at ${url}: ${message(e?.error ?? e?.message ?? "error")}`);
    ws.onopen = () => ws.send(JSON.stringify({ message_id: "vesper-1", command: "commission_with_code",
                                               args: { code, network_only: true } }));
    ws.onmessage = (m) => {
      let reply;
      try { reply = JSON.parse(m.data); } catch { return; }
      if (reply.message_id !== "vesper-1") return; // the server's greeting and events
      clearTimeout(timer);
      ws.close();
      if (reply.error_code !== undefined) reject(new Error(`commission: ${reply.details ?? `error ${reply.error_code}`}`));
      else resolve({ node_id: Number(reply.result?.node_id) });
    };
  });
}

async function shutdown() {
  const all = [...nodes.values()];
  nodes.clear();
  await Promise.allSettled(all.map((n) => n.node.close()));
  return {};
}

const OPS = { add, set, get, remove, commission, shutdown };

async function answer(req) {
  const op = OPS[req.op];
  try {
    if (!op) throw new Error(`unknown op ${JSON.stringify(req.op)}`);
    send({ id: req.id, ok: true, ...(await op(req)) });
  } catch (e) {
    send({ id: req.id, ok: false, error: message(e) });
  }
}

const input = createInterface({ input: process.stdin });
let queue = Promise.resolve(); // node requests run one at a time, in order: a set never overtakes its add
input.on("line", (line) => {
  let req;
  try { req = JSON.parse(line); } catch { process.stderr.write(`unparseable request: ${line.slice(0, 200)}\n`); return; }
  if (req.op === "commission") { // minutes long and touches no node here: it must not hold up the twins' reports
    answer(req);
    return;
  }
  queue = queue.then(() => answer(req)).then(() => { if (req.op === "shutdown") process.exit(0); });
});
input.on("close", () => { queue = queue.then(shutdown).then(() => process.exit(0)); });

const pins = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).dependencies;
send({ event: "ready", packages: pins, offline: OFFLINE });
