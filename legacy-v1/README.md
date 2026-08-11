# legacy-v1 — the single-device prototype

This is the **previous generation** of FindItem, kept intact and still built by
CI. The current system lives at the repository root; see the top-level
[README](../README.md).

## Why it is here and not deleted

The two generations are not compatible, so they cannot share a tree:

| | legacy-v1 | current (root) |
|---|---|---|
| Devices | one ESP32 per bin, LED + buzzer + stop button | 5 × (ESP32-C3 + MCP23017 + 10 × WS2812) = 50 drawers |
| MQTT topics | `findit/device/{id}/command` | `findit/controllers/CTRL-0x/{command,ack,status,heartbeat}` |
| Catalog | `config/items.json`, re-read per request | PostgreSQL — items, drawers, controllers, commands, device events |
| Backends | FastAPI + Spring Boot 3 (JDK 17) + Spring Boot 2.7 (JDK 8) | FastAPI |
| Frontend | single static `index.html` | React + Vite |

The Java backends in particular have no equivalent in the current system, which
is why the whole generation is preserved rather than only its Python half.

## Why the whole directory moved together

Every path inside this generation is relative to *its own* root. The Java
backends resolve their catalog and frontend as `../config/items.json` and
`../frontend/index.html`; the Python backend resolves
`Path(__file__).parents[2] / "config" / "items.json"`.

Had `backend-java/` been left at the repository root, `../frontend/index.html`
would have silently resolved to the **current** React shell — a wrong answer
with no error. Keeping the generation in one directory makes every one of those
relative paths land where it always did.

## Running it

Everything below is run from **this** directory, exactly as it was when it lived
at the repository root.

```bash
cd legacy-v1

# Python backend
pip install -r requirements.txt -r requirements-dev.txt
pytest
uvicorn backend.app.main:app --port 8000

# Java backends
cd backend-java  && mvn -B -ntp verify     # Spring Boot 3 / JDK 17
cd backend-java8 && mvn -B -ntp verify     # Spring Boot 2.7 / JDK 8
```

### Offline locate simulator

Preview item resolution, physical outputs, and the exact MQTT command without an
ESP32, broker, or backend:

```powershell
cd legacy-v1
python -m simulator.locate "NEO Motor"
python -m simulator.locate FINDIT-002 --no-buzzer --json
```

It reads this generation's real `config/items.json`. Its GPIO and buzzer values
mirror the v1 firmware, so it reports a per-device single-color LED rather than
pretending that build had addressable RGB pixels.

> Not to be confused with the **current** system's `simulator/` at the
> repository root, which models five controllers and fifty WS2812 pixels. The
> two are different generations and read different catalogs — this one resolves
> `parents[1] / "config" / "items.json"`, which lands inside `legacy-v1/`.

Broker config and the certificate/password helper scripts are shared with the
current system and stay at the repository root: `mosquitto/`,
`scripts/gen-certs.ps1`, `scripts/init-mqtt-passwd.ps1`.

`scripts/start-all.ps1` here is the **v1** launcher (Mosquitto + FastAPI over
HTTPS on 8443). The root `scripts/start-all.ps1` is the current one
(PostgreSQL + FastAPI + Vite).

## Status

Frozen. It is kept building so it does not rot, but new work belongs in the
current system at the repository root.
