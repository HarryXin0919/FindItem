# FindItem backend — Java 8 / Spring Boot 2.7

Same backend as [`../backend-java`](../backend-java), but kept **Java 8 compatible**
so it builds and runs on an existing JDK 8 install without upgrading the toolchain.
Identical REST + MQTT contract; shares `../config/items.json` and `../frontend/index.html`.

**What differs from the Java 17 version** (behaviour is the same):

| Java 17 version | This Java 8 version |
|---|---|
| `record SearchEvent(...)` | plain POJO with getters/setters |
| `jakarta.annotation.*` | `javax.annotation.*` |
| Spring Boot 3.2 | Spring Boot 2.7.18 (last line supporting Java 8) |
| `instanceof` pattern, `var` | classic casts, explicit generics |

## Run

> Requires JDK 8+ and Maven. Start Mosquitto first.

```bash
cd backend-java8
mvn spring-boot:run
```

Or build a jar:

```bash
mvn clean package
java -jar target/finditem-backend-java8-0.1.0.jar
```

Listens on port **8443**, serves the frontend at `/`. Config via the same env vars
as the other backends (`MQTT_HOST/PORT/USER/PASS`, `FINDITEM_CATALOG`,
`FINDITEM_FRONTEND`).

## API & MQTT

Identical to the Python and Java 17 backends — see the repo root `README.md`.
