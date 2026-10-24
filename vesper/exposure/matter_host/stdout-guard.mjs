// Imported first by host.mjs. matter.js logs through console.debug/info/log, which Node writes to stdout, but
// stdout carries the host's JSON-lines protocol. Everything else written to stdout goes to stderr (the host's
// log) instead; `send` writes protocol messages to the real stdout.
import { format } from "node:util";

const write = process.stdout.write.bind(process.stdout);
for (const name of ["log", "info", "debug", "trace"]) {
  console[name] = (...args) => process.stderr.write(format(...args) + "\n");
}
process.stdout.write = (chunk, ...rest) => process.stderr.write(chunk, ...rest);

/** One protocol message as one line on the real stdout. */
export const send = (message) => write(JSON.stringify(message) + "\n");
