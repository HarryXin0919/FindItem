# FindItem backend — Java / Spring Boot

A drop-in alternative to the Python/FastAPI backend in `../backend/`. It exposes the
**same REST API** and speaks the **same MQTT command/status contract**, and reads the
shared `../config/items.json` and serves the shared `../frontend/index.html`. Run
either backend against the same firmware, broker, and frontend — pick one.

## Stack

Java 17 · Spring Boot 3.2 (Web) · Eclipse Paho MQTT client · Jackson.

## Run

> Requires JDK 17+ and Maven. Start Mosquitto first (see the repo root README /
> `scripts/init-mqtt-passwd.ps1`).

```bash
cd backend-java
mvn spring-boot:run
```

Or build a jar:

```bash
mvn clean package
java -jar target/finditem-backend-0.1.0.jar
```

The backend listens on port **8443** and serves the frontend at `/`.

### Config (env vars or `application.yml`)

| Key | Default | Purpose |
|---|---|---|
| `MQTT_HOST` | `127.0.0.1` | broker host |
| `MQTT_PORT` | `1883` | broker port |
| `MQTT_USER` | `findit_backend` | broker user |
| `MQTT_PASS` | `findit123` | broker password (**placeholder — change it**) |
| `FINDITEM_CATALOG` | `../config/items.json` | part catalog (hot-reloaded) |
| `FINDITEM_FRONTEND` | `../frontend/index.html` | served at `/` |

To serve HTTPS like the Python backend, uncomment the `server.ssl` block in
`application.yml` and point it at the PEM cert from `scripts/gen-certs.ps1`.

## API & MQTT

Identical to the Python backend — see the repo root `README.md` for the full REST
table and MQTT topic/payload reference. In short:

- `GET /api/items` · `POST /api/search-events` · `GET /api/devices` ·
  `GET /api/devices/{id}` · `GET /api/events?limit=N`
- publishes `findit/device/{id}/command`, subscribes `findit/device/+/status`

## Layout

```
backend-java/
├── pom.xml
└── src/main/
    ├── java/dev/finditem/
    │   ├── FindItemApplication.java   app entry
    │   ├── model/SearchEvent.java     request body
    │   ├── catalog/ItemCatalog.java   items.json reader (hot reload)
    │   ├── mqtt/FindItBridge.java     MQTT client, device state, event log
    │   └── web/
    │       ├── ApiController.java     REST endpoints
    │       └── WebController.java     serves the frontend at /
    └── resources/application.yml      config
```
