# Project Baseline

## Problem
A user searches for an item on a web dashboard. The system resolves the item to a physical drawer and illuminates the corresponding drawer LED.

## Locked physical topology
`(ESP32-C3 + MCP23017 + 10 x WS2812) x 5`

## Controller ranges
- CTRL-01 -> drawers 01-10
- CTRL-02 -> drawers 11-20
- CTRL-03 -> drawers 21-30
- CTRL-04 -> drawers 31-40
- CTRL-05 -> drawers 41-50

## MCP23017 role
The MCP23017 is local to each ESP32-C3 node and provides expandable GPIO for drawer buttons, reed switches, acknowledgement inputs and later sensor extensions.

## WS2812 role
Each ESP32-C3 directly drives one 10-pixel WS2812 chain. The MCP23017 is **not** used to generate WS2812 data.

## Software stack
- FastAPI backend
- PostgreSQL in Docker Desktop
- React + Vite frontend
- MQTT command/status channel
- native/local MQTT broker
- Python five-node simulator
- Arduino ESP32-C3 shared firmware

## Previously validated
Single ESP32-C3 serial console upload/output: PASS.

## Out of MVP scope
- computer vision / OCR
- custom PCB
- mobile native app
- cloud multi-tenant accounts
- 50 independent ESP32 boards
