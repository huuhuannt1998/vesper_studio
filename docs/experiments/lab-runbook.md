# Run book: VESPER Lab, live checks

The Lab's unit tests run against an in-memory broker and Home Assistant. These checks run it against VESPER's own Mosquitto and Home Assistant, so they start real services. On the shared lab Mac each one needs the PI's go and a slot agreed in the ledger: never during another project's experiment, with ports 18830, 18123 and 18080 free, `taskpolicy -b`, at most 2 cores.

## Before any check

1. Build the catalog with the current code: `vesper catalog build`. Commands refuse a catalog built by other catalog code.
2. Mosquitto is installed, and Home Assistant is set up once with `vesper setup ha` (see the device-twins run book, "Before any run").
3. For `vesper lab look`: a Chromium-based browser. Playwright's cached `chrome-headless-shell` is found first, then Google Chrome; `VESPER_BROWSER` names another.

## Lab check 1: Home Assistant in the loop

Work on a copy of the example, so that its traces and an automation edited in Home Assistant land outside the repository:

    mkdir -p ~/vesper-lab && cp -R examples/lab/test-house ~/vesper-lab/test-house
    taskpolicy -b .venv/bin/vesper lab play ~/vesper-lab/test-house/home.yaml --hub

What it does:
1. Starts VESPER's Mosquitto on 127.0.0.1:18830 and its Home Assistant on 127.0.0.1:18123.
   - Home Assistant 2026.10 keeps its HTTP settings in `.storage/http` and reads `http:` from YAML only on a first boot. So VESPER writes that store before every start, for the Lab as for the experiments: bound to 127.0.0.1:18123, and no pending trial, which Home Assistant would otherwise revert after 5 minutes by restarting on 0.0.0.0:8123.
   - The Lab's settings differ from the experiments' in one HTTP setting, that the play page may frame the dashboard (`use_x_frame_options: false`), and in loading the home's `automations.yaml` (Home Assistant's automation editor writes to it).
2. Onboards Home Assistant if needed, adds the MQTT integration, and sets the Lab login once (kept in `.vesper/ha/lab-login.json`, readable by its owner only).
3. Puts the home's Zigbee devices on the broker through the Zigbee2MQTT bridge. Matter devices are not on the hub yet (plan W2d); the list says so.
4. Opens the page at http://127.0.0.1:18080/ and prints the login and each device's Home Assistant entities.

Check, each one a pass criterion:
1. The hub panel shows Home Assistant's dashboard, and the printed login works inside the page.
2. Home Assistant lists the six devices, with the printed entities.
3. Walking (WASD) from the kitchen into the hall turns the hall's motion entity on in Home Assistant within about a second. The automation "Bedroom lamp on when someone is in the hall" fires, the bedroom lamp glows on the map, and the console shows `the hub sets bedroom_lamp state = "ON"`.
4. Switching the kitchen plug on in Home Assistant turns its icon on in the map.
5. Pressing E at the bedroom switch toggles the lamp through its binding, and Home Assistant shows it.
6. An automation edited in Home Assistant is written back to `~/vesper-lab/test-house/automations.yaml` when play stops.
7. Ctrl-C stops the page, the bridge, Home Assistant and Mosquitto, and frees the ports.
8. `vesper lab replay <the printed trace> --home ~/vesper-lab/test-house/home.yaml` says the reports are identical. A wall-clock trace is compared per device, with report times within 0.5 s.
9. The session is longer than 5 minutes, and Home Assistant is still on 127.0.0.1:18123 at the end (`lsof -nP -iTCP:18123 -sTCP:LISTEN`), never on 8123.
10. The page plays like PUBG (spec §2a):
    - two keys walk the diagonal, and Shift runs;
    - the pointer on the kitchen plug says "Click: switch kitchen_plug on" in reach, and "too far (… m)" from the hall;
    - a click on it in reach switches it, while a click on bare floor only says that WASD walks;
    - the event feed (top right) shows the automation turning the lamp on, then fades;
    - the header shows the room and the heading;
    - the residents panel shows Alex's task.

Then, for the record, the same story as an automatic look:

    taskpolicy -b .venv/bin/vesper lab look ~/vesper-lab/test-house/home.yaml --hub \
        --steps ~/vesper-lab/test-house/look-hub.yaml --out ~/vesper-lab/looks/hub-<UTC>

Pass: exit code 0, and the shots read: the lamp lit after the walk, by the automation.

Budget (estimate until the first run measures it): Home Assistant 0.5–1 GB and one core while it starts (minutes on a first start); Mosquitto and the page negligible; a headless browser about 0.3 GB for a minute.

Record in the ledger: the time, the trace path, the look's `report.md`, and anything that failed.

## When something goes wrong

- **A port is in use:** another VESPER instance, or another project's service. The Lab never shares a broker or a Home Assistant; stop the other one or wait for its slot.
- **The login fails:** delete `.vesper/ha/lab-login.json`; the next `--hub` sets a new one. A new onboarding deletes it by itself.
- **Entities are missing in Home Assistant:** check that the MQTT integration points at 127.0.0.1:18830 (Settings, Devices & services).
