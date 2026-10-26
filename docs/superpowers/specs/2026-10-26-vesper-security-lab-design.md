# VESPER security lab: the home network and paper-to-scenario (design)

Status: design, 2026-10-26. It replaces §6 of `2026-10-24-vesper-lab-design.md` (the security lab's first version)
and adds the home network. Sub-projects: W7a, W4a, W4b, W7b (§2).

## 0. Intent and success

A researcher gives a smart-home security paper to Claude Desktop, or to any MCP client. The model reads the paper
and, through VESPER's typed tools, sets up the paper's attack and defense in a VESPER home, runs it, and reports
claim by claim whether the paper's result was reproduced. The output is a scenario file: plain, versioned, and
rerunnable without any model. This is how VESPER shows that it can demonstrate the attacks and defenses of
research papers, and not only one.

The home has a network, as a real house has one: a router, the hub on Ethernet, Wi-Fi devices with their own
connections, a Zigbee mesh. Every device's real traffic crosses it.

Success:
1. **Paper to scenario.** Given only the Phantom-Delay paper (Fu et al., "IoT Phantom-Delay Attacks: Demystifying
   and Exploiting IoT Timeout Behaviors", DSN 2022), Claude Desktop produces through the MCP server a scenario
   that matches a hand-written gold scenario and reproduces the paper's claims. No VESPER code is written for
   Phantom-Delay itself: it is the test case, not the product.
2. **The same with a local model** through LM Studio, scored the same way, so the server is not tied to one model.
3. **The network carries the traffic.** An end-to-end test proves that each device's reports and commands cross
   its link and the relay: cutting a link silences that device in Home Assistant, and nothing else.
4. **Honesty.** What VESPER cannot express is recorded as a gap, never faked; the evaluation counts gaps.

## 1. Decisions (PI, 2026-10-26)

- The main function is the paper-to-scenario MCP server; Phantom-Delay is the example used to test it.
- Network attacks act on real traffic. The standing rule against attack tooling is changed for this scope only:
  **VESPER may run a loopback-only relay between its own services (the device bridge, its Mosquitto on 18830,
  its Home Assistant on 18123, its virtual devices) that really delays, drops, reorders, duplicates or cuts their
  traffic. Still forbidden: ARP or DNS spoofing, radio, any LAN or other host, ports 1883 and 8123, other
  people's devices, anything not started by the session.**
- The home network is emulated with both backends over one topology: link emulation on every OS (W7a), and real
  802.11 on Linux (W7b).
- Wi-Fi devices with their own MQTT connections are added, and they are virtual twins like every other device.

## 2. Sub-projects and order

| | What | Depends on |
|---|---|---|
| **W7a** | The home network by link emulation: topology, the relay, link models, Matter devices on the hub, virtual Wi-Fi (Tasmota) devices | — |
| **W4a** | The scenario engine: the file, the building blocks, defenses, predicates, runs and reports; the gold Phantom-Delay scenario | W7a |
| **W4b** | The MCP server, its prompt, its registration, and the evaluation harness | W4a |
| **W7b** | Real 802.11 on Linux: namespaces, mac80211_hwsim, wmediumd, hostapd, over the same topology | W7a; a Linux machine or a VM window |

The MCP server comes after the engine because it can only be as good as what it can set up: before W7a and W4a it
could not reproduce a network-timing paper such as Phantom-Delay.

## 3. The home network (W7a)

### 3.1 Topology

`home.yaml` gains a `network:` section. Every home has a default, so none needs writing one:

```yaml
network:
  router: {room: hall}                    # the access point and the router, at the room's centre unless xy given
  hub: {link: ethernet}                   # Home Assistant and VESPER's broker run on the hub
  coordinator: {on: hub}                  # the Zigbee coordinator: on the hub, or its own host with a link
  devices:                                # only exceptions; the default follows each product's radio
    porch_plug: {link: wifi}
```

Media: `ethernet`, `wifi`, `zigbee`, `thread`. A device's default medium is its product's radio: Zigbee products
join the coordinator's mesh, Matter-over-Wi-Fi and Tasmota products join the router's Wi-Fi. A researcher can move
the router, put the coordinator on its own Wi-Fi host (a common real setup), or change a link's parameters.

### 3.2 What crosses the network

Real connections, each through the relay (§3.4):
- **TCP:** the bridge's MQTT connection to the broker (the coordinator's link); each Wi-Fi device's own MQTT
  connection; Home Assistant's MQTT connection.
- **UDP:** each Matter node's traffic with Home Assistant's Matter server.

Real TCP retransmissions, MQTT keepalives and Matter's retransmissions therefore meet every delay. The real Home
Assistant decides when a device is unavailable.

Modelled hops (not IP): each Zigbee or Thread device's hop to the coordinator or border router, with per-hop
retries and loss, inside VESPER's process.

### 3.3 Devices on the network

- **Matter devices on the hub.** The Matter tier's nodes (plan D) are commissioned into Home Assistant's Matter
  server (the unfinished W2d), each as a Wi-Fi or Thread device.
- **Virtual Wi-Fi devices: Tasmota.** A new catalog source: Tasmota device templates (real products, each with its
  GPIO template). Each becomes a VESPER twin that speaks Tasmota's MQTT protocol and its Home Assistant discovery,
  over its own MQTT connection and its own Wi-Fi link. These devices are virtual: no hardware and no real firmware
  on any network. They live only on VESPER's loopback network. The template database's license is checked before
  it enters the catalog; a release build keeps only records whose source allows redistribution, as for the rest
  of the catalog.
- **Later:** ESPHome (its native TCP API) and Shelly (HTTP and WebSocket). They are real, and heavier to model.

### 3.4 The relay

One asyncio process per session, bound to 127.0.0.1 only. For each connection it listens on a port of its own and
forwards to the real service. Clients are configured to connect to the relay's port; services keep their own
ports. Per direction it holds a queue and applies the link's model and the attack's effects:
- delay (a fixed hold, or a distribution), drop, reorder, duplicate, throttle (bytes per second), cut (close or
  silence the connection);
- each filtered by device, direction, and message (an MQTT topic or payload field; a Matter message class).

TCP effects act on whole MQTT packets (the relay parses MQTT framing), so a held report is held intact and arrives
late on a live connection: the connection-keeping delays of Phantom-Delay, against a real broker and hub.

**Before it starts,** the relay checks every upstream against the session's own services: the broker the session
started, the Home Assistant it started, the virtual devices it started. Any other address or port, including 1883
and 8123, is refused by name.

### 3.5 Link models

- **Wi-Fi:** signal from distance and walls in the home: a log-distance path-loss model with a per-wall
  attenuation, walls taken from the home's map (or from HSSD's geometry through the map). The signal sets the
  link's latency, jitter, loss and rate. Below a threshold the device drops off, and it reconnects with a
  realistic back-off (the old VESPER measured about 11 s for a deauthenticated station to recover).
