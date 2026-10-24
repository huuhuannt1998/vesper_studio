"""Zigbee captures read through tshark: one Frame per decrypted APS data frame, plus the decryption rate.

tshark decrypts with the published network key and verifies each frame's MIC; a frame it could not
decrypt is counted (`secured` minus `decrypted`, also per PAN), never silently dropped. A frame whose FCS
is known bad is counted and never used. Only APS data frames carry the per-device APS counter that reveals
sniffer loss, so APS acknowledgements and NWK commands are counted but not returned.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

FIELDS = ("frame.time_epoch", "zbee_nwk.src", "zbee_nwk.seqno", "zbee_nwk.security", "zbee.sec.decryption_key",
          "zbee_aps.type", "zbee_aps.counter", "zbee_aps.cluster", "@zbee_zcl", "zbee_aps.src", "wpan.dst_pan",
          "wpan.fcs_ok")
KEY_LABEL = "vesper"
_KEY = re.compile(r"^[0-9a-fA-F]{32}$")


class CaptureError(RuntimeError):
    """tshark could not read a capture, or was asked to with an invalid key."""


@dataclass(frozen=True)
class Frame:
    t: float  # capture time, seconds since the epoch
    src: int  # NWK source: the originating device, not the hop that relayed it
    nwk_seq: int
    aps_counter: int
    cluster: int
    zcl: bytes | None  # the decrypted ZCL frame, if the APS payload is ZCL
    endpoint: int | None = None  # the APS source endpoint


@dataclass
class Capture:
    frames: list[Frame]
    secured: int  # NWK frames with security on (bad-FCS frames excluded)
    decrypted: int  # of those, the ones a configured key decrypted and authenticated
    pans: dict[int | None, list[int]] = field(default_factory=dict)  # destination PAN -> [secured, decrypted]
    bad_fcs: int = 0  # NWK frames whose FCS check failed: counted, never used


def tshark_command(path: str | Path, key_hex: str, *, byte_order: str = "Normal", tshark: str = "tshark") -> list[str]:
    if not _KEY.match(key_hex or ""):
        raise CaptureError(f"a Zigbee network key is 32 hex digits, got {key_hex!r}")
    if byte_order not in ("Normal", "Reverse"):
        raise CaptureError(f"byte order is Normal or Reverse, got {byte_order!r}")
    uat = f'uat:zigbee_pc_keys:"{key_hex.upper()}","{byte_order}","{KEY_LABEL}"'
    cmd = [tshark, "-n", "-r", str(path), "-o", uat, "-T", "fields", "-E", "separator=/t", "-E", "occurrence=f"]
    for name in FIELDS:
        cmd += ["-e", name]
    return cmd


def parse_fields(lines: Iterable[str]) -> Capture:
    frames, secured, decrypted, bad_fcs = [], 0, 0, 0
    pans: dict[int | None, list[int]] = {}
    for line in lines:
        cells = line.rstrip("\n").split("\t")
        if len(cells) < len(FIELDS):
            cells += [""] * (len(FIELDS) - len(cells))
        t, src, seq, security, key, aps_type, counter, cluster, zcl, endpoint, pan, fcs_ok = cells[:len(FIELDS)]
        if not src:
            continue  # not a Zigbee NWK frame (MAC acknowledgement, beacon, other traffic)
        if fcs_ok == "False":
            bad_fcs += 1
            continue
        if security == "True":
            counts = pans.setdefault(int(pan, 16) if pan else None, [0, 0])
            secured += 1
            counts[0] += 1
            if key == KEY_LABEL:
                decrypted += 1
                counts[1] += 1
        if aps_type and int(aps_type, 16) == 0 and counter and cluster:
            frames.append(Frame(float(t), int(src, 16), int(seq), int(counter), int(cluster, 16),
                                bytes.fromhex(zcl.replace(":", "")) if zcl else None,
                                int(endpoint, 0) if endpoint else None))
    return Capture(frames, secured, decrypted, pans, bad_fcs)


def read_capture(path: str | Path, key_hex: str, *, byte_order: str = "Normal", tshark: str = "tshark",
                 run: Callable = subprocess.run) -> Capture:
    cmd = tshark_command(path, key_hex, byte_order=byte_order, tshark=tshark)
    try:
        r = run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise CaptureError(f"cannot run tshark: {e}") from e
    if r.returncode != 0:
        raise CaptureError(f"tshark failed on {path}: {r.stderr.strip()[:500]}")
    return parse_fields(r.stdout.splitlines())
