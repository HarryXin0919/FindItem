# Firmware Architecture

One firmware codebase, five configurations.

Modules:
- configuration / controller identity
- Wi-Fi connection manager
- MQTT transport
- MCP23017 input/output abstraction
- WS2812 LED service
- non-blocking locate animation
- heartbeat / status
- command idempotency
- diagnostics

Pin baseline:
- I2C SDA: GPIO4
- I2C SCL: GPIO5
- WS2812 data: GPIO3

Board-specific pin availability must be checked before final wiring if a non-generic ESP32-C3 board is used.