- **Ethernet:** near-perfect, configurable.
- **Zigbee and Thread:** per-hop latency, retries and loss, and routing through mains-powered routers.
- **Residents and furniture** do not attenuate in the first version.

A link's state changes with the world: carrying the router into the garage degrades the porch sensor's link, for
real.

### 3.6 The trace

The trace gains network records: link up and down, signal changes beyond a step, and every message an effect
touched (when it was sent, when it was delivered or dropped, which effect). Predicates and defenses use them, as
they use the existing ground truth of who did what.

### 3.7 Logical-clock runs

`lab day`, `lab run` and scenario sweeps on the logical clock have no sockets. The same link models and effects are
applied in-process to the same messages, on the logical clock, so a scenario runs both ways. Real timeouts are the
live mode's job; the report says which mode produced a result.

## 4. The scenario engine (W4a)

### 4.1 The file

`vesper-scenario/1`, YAML, next to a home. An example (values illustrative; the gold file takes them from the paper):

```yaml
format: vesper-scenario/1
title: A delayed door report keeps the away alarm silent
source:
  paper: {title: "IoT Phantom-Delay Attacks: Demystifying and Exploiting IoT Timeout Behaviors",
          venue: "DSN 2022", doi: "<from the paper>"}
  passages: {threat: "<quoted>", attack: "<quoted>", result: "<quoted>"}
home: ../home.yaml
setup:
  devices: [{product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: hall-porch}}]
  network: {coordinator: {link: wifi, room: hall}}
  automations: automations/away-alarm.yaml        # Home Assistant YAML, the paper's victim automation
  home_state: {occupants: none, alarm: armed_away}
timeline:
  - {at: "22:00:00", actor: intruder, do: {open: hall-porch}, cites: [attack]}
attack:
  - {at: "21:59:30", vantage: network, effect: delay, link: coordinator, direction: up,
     match: {device: front_door, field: contact}, hold_s: 40, keep_connection: true, cites: [attack]}
defenses:
  - {kind: freshness, max_age_s: 10, action: alert}
expect:
  - claim: the alarm sounds only after the intruder is inside
    predicate: {before: [{actor: intruder, enters: hall}, {automation: away_alarm, fired: true}]}
    conditions: {baseline: false, attack: true, attack+defense: false}
    cites: [result]
runs: {mode: live, repeat: 5, conditions: [baseline, attack, attack+defense]}
gaps:
  - {what: "the cloud leg of the paper's platforms", why: "VESPER has no cloud; the attack is reproduced on the hub leg"}
```

