"""Connecting to an MQTT broker with paho-mqtt (EPL-2.0 or BSD-3-Clause)."""
from __future__ import annotations

import threading

import paho.mqtt.client as mqtt


def connect(host: str, port: int, client_id: str, timeout: float = 10.0,
            will: tuple[str, str] | None = None, keepalive: int = 60, retry_s: int | None = None) -> mqtt.Client:
    """A connected client with its network loop running on a background thread. `will` is a retained
    (topic, payload) the broker publishes if this client vanishes without disconnecting; the broker notices that
    after 1.5 `keepalive`s of silence. A lost connection is retried, at most `retry_s` apart."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if will:
        client.will_set(will[0], will[1], qos=1, retain=True)
    if retry_s:
        client.reconnect_delay_set(1, retry_s)
    ready = threading.Event()
    client.on_connect = lambda c, userdata, flags, reason, props: ready.set() if not reason.is_failure else None
    client.connect(host, port, keepalive=keepalive)
    client.loop_start()
    if not ready.wait(timeout):
        client.loop_stop()
        raise ConnectionError(f"no MQTT connection to {host}:{port} within {timeout:.0f} s")
    return client


def connect_async(host: str, port: int, client_id: str, will: tuple[str, str] | None = None, keepalive: int = 60,
                  retry_s: int | None = None) -> mqtt.Client:
    """A client that connects once its loop starts (`client.loop_start()`), on its own thread, never blocking the
    caller, and retries (at most `retry_s` apart) until it is connected, and whenever it loses the broker."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if will:
        client.will_set(will[0], will[1], qos=1, retain=True)
    if retry_s:
        client.reconnect_delay_set(1, retry_s)
    client.connect_async(host, port, keepalive=keepalive)
    return client


def disconnect(client: mqtt.Client) -> None:
    client.disconnect()
    client.loop_stop()
