// HSSD's models for Blender (plan W9b): their KTX2 (Basis) textures turned into PNG, which Blender's glTF importer
// reads, everything else in the model kept as it was.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { inflateSync } from "node:zlib";
import { basisTranscoder, convertGlb, encodePng, readGlb } from "../../../vesper/lab/ktx2png.mjs";

function glb(json, bin) { // a GLB from its JSON and its binary chunk
  const pad = (b, fill) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) % 4, fill)]);
  const j = pad(Buffer.from(JSON.stringify(json)), 0x20), b = pad(Buffer.from(bin), 0);
  const head = Buffer.alloc(12);
  head.writeUInt32LE(0x46546c67, 0); head.writeUInt32LE(2, 4); head.writeUInt32LE(12 + 8 + j.length + 8 + b.length, 8);
  const chunk = (len, type) => { const c = Buffer.alloc(8); c.writeUInt32LE(len, 0); c.writeUInt32LE(type, 4); return c; };
  return Buffer.concat([head, chunk(j.length, 0x4e4f534a), j, chunk(b.length, 0x004e4942), b]);
}

const POSITIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
const MODEL = () => glb({
  asset: { version: "2.0" },
  extensionsUsed: ["KHR_materials_ior", "KHR_texture_basisu"], extensionsRequired: ["KHR_texture_basisu"],
  bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 12 }, { buffer: 0, byteOffset: 12, byteLength: 5 }],
  buffers: [{ byteLength: 17 }],
  images: [{ bufferView: 1, mimeType: "image/ktx2" }],
  textures: [{ sampler: 0, extensions: { KHR_texture_basisu: { source: 0 } } }],
}, [...POSITIONS, 0xab, 0xab, 0xab, 0xab, 0xab]);
const RED_AND_HALF_GREEN = () => ({ width: 2, height: 1, rgba: Uint8Array.from([255, 0, 0, 255, 0, 255, 0, 128]) });

function decodePng(png) { // IHDR's size and the filtered rows of a PNG this module wrote
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  let at = 8, w, h, idat = [];
  while (at < png.length) {
    const len = png.readUInt32BE(at), type = png.toString("latin1", at + 4, at + 8), data = png.subarray(at + 8, at + 8 + len);
    if (type === "IHDR") { w = data.readUInt32BE(0); h = data.readUInt32BE(4); assert.deepEqual([...data.subarray(8)], [8, 6, 0, 0, 0]); }
    if (type === "IDAT") idat.push(data);
    at += 12 + len;
  }
  return { w, h, rows: [...inflateSync(Buffer.concat(idat))] };
}

test("a PNG holds the pixels, row by row, each row unfiltered", () => {
  const { w, h, rows } = decodePng(encodePng(2, 1, RED_AND_HALF_GREEN().rgba));
  assert.deepEqual([w, h, rows], [2, 1, [0, 255, 0, 0, 255, 0, 255, 0, 128]]);
});

test("a KTX2 texture becomes a PNG image; the model's other data stays byte for byte", async () => {
  const seen = [];
  const out = await convertGlb(MODEL(), (ktx) => { seen.push([...ktx]); return RED_AND_HALF_GREEN(); });
  assert.deepEqual(seen, [[0xab, 0xab, 0xab, 0xab, 0xab]]);
  const { json, bin } = readGlb(out);
  assert.equal(json.images[0].mimeType, "image/png");
  assert.deepEqual(json.textures[0], { sampler: 0, source: 0 });
  assert.deepEqual(json.extensionsUsed, ["KHR_materials_ior"]);
  assert.equal(json.extensionsRequired, undefined);
  const view = (k) => bin.subarray(json.bufferViews[k].byteOffset, json.bufferViews[k].byteOffset + json.bufferViews[k].byteLength);
  assert.deepEqual([...view(0)], POSITIONS);
  assert.deepEqual(decodePng(view(1)).rows, [0, 255, 0, 0, 255, 0, 255, 0, 128]);
  assert.equal(json.buffers[0].byteLength, bin.length);
  assert.equal(json.bufferViews[1].byteOffset % 4, 0);
});

test("a model without KTX2 textures comes out unchanged", async () => {
  const plain = glb({ asset: { version: "2.0" }, bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 12 }],
                      buffers: [{ byteLength: 12 }] }, POSITIONS);
  const out = await convertGlb(plain, () => assert.fail("nothing to transcode"));
  assert.deepEqual(readGlb(out).json, readGlb(plain).json);
  assert.deepEqual([...readGlb(out).bin.subarray(0, 12)], POSITIONS);
});

const HSSD_OBJECT = "data/versioned_data/hssd-hab/objects/0/00a896b002a685b0366798b1d3a96a20e96cf560.glb";
test("a real HSSD model's textures all become PNG with the vendored Basis transcoder",
  { skip: !existsSync(HSSD_OBJECT) && "HSSD is not downloaded" }, async () => {
    const out = await convertGlb(readFileSync(HSSD_OBJECT), await basisTranscoder());
    const { json } = readGlb(out);
    assert.ok(json.images.length > 0 && json.images.every((i) => i.mimeType !== "image/ktx2"));
    assert.ok(!(json.extensionsUsed || []).includes("KHR_texture_basisu"));
  });