### 4.2 Building blocks

| Vantage | Steps |
|---|---|
| network | `delay`, `drop`, `reorder`, `duplicate`, `throttle`, `cut`; on a link, a direction and a message filter; `keep_connection` for delays inside a timeout |
| broker client | `forge` a report on a device's topic, `inject` a command on its `/set` topic, `replay` captured messages |
| compromised device | `rewrite` its reports, `ignore` or `delay` its commands |
| hub | `install` a malicious automation; use a `stolen_token` on Home Assistant's REST API |
| physical | an intruder's actions in the world; `tamper` with a device (cover, remove, move) |

Each step is typed and validated: it names devices, links, doors and automations of the home, never a raw host,
port or command.

### 4.3 Defenses

| Defense | What it does |
|---|---|
| `broker_acl` | broker authentication and per-client ACLs: only the bridge and each device publish their own topics; Home Assistant only commands |
| `physical_consistency` | every report is compared with the world's ground truth (did anyone open the door); a report with no cause raises an alert. Only a simulator can offer it |
| `freshness` | reports carry the time they were made; one older than a bound on arrival is flagged or dropped |
| `automation_hardening` | conditions added to the victim automations (a second sensor, a state check) |
| `timeouts` | Home Assistant's and the broker's keepalive and availability settings |

### 4.4 Predicates

