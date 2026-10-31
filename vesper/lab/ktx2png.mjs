// HSSD's models for Blender (home-generator spec §5, plan W9b): HSSD packs its textures as KTX2 (Basis Universal),
// which Blender's glTF importer cannot read. This turns each KTX2 image of a GLB into a PNG of its full-size level,
// transcoded by the Basis transcoder the 3D view already ships, and leaves the rest of the model as it was.
//
//   node ktx2png.mjs IN.glb OUT.glb          one model
//   node ktx2png.mjs --batch < pairs.json    [[in, out], ...]: many, the transcoder started once
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASIS_DIR = join(HERE, "ui", "vendor", "three", "examples", "jsm", "libs", "basis");
const RGBA32 = 13; // the transcoder's cTFRGBA32
const MAGIC = 0x46546c67, JSON_CHUNK = 0x4e4f534a, BIN_CHUNK = 0x004e4942;

export function readGlb(buf) {
  if (buf.readUInt32LE(0) !== MAGIC) throw new Error("not a GLB file");
  const n = buf.readUInt32LE(12);
  const json = JSON.parse(buf.subarray(20, 20 + n).toString("utf8"));
  const at = 20 + n, bin = at < buf.length ? buf.subarray(at + 8, at + 8 + buf.readUInt32LE(at)) : Buffer.alloc(0);
  return { json, bin };
}

function writeGlb(json, bin) {
  const pad = (b, fill) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) % 4, fill)]);
  const j = pad(Buffer.from(JSON.stringify(json)), 0x20), b = pad(bin, 0);
  const chunk = (len, type) => { const c = Buffer.alloc(8); c.writeUInt32LE(len, 0); c.writeUInt32LE(type, 4); return c; };
  const head = Buffer.alloc(12);
  head.writeUInt32LE(MAGIC, 0); head.writeUInt32LE(2, 4); head.writeUInt32LE(12 + 8 + j.length + 8 + b.length, 8);
  return Buffer.concat([head, chunk(j.length, JSON_CHUNK), j, chunk(b.length, BIN_CHUNK), b]);
}

const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export function encodePng(width, height, rgba) { // 8-bit RGBA, each row unfiltered
  const rows = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    rows[y * (width * 4 + 1)] = 0;
    Buffer.from(rgba.buffer, rgba.byteOffset + y * width * 4, width * 4).copy(rows, y * (width * 4 + 1) + 1);
  }
  const chunk = (type, data) => {
    const head = Buffer.alloc(8), crc = Buffer.alloc(4), body = Buffer.concat([Buffer.from(type, "latin1"), data]);
    head.writeUInt32BE(data.length, 0); head.write(type, 4, "latin1"); crc.writeUInt32BE(crc32(body), 0);
    return Buffer.concat([head, data, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr),
                        chunk("IDAT", deflateSync(rows)), chunk("IEND", Buffer.alloc(0))]);
}

// `transcode(ktxBytes)` -> {width, height, rgba}: the KTX2 image's full-size level as 8-bit RGBA.
export async function convertGlb(buf, transcode) {
  const { json, bin } = readGlb(buf);
  const ktx = new Set((json.images || []).flatMap((img, k) => (img.mimeType === "image/ktx2" ? [k] : [])));
  if (!ktx.size) return buf;
  const replaced = new Map(); // bufferView -> new bytes
  for (const k of ktx) {
    const img = json.images[k], bv = json.bufferViews[img.bufferView];
    const { width, height, rgba } = await transcode(bin.subarray(bv.byteOffset || 0, (bv.byteOffset || 0) + bv.byteLength));
    replaced.set(img.bufferView, encodePng(width, height, rgba));
    img.mimeType = "image/png";
  }
  const parts = [];
  let offset = 0;
  json.bufferViews.forEach((bv, k) => { // repacked in order, each view on a 4-byte boundary
    const bytes = replaced.get(k) || bin.subarray(bv.byteOffset || 0, (bv.byteOffset || 0) + bv.byteLength);
    const gap = (4 - (offset % 4)) % 4;
    if (gap) parts.push(Buffer.alloc(gap));
    offset += gap;
    bv.byteOffset = offset;
    bv.byteLength = bytes.length;
    parts.push(bytes);
    offset += bytes.length;
  });
  for (const t of json.textures || []) {
    const basisu = t.extensions && t.extensions.KHR_texture_basisu;
    if (!basisu) continue;
    t.source = basisu.source;
    delete t.extensions.KHR_texture_basisu;
    if (!Object.keys(t.extensions).length) delete t.extensions;
  }
  for (const key of ["extensionsUsed", "extensionsRequired"]) {
    if (!json[key]) continue;
    json[key] = json[key].filter((e) => e !== "KHR_texture_basisu");
    if (!json[key].length) delete json[key];
  }
  const out = Buffer.concat(parts);
  json.buffers[0].byteLength = out.length;
  return writeGlb(json, out);
}

export async function basisTranscoder() { // the 3D view's own transcoder, run here
  const BASIS = createRequire(import.meta.url)(join(BASIS_DIR, "basis_transcoder.js"));
  const M = await BASIS({ wasmBinary: readFileSync(join(BASIS_DIR, "basis_transcoder.wasm")) });
  M.initializeBasis();
  return (ktx) => {
    const f = new M.KTX2File(new Uint8Array(ktx));
    try {
      if (!f.isValid() || !f.startTranscoding()) throw new Error("not a KTX2 texture the transcoder reads");
      const width = f.getWidth(), height = f.getHeight();
      const rgba = new Uint8Array(f.getImageTranscodedSizeInBytes(0, 0, 0, RGBA32));
      if (!f.transcodeImage(rgba, 0, 0, 0, RGBA32, 0, -1, -1)) throw new Error("the transcoder failed");
      return { width, height, rgba };
    } finally {
      f.close();
      f.delete();
    }
  };
}

async function main(argv) {
  const pairs = argv[0] === "--batch" ? JSON.parse(readFileSync(0, "utf8")) : [[argv[0], argv[1]]];
  if (!pairs.length || pairs.some((p) => !p[0] || !p[1])) {
    console.error("usage: node ktx2png.mjs IN.glb OUT.glb | --batch < [[in, out], ...]");
    return 2;
  }
  const transcode = await basisTranscoder();
  for (const [src, dst] of pairs) {
    mkdirSync(dirname(dst), { recursive: true });
    writeFileSync(dst, await convertGlb(readFileSync(src), transcode));
  }
  return 0;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (e) => { console.error(String(e.message || e)); process.exit(1); });
}
