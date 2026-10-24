// VDS values <-> matter.js values, driven by the attribute's schema in the matter.js model.
//
// VESPER's records name Matter things the way connectedhomeip's ZAP XML does ("Door Lock.LockState" =
// "Unlocked"); matter.js names the same things in camel case (doorLock.lockState = 2). Names are matched
// after normalization: lower case, letters and digits only. Pure functions: schemas are passed in, so the
// tests run without matter.js installed.

export const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");

export function children(schema) {
  return schema.children?.length ? schema.children : (schema.base?.children ?? []);
}

/** A VDS enum name <-> its numeric code. */
export function enumCodec(schema) {
  const byName = new Map(children(schema).map((c) => [norm(c.name), c.id]));
  const byCode = new Map(children(schema).map((c) => [c.id, c.name]));
  return {
    encode(name) {
      const code = byName.get(norm(name));
      if (code === undefined) throw new Error(`${JSON.stringify(name)} is not a value of ${schema.name}`);
      return code;
    },
    decode: (code) => byCode.get(code) ?? code,
  };
}

/** A bitmap's fields: single bits ("3") or ranges ("4 to 6"), keyed as matter.js keys them. */
export function bitmapFields(schema) {
  return children(schema).map((c) => {
    const r = /^(\d+)(?:\s*to\s*(\d+))?$/.exec(String(c.constraint).trim());
    if (!r) throw new Error(`cannot read bit position ${c.constraint} of ${schema.name}.${c.name}`);
    const lo = Number(r[1]);
    return { key: c.name.charAt(0).toLowerCase() + c.name.slice(1), lo, hi: r[2] === undefined ? lo : Number(r[2]) };
  });
}

/** A VDS bitmap integer <-> the object matter.js keeps ({occupied: true}). */
export function bitmapCodec(schema) {
  const fields = bitmapFields(schema);
  return {
    encode: (n) => Object.fromEntries(fields.map((f) => {
      const v = Math.floor(n / 2 ** f.lo) % 2 ** (f.hi - f.lo + 1);
      return [f.key, f.lo === f.hi ? v === 1 : v];
    })),
    decode: (o) => fields.reduce((n, f) => n + (f.lo === f.hi ? (o?.[f.key] ? 1 : 0) : (o?.[f.key] ?? 0)) * 2 ** f.lo, 0),
  };
}

const IDENTITY = { encode: (v) => v, decode: (v) => v };

/** The codec for one VDS feature type; `schema` is needed only for enums and bitmaps. */
export function codecFor(type, schema) {
  if (type === "enum") return enumCodec(schema);
  if (type === "bitmap") return bitmapCodec(schema);
  return IDENTITY;
}

/** The key of `object` whose normalized name is `name`'s ("On/Off" finds "onOff"), or undefined. */
export function findKey(object, name) {
  return Object.keys(object ?? {}).find((k) => norm(k) === norm(name));
}

/**
 * Resolve a VDS feature name ("Cluster.Attribute") against an endpoint's state: {cluster, attribute, schema}
 * or null. `models` maps normalized cluster names to matter.js cluster models ({attributes: {name: {schema}}}).
 */
export function resolveFeature(state, models, featureName) {
  const dot = featureName.indexOf(".");
  if (dot < 0) return null;
  const cluster = findKey(state, featureName.slice(0, dot));
  const attribute = cluster && findKey(state[cluster], featureName.slice(dot + 1));
  if (!attribute) return null;
  const model = models[norm(featureName.slice(0, dot))];
  const modelKey = model && findKey(model.attributes, attribute);
  return { cluster, attribute, schema: modelKey ? model.attributes[modelKey].schema : null };
}
