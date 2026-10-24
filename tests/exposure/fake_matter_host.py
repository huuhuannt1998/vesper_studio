"""A stand-in for vesper/exposure/matter_host/host.mjs that speaks the same JSON-lines protocol, for the Host tests.

Test-only ops: `env` (returns the MATTER_* variables), `sleep`, `fail`, `die` and `emit` (sends an event first).
"""
import json
import os
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


send({"event": "ready", "matter_js": "fake"})
for line in sys.stdin:
    req = json.loads(line)
    op, rid = req.get("op"), req.get("id")
    if op == "add":
        names = [f["name"] for f in req["features"]]
        send({"id": rid, "ok": True, "resolved": names, "unresolved": [],
              "pairing": {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}})
    elif op in ("set", "start", "remove"):
        send({"id": rid, "ok": True})
    elif op == "env":
        send({"id": rid, "ok": True, "env": {k: v for k, v in os.environ.items() if k.startswith("MATTER_")}})
    elif op == "sleep":
        time.sleep(req["seconds"])
        send({"id": rid, "ok": True})
    elif op == "fail":
        send({"id": rid, "ok": False, "error": "boom"})
    elif op == "die":
        sys.exit(3)
    elif op == "emit":
        send(req["event"])
        send({"id": rid, "ok": True})
    elif op == "shutdown":
        send({"id": rid, "ok": True})
        sys.exit(0)
    else:
        send({"id": rid, "ok": False, "error": f"unknown op {op!r}"})
