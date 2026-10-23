"""Connecting to an MQTT broker with paho-mqtt (EPL-2.0 or BSD-3-Clause)."""
from __future__ import annotations

import threading

import paho.mqtt.client as mqtt


def connect(host: str, port: int, client_id: str, timeout: float = 10.0) -> mqtt.Client:
    """A connected client with its network loop running on a background thread."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    ready = threading.Event()
    client.on_connect = lambda c, userdata, flags, reason, props: ready.set() if not reason.is_failure else None
    client.connect(host, port)
    client.loop_start()
    if not ready.wait(timeout):
        client.loop_stop()
        raise ConnectionError(f"no MQTT connection to {host}:{port} within {timeout:.0f} s")
    return client


def disconnect(client: mqtt.Client) -> None:
    client.disconnect()
    client.loop_stop()
