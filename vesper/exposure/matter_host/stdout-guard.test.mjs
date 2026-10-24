// node --test: once the guard is imported, only protocol messages reach stdout.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const guard = fileURLToPath(new URL("./stdout-guard.mjs", import.meta.url));
const child = `
  const { send } = await import(${JSON.stringify(guard)});
  console.log("log line"); console.info("info line"); console.debug("debug line");
  process.stdout.write("raw write\\n");
  send({ event: "ready" });
  console.warn("warning");
`;

test("console output goes to stderr and send() alone writes to stdout", () => {
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", child], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, '{"event":"ready"}\n');
  for (const line of ["log line", "info line", "debug line", "raw write", "warning"]) {
    assert.ok(r.stderr.includes(line), line);
  }
});
