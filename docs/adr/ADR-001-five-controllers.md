# ADR-001 - Five Distributed Cabinet Controllers

**Status:** Accepted

## Decision
Use five identical controller modules:

`(ESP32-C3 + MCP23017 + 10 x WS2812) x 5`

rather than one ESP32 for all 50 drawers or one ESP32 per drawer.

## Rationale
This preserves manageable wiring groups while avoiding the provisioning and Wi-Fi overhead of 50 independent nodes. Each controller services exactly ten drawer indicators and one local MCP23017 expansion domain.

## Consequences
- Five Wi-Fi/MQTT clients.
- Five controller IDs.
- One shared firmware source.
- Five configuration records.
- Cross-controller routing must be tested.
- A failed controller affects at most ten drawer channels.
