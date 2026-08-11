# MCP23017 I2C Scan

Per controller:
- MCP23017 VDD -> 3V3
- VSS -> GND
- SDA -> ESP32-C3 GPIO4
- SCL -> ESP32-C3 GPIO5
- A0/A1/A2 -> GND for address 0x20
- RESET held HIGH

Because each controller has its own ESP32-C3/I2C bus, all five MCP23017 chips may use 0x20.

PASS: scanner finds 0x20 reliably.
