"""A miniature ZIOTP2025 checkout: Device_Name.txt, empty capture files and a fake tshark reader."""
import struct

from vesper.validation.capture import Capture, Frame

KEY_A, KEY_B = "2fd6b1b929e883d274478f3a2bf8d4ef", "60ca4507229ff82271c615339e526146"
NAMES = (f"Topology A\n0x7150\tSonoff Temperature\tkey {KEY_A}\t(prev 0xd8ee)\n0x772c\tAqara Door\tkey {KEY_A}\n\n"
         f"Topology B\n0x6a5e\tSonoff Temperature\tkey {KEY_B}\n---\n192.168.8.116\tHomeAssistant\n")


def temp(t, counter, raw, src=0x7150):
    return Frame(t, src, counter, counter, 0x0402, struct.pack("<BBBHBh", 0x18, counter, 0x0A, 0, 0x29, raw))


def battery(t, counter, pct, src=0x772C):
    return Frame(t, src, counter, counter, 0x0001, struct.pack("<BBBHBB", 0x18, counter, 0x0A, 0x21, 0x20, pct))


def tree(tmp_path, files: dict[str, list[Frame]], names: str = NAMES):
    data = tmp_path / "Data"
    data.mkdir()
    (data / "Device_Name.txt").write_text(names)
    for rel in files:
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_bytes(b"")
    return tmp_path


def reader_for(files: dict[str, list[Frame]], decrypted: dict | None = None):
    calls = []

    def read(path, key, byte_order="Normal"):
        rel = "/".join(path.parts[-3:])
        calls.append((rel, key, byte_order))
        frames = files[rel]
        rate = (decrypted or {}).get((rel, byte_order), 1.0)
        return Capture(frames if rate else [], secured=len(frames), decrypted=int(rate * len(frames)))
    read.calls = calls
    return read


GOOD = [temp(1_760_000_000.0 + 900 * i, i + 1, 2000) for i in range(25)]  # heartbeats every 900 s
AQARA = [battery(1_760_000_100.0 + 3000 * i, 100 + i, 100) for i in range(25)]  # its own schedule
