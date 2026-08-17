#!/usr/bin/env node
/** Build the exact ESP Emote GFX mmap pack without relying on browser downloads. */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_ROOT = path.dirname(fileURLToPath(import.meta.url));
const BOARD_ROOT = path.resolve(TOOL_ROOT, "..");
const ASSET_ROOT = path.join(BOARD_ROOT, "emote_lab");
const SPEC_PATH = path.join(ASSET_ROOT, "source", "hensun_emote_motion_spec.json");
const WASM_PATH = path.join(TOOL_ROOT, "vendor", "eaf_converter_bg-gkbc_Rvp.wasm");
const OUTPUT_PATH = path.join(ASSET_ROOT, "hensun_emote_lab_v1.bin");
const EXPECTED_WASM_SHA256 = "c3e1d8af3651df97188356ef7f1a42ab871928d9ca06341626cdbccb4437205d";
const MMAP_HEADER_SIZE = 12;
const MMAP_NAME_FIELD_SIZE = 16;

let wasm;
let memoryCache;
let textDecoder = new TextDecoder("utf-8", { ignoreBOM: true, fatal: true });

function memoryBytes() {
  if (!memoryCache || memoryCache.byteLength === 0) {
    memoryCache = new Uint8Array(wasm.memory.buffer);
  }
  return memoryCache;
}

function decodeWasmString(pointer, length) {
  return textDecoder.decode(memoryBytes().subarray(pointer >>> 0, (pointer >>> 0) + length));
}

function takeExternref(index) {
  const value = wasm.__wbindgen_export_0.get(index);
  wasm.__externref_table_dealloc(index);
  return value;
}

async function initializeConverter() {
  const wasmBytes = fs.readFileSync(WASM_PATH);
  const digest = crypto.createHash("sha256").update(wasmBytes).digest("hex");
  if (digest !== EXPECTED_WASM_SHA256) {
    throw new Error(`EAF converter checksum mismatch: ${digest}`);
  }
  const imports = {
    wbg: {
      __wbindgen_init_externref_table() {
        const table = wasm.__wbindgen_export_0;
        const start = table.grow(4);
        table.set(0, undefined);
        table.set(start, undefined);
        table.set(start + 1, null);
        table.set(start + 2, true);
        table.set(start + 3, false);
      },
      __wbindgen_string_new(pointer, length) {
        return decodeWasmString(pointer, length);
      },
      __wbindgen_throw(pointer, length) {
        throw new Error(decodeWasmString(pointer, length));
      },
    },
  };
  const result = await WebAssembly.instantiate(wasmBytes, imports);
  wasm = result.instance.exports;
  wasm.__wbindgen_start();
}

function convertGif(gifBytes) {
  const pointer = wasm.__wbindgen_malloc(gifBytes.length, 1) >>> 0;
  memoryBytes().set(gifBytes, pointer);
  const options = wasm.wasmconvertoptions_new() >>> 0;
  wasm.wasmconvertoptions_set_enable_rle(options, true);
  wasm.wasmconvertoptions_set_enable_jpeg(options, true);
  wasm.wasmconvertoptions_set_enable_huffman(options, true);
  wasm.wasmconvertoptions_set_enable_heatshrink(options, false);
  wasm.wasmconvertoptions_set_enable_raw(options, false);
  wasm.wasmconvertoptions_set_jpeg_quality(options, 80);
  wasm.wasmconvertoptions_set_resize(options, 320, 240);

  const result = wasm.convert_gif_wasm(pointer, gifBytes.length, options);
  if (result[3]) {
    throw takeExternref(result[2]);
  }
  const output = memoryBytes().subarray(result[0], result[0] + result[1]).slice();
  wasm.__wbindgen_free(result[0], result[1], 1);
  return output;
}

function buildMmapPack(files) {
  const encoder = new TextEncoder();
  const records = [];
  let dataOffset = 0;
  for (const file of files) {
    const nameBytes = encoder.encode(file.fileName);
    if (nameBytes.length > MMAP_NAME_FIELD_SIZE) {
      throw new Error(`asset name exceeds ${MMAP_NAME_FIELD_SIZE} bytes: ${file.fileName}`);
    }
    records.push({
      ...file,
      nameBytes,
      fileSize: file.data.length,
      offset: dataOffset,
    });
    dataOffset += 2 + file.data.length;
  }

  const entrySize = MMAP_NAME_FIELD_SIZE + 12;
  const table = new Uint8Array(records.length * entrySize);
  const data = new Uint8Array(dataOffset);
  records.forEach((record, index) => {
    table.set(record.nameBytes, index * entrySize);
    const view = new DataView(
      table.buffer,
      table.byteOffset + index * entrySize + MMAP_NAME_FIELD_SIZE,
      12,
    );
    view.setUint32(0, record.fileSize, true);
    view.setUint32(4, record.offset, true);
    view.setUint16(8, 0, true);
    view.setUint16(10, 0, true);
  });

  let cursor = 0;
  for (const record of records) {
    data[cursor++] = 0x5a;
    data[cursor++] = 0x5a;
    data.set(record.data, cursor);
    cursor += record.data.length;
  }

  const payload = new Uint8Array(table.length + data.length);
  payload.set(table);
  payload.set(data, table.length);
  const checksum = payload.reduce((sum, byte) => (sum + byte) & 0xffff, 0);
  const header = new Uint8Array(MMAP_HEADER_SIZE);
  const headerView = new DataView(header.buffer);
  headerView.setUint32(0, records.length, true);
  headerView.setUint32(4, checksum, true);
  headerView.setUint32(8, payload.length, true);

  const pack = new Uint8Array(header.length + payload.length);
  pack.set(header);
  pack.set(payload, header.length);
  return pack;
}

await initializeConverter();
const spec = JSON.parse(fs.readFileSync(SPEC_PATH, "utf8"));
const index = [];
const files = [];
for (const [name, animation] of Object.entries(spec.animations)) {
  const gifPath = path.join(ASSET_ROOT, "gifs", animation.export_file);
  const eaf = convertGif(fs.readFileSync(gifPath));
  const fileName = `${name}.eaf`;
  index.push({
    name,
    file: fileName,
    x: 0,
    y: 0,
    loop: [animation.loop_start_frame, animation.loop_end_frame],
    fps: spec.canvas.fps,
  });
  files.push({ fileName, data: eaf });
}

files.unshift({
  fileName: "index.json",
  data: new TextEncoder().encode(JSON.stringify(index, null, 2)),
});
const pack = buildMmapPack(files);
fs.writeFileSync(OUTPUT_PATH, pack);
const outputDigest = crypto.createHash("sha256").update(pack).digest("hex");
console.log(`packed ${index.length} animations into ${OUTPUT_PATH}`);
console.log(`size=${pack.length} sha256=${outputDigest}`);