A small typed language over the trace: events (a device report and its value, a command, an automation fired, an
actor's action or room, an alert, a link event, a held message), `before`/`after`/`within` a time, the state of a
device at a time, and counts. A predicate is evaluated per run; the report gives the trace records that decide it.

### 4.5 Runs and the report

`vesper lab scenario check|run|report <file>`. A run plays each condition (baseline, attack, attack + defense)
`repeat` times, live or logical, each with its own trace. The report gives, per claim and condition: holds or not,
in how many runs, the deciding records, and whether the paper's expectation was **reproduced**. The gaps are
listed first.

### 4.6 The gold Phantom-Delay scenario

Written by hand from the paper, checked with the PI, kept in `examples/lab/scenarios/phantom-delay/`. It is the
reference the MCP evaluation scores against, and the first live run of the lab.

## 5. The MCP server (W4b)

### 5.1 Server

`vesper mcp`: a stdio MCP server on the official Python SDK, started by Claude Desktop, LM Studio or any MCP
client. It is a thin layer: each tool calls the scenario engine's own functions, as the command line does. VESPER
Studio's setup offers to register it with Claude Desktop, with the user's consent.

### 5.2 Tools

| Tool | What it does |
|---|---|
| `capabilities` | the vocabulary: vantage points, steps and their parameters, defenses, predicate forms, run modes, and the limits |
| `homes`, `home_describe` | the homes, and one home's rooms, devices, network and automations |
| `catalog_search` | products to add, from the catalog |
| `scenario_draft`, `scenario_edit`, `scenario_validate` | build the file piece by piece; every edit is validated and an error names the nearest valid choice |
| `cite` | attach the paper and the quoted passage behind an element |
| `gap` | record what cannot be expressed, and why |
| `scenario_save`, `scenario_run`, `run_status`, `run_report` | save, run the conditions, and read the claim-by-claim report |

No tool takes a host, a port, a shell command or code. The model cannot write or run code.

### 5.3 The prompt

`reproduce_paper` guides the model in order: the threat model; the preconditions and the victim setup; the attack
steps; the defense; the claims as predicates; the gaps; validate; run; explain the result to the researcher, who
reviews and corrects it in the conversation.

### 5.4 Evaluation

The Phantom-Delay PDF goes to Claude Desktop, and to a local model through LM Studio, with only `reproduce_paper`.
Each draft is scored against the gold scenario by a script:
- **fields:** setup, attack, defense and claims match the gold file (element by element);
- **reproduction:** the run reproduces the paper's claims;
- **gaps:** honestly flagged against faked;
- **effort:** turns and corrections.

Later papers add a gold file and a row.

## 6. Real 802.11 (W7b)

The same `network:` topology driven into Linux network namespaces: mac80211_hwsim radios, wmediumd's channel with
the link model's path loss, hostapd for the router, wpa_supplicant for each Wi-Fi device, veth pairs for
Ethernet. The virtual devices and the hub run in their namespaces; the relay's effects become `tc netem` and
nftables rules on the namespace links. It runs on Linux, or in a Linux VM on macOS (WSL2's kernel lacks
mac80211_hwsim). It is optional: a scenario names the backend it needs, and the report says which ran.

## 7. Safety boundary

- The relay binds 127.0.0.1 only and forwards only to services the session started; any other target is refused
  before a run starts (W7b: inside the session's own namespaces only).
- No ARP or DNS spoofing, no radio, no LAN, no other host, never ports 1883 or 8123, never anyone else's device.
- Every device is a virtual twin.
- Scenarios name devices, links, doors and automations, never addresses; the model cannot write or run code.

## 8. Testing

- Unit: the topology and its defaults, the link models, the relay's effects on real sockets on loopback, MQTT
  framing, the scenario schema, every building block, every predicate form, the report.
- End-to-end: each device's traffic crosses its link and the relay (cut one link: only that device goes
  unavailable in Home Assistant). This is the old VESPER's failure, turned into a test.
- The MCP tools through a real MCP client session.
- Opt-in live: Home Assistant in the loop, real timing (as `VESPER_STACK_TESTS` and `VESPER_HABITAT` now).
- The gold Phantom-Delay scenario reproduces its claims live.

## 9. Risks, settled first by spikes

1. **Matter through the relay.** Home Assistant's Matter server finds nodes by mDNS. The relay needs the nodes to
   advertise the relay's port. If matter.js cannot, the effects are applied at the node's own socket instead.
2. **The Tasmota template database's license** before it enters the catalog.
3. **Home Assistant's MQTT and availability timeouts** under held packets: measured live first, so the Phantom-Delay
   claims are tested against what the real hub does.
4. **Wall clock and the session's clock thread** with the relay in the loop (latency budget, ordering).

## 10. Out of scope

Cloud platforms (a paper's cloud leg is reproduced on the hub leg, and the scenario says so); attacks on anything
outside the session; radio emulation on macOS and Windows (W7b is Linux); model-written attack code; several
papers' gold files before the Phantom-Delay one works end to end.
