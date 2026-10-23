"""An in-memory stand-in for paho's MQTT client: records publishes and injects messages."""
import json
from collections import deque
from types import SimpleNamespace

from paho.mqtt.client import topic_matches_sub


class FakeClient:
    def __init__(self):
        self.published: list[tuple[str, object, bool]] = []
        self.subscriptions: list[str] = []
        self.on_message = None

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload) if payload else None, retain))

    def subscribe(self, topic):
        self.subscriptions.append(topic)

    def deliver(self, topic, payload):
        text = payload if isinstance(payload, bytes | str) else json.dumps(payload)
        raw = text if isinstance(text, bytes) else text.encode()
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=raw))

    def last(self, topic):
        return next((p for t, p, _ in reversed(self.published) if t == topic), None)

    def retained(self, topic):
        return next((r for t, _, r in reversed(self.published) if t == topic), None)


class FakeBroker:
    """One in-memory broker shared by several clients, as Mosquitto would be: retained messages,
    wildcard subscriptions, delivery to the publisher too, and in publish order. Delivery is queued,
    so a handler that publishes is never re-entered."""

    def __init__(self):
        self.published: list[tuple[str, object, bool]] = []  # every client's publishes, decoded
        self.retained: dict[str, bytes] = {}
        self.clients: list[BrokerClient] = []
        self._queue: deque = deque()
        self._delivering = False

    def client(self, *args, **kwargs):
        c = BrokerClient(self)
        self.clients.append(c)
        return c

    def last(self, topic):
        return next((p for t, p, _ in reversed(self.published) if t == topic), None)

    def deliver(self, topic, payload):
        """A message from outside every client (for example Home Assistant sending a command)."""
        self._publish(topic, payload if isinstance(payload, str | bytes) else json.dumps(payload), False)

    def _publish(self, topic, payload, retain):
        raw = payload if isinstance(payload, bytes) else (payload or "").encode()
        self.published.append((topic, json.loads(raw) if raw else None, retain))
        if retain:
            if raw:
                self.retained[topic] = raw
            else:
                self.retained.pop(topic, None)
        for c in self.clients:
            if any(topic_matches_sub(s, topic) for s in c.subscriptions):
                self._queue.append((c, topic, raw))
        self._drain()

    def _subscribe(self, client, sub):
        for topic, raw in list(self.retained.items()):
            if topic_matches_sub(sub, topic):
                self._queue.append((client, topic, raw))
        self._drain()

    def _drain(self):
        if self._delivering:
            return
        self._delivering = True
        try:
            while self._queue:
                c, topic, raw = self._queue.popleft()
                if c.connected and c.on_message:
                    c.on_message(c, None, SimpleNamespace(topic=topic, payload=raw))
        finally:
            self._delivering = False


class BrokerClient:
    def __init__(self, broker):
        self.broker, self.subscriptions, self.on_message, self.connected = broker, [], None, True

    def publish(self, topic, payload, qos=0, retain=False):
        self.broker._publish(topic, payload, retain)

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        self.broker._subscribe(self, topic)

    def unsubscribe(self, topic):
        self.subscriptions.remove(topic)
