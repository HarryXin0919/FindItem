# Contributing to FindItem

Thanks for your interest in FindItem! This guide covers the layout, how to run each
piece, and the contract that ties them together.

## Architecture in one line

```
phone / web  ──HTTPS──▶  backend  ──MQTT──▶  Mosquitto  ──MQTT──▶  ESP32 (LED + buzzer)
```

The frontend only talks HTTPS to a backend; the backend is the only thing that
publishes MQTT commands to devices. Any one of the three interchangeable backends
implements the **same REST + MQTT contract**, so you can run whichever you prefer
against the same firmware, broker, and frontend.

## Repository layout

| Path | What it is |
|---|---|
| `backend/` | Python · FastAPI backend (`app/main.py`, `app/mqtt_bridge.py`) |
| `backend-java/` | Java 17 · Spring Boot 3.2 backend |
| `backend-java8/` | Java 8 · Spring Boot 2.7 backend (no toolchain upgrade needed) |
| `frontend/index.html` | Vanilla HTML/CSS/JS web client |
| `esp32/findit_esp32.ino` | Device firmware (Arduino / C++) |
| `config/items.json` | Part catalog (hot-reloaded by the Python backend) |
| `mosquitto/` | Broker config |
| `scripts/*.ps1` | Cert / password / launch helpers (Windows + PowerShell) |

## Running a backend

### Python (FastAPI)

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m compileall backend                      # quick syntax check
# full local run (Windows): .\scripts\start-all.ps1  (starts Mosquitto + the backend)
```

### Java 17 (Spring Boot 3.2)

```bash
cd backend-java
mvn -B compile        # build
mvn spring-boot:run   # run
```

### Java 8 (Spring Boot 2.7)

```bash
cd backend-java8
mvn -B compile        # build with a JDK 8 toolchain
mvn spring-boot:run   # run
```

## Flashing the firmware

1. Install the ESP32 board package in the Arduino IDE.
2. Install the **PubSubClient** and **ArduinoJson** libraries.
3. Open `esp32/findit_esp32.ino`, edit the config block at the top (`WIFI_SSID`,
   `WIFI_PASSWORD`, `MQTT_HOST`, a unique `DEVICE_ID` matching `config/items.json`),
   then select your board + port and upload.

## The contract (keep all backends in sync)

If you change one backend's API, update the others so the shared contract holds:

- **REST**: `GET /api/items`, `POST /api/search-events`, `GET /api/devices`,
  `GET /api/devices/{id}`, `GET /api/events?limit=N`
- **MQTT**: `findit/device/{id}/command` (backend → device),
  `findit/device/{id}/status` (device → backend)

See the API section of the [README](README.md) for the exact payloads.

## Security

Never commit secrets. `mosquitto/passwords` and the TLS keys under `certs/` are
gitignored. The `findit123` in the code is a **placeholder** — change it before any
real deployment and update both the firmware and the backend's `MQTT_PASS`.

## Conventions

- Keep the three backends behavior-compatible; a change to the REST/MQTT contract is
  a change to all of them.
- Firmware pin assignments are macros at the top of the `.ino` — document any change
  in the wiring table in the README.
