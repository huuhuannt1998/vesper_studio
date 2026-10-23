// Export every zigbee-herdsman-converters definition as plain JSON.
// Usage: node zhc_export.cjs <path to the installed package> <output.json>
"use strict";
const fs = require("fs");
const [, , pkgPath, outPath] = process.argv;
const zhc = require(pkgPath);
const defs = require(pkgPath + "/dist/devices/index").default;
const version = require(pkgPath + "/package.json").version;

const fingerprints = (list) =>
  (list || []).map((f) => ({model_id: f.modelID || null, manufacturer_name: f.manufacturerName || null}));

const out = [];
for (const raw of defs) {
  const rec = {
    model: raw.model,
    vendor: raw.vendor,
    description: raw.description || "",
    zigbee_model: raw.zigbeeModel || [],
    fingerprint: fingerprints(raw.fingerprint),
    white_label: (raw.whiteLabel || []).map((w) => ({
      // a rebrand without its own vendor is sold under the parent's vendor
      vendor: w.vendor || raw.vendor, model: w.model, description: w.description || null,
      fingerprint: fingerprints(w.fingerprint),
    })),
    exposes: null,
    exposes_dynamic: false,
    options: [],
    ota: false,
    error: null,
  };
  try {
    const d = zhc.prepareDefinition(raw);
    rec.exposes_dynamic = typeof d.exposes === "function";
    const ex = rec.exposes_dynamic ? d.exposes({isDummyDevice: true}, {}) : d.exposes;
    rec.exposes = JSON.parse(JSON.stringify(ex));
    rec.options = (d.options || []).map((o) => o.name);
    rec.ota = Boolean(d.ota);
  } catch (e) {
    rec.error = String((e && e.message) || e);
  }
  out.push(rec);
}
fs.writeFileSync(outPath, JSON.stringify({package: "zigbee-herdsman-converters", version, definitions: out}));
console.log(`exported ${out.length} definitions from zigbee-herdsman-converters ${version}`);
