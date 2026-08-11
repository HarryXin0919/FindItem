# Controller Provisioning

Five identities: `CTRL-01` ... `CTRL-05`.

**One firmware source, five configurations.** Do not fork the sketch. Identity
is a build-time flag, nothing else differs between the nodes.

## Identity selection

The only thing that changes between the five controllers is one define:

```
-DFINDIT_CONTROLLER_INDEX=<1..5>
```

Everything else is derived from it in `Config.h`, so a typo cannot put two
nodes on the same drawer range:

```c
GLOBAL_DRAWER_START = (INDEX - 1) * 10 + 1
GLOBAL_DRAWER_END   = GLOBAL_DRAWER_START + 9
CONTROLLER_ID       = "CTRL-0" INDEX
```

An index outside 1-5 is a compile error (`the topology is locked by ADR-001`).

| Build flag | Identity | Drawers | Local LED | Command topic |
|---|---|---|---|---|
| `-DFINDIT_CONTROLLER_INDEX=1` | CTRL-01 | 1-10 | 0-9 | `findit/controllers/CTRL-01/command` |
| `-DFINDIT_CONTROLLER_INDEX=2` | CTRL-02 | 11-20 | 0-9 | `findit/controllers/CTRL-02/command` |
| `-DFINDIT_CONTROLLER_INDEX=3` | CTRL-03 | 21-30 | 0-9 | `findit/controllers/CTRL-03/command` |
| `-DFINDIT_CONTROLLER_INDEX=4` | CTRL-04 | 31-40 | 0-9 | `findit/controllers/CTRL-04/command` |
| `-DFINDIT_CONTROLLER_INDEX=5` | CTRL-05 | 41-50 | 0-9 | `findit/controllers/CTRL-05/command` |

## Config records

`09_Code/firmware/controller_configs/CTRL-0x.json` is the provisioning record
for each node. Each stores `controller_id`, `controller_index`, global drawer
start/end, `led_count`, `mqtt_base_topic` plus the four resolved topics,
`fw_version`, `config_version`, `build_flag`, and the pin/address baseline.

These records are the source of truth for what should be flashed onto which
board. They contain **no credentials**.

## Secret strategy

Credentials never enter the repository (AGENTS.md "Secrets", MQTT contract
section 4).

1. `Secrets.h.example` is the committed template.
2. Copy it locally:
   ```powershell
   cd 09_Code/firmware/FindIt_Controller_Node
   copy Secrets.h.example Secrets.h
   ```
3. Fill in `FINDIT_WIFI_SSID`, `FINDIT_WIFI_PASSWORD`, `FINDIT_MQTT_HOST`,
   `FINDIT_MQTT_PORT`, `FINDIT_MQTT_USERNAME`, `FINDIT_MQTT_PASSWORD`.
4. `Secrets.h` is git-ignored.

If `Secrets.h` is absent the firmware still **compiles**, using the
placeholders in `Config.h`, and reports `provisioned: NO (placeholders)` on the
serial banner and `"provisioned": false` in its status payload. It never
pretends to be provisioned when it is not.

Serial diagnostics print *whether* the build was provisioned - never the values.

## Build and flash

Compile only (no board attached):

```powershell
cd 09_Code/firmware
arduino-cli compile --fqbn esp32:esp32:esp32c3 `
  --build-property "compiler.cpp.extra_flags=-DFINDIT_CONTROLLER_INDEX=1" `
  FindIt_Controller_Node
```

Flash one node (S12 onward, board attached):

```powershell
arduino-cli upload --fqbn esp32:esp32:esp32c3 -p COM<n> FindIt_Controller_Node
```

Change only the number in the flag for each of the five boards. Label each
physical board with its `CTRL-0x` identity as it is flashed - the firmware is
otherwise identical and the boards are indistinguishable.

## Provisioning order (S12-S16)

1. Flash and fully validate **CTRL-01** alone (S12-S15).
2. Only then flash CTRL-02..CTRL-05 with the same source and their own index (S16).
3. Verify with S17 that a locate for a drawer in one node's range lights nothing
   on the other four.

Because the five configs are identical apart from identity and range, a wiring
mistake will **not** show up as a config difference - it must be caught
physically (S10 finding R5).

## Modules

| Module | File | Responsibility |
|---|---|---|
| Configuration / identity | `Config.h` | index, derived range, topics, pins, timings, secret strategy |
| Wi-Fi connection manager | `WifiManager.*` | non-blocking connect and retry |
| MQTT transport | `MqttTransport.*` | subscribes to this node's command topic **only**; publishes ack/status/heartbeat |
| MCP23017 I/O | `Mcp23017Io.*` | register-level I2C, inputs with pull-ups, debounce |
| WS2812 LED service | `Ws2812Service.*` | ten pixels on one data pin, non-blocking locate animation with auto-off |
| Command parsing / idempotency | `CommandParser.*` | validation in the same order as the simulator; bounded seen-id window |
| Heartbeat / status | `MqttTransport.*` + `.ino` loop | periodic liveness and state |
| Diagnostics | `Diagnostics.*` | serial output, never a credential |

The command validation order in `CommandParser.cpp` is deliberately identical
to `SimulatedController.handle_command` in the simulator, rejection messages
included. If the two ever drift, the software proof from S01-S10 stops
transferring to the physical node (S10 finding F18).
