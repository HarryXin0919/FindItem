# Hardware Integration Guide

## Gate
Do not begin physical integration before S10 is DONE. S11 may prepare/compile firmware without connected hardware.

## One-node build order

1. Use one ESP32-C3 as CTRL-01.
2. Confirm the existing serial baseline.
3. Run `05_Hardware_Quick_Tests/01_WiFi/WiFi_Quick_Test.ino`.
4. Run `02_MQTT/MQTT_Quick_Test.ino`.
5. Wire MCP23017 and run the I2C scan.
6. Run the MCP GPIO test.
7. Wire one WS2812 and run the single-pixel test.
8. Expand to 10 WS2812 and run the sequential test.
9. Run `07_Complete_Node/Complete_Node_Quick_Test.ino`.
10. Only after S15 passes, duplicate the validated design to CTRL-02..05.

## Per-node wiring baseline

- ESP32-C3 GPIO4 -> MCP23017 SDA
- ESP32-C3 GPIO5 -> MCP23017 SCL
- MCP23017 VDD -> 3.3V
- MCP23017 VSS -> GND
- MCP23017 A0/A1/A2 -> GND
- MCP23017 RESET -> HIGH
- ESP32-C3 GPIO3 -> recommended 3.3V-to-5V HCT level shifter -> 330-470 ohm -> first WS2812 DIN
- regulated 5V -> WS2812 5V
- all grounds common

## Power

Do not power a ten-pixel chain at high brightness from the ESP32-C3 regulator. Use a regulated 5V supply sized with margin. During bring-up, use low brightness and verify supply stability.

## Five-node integration

Provision unique identities:
`CTRL-01` ... `CTRL-05`.

Each node may use MCP23017 address `0x20` because the I2C buses are physically independent.

Use `five_node_matrix_publisher.py` only after all five nodes are individually ready. Visually verify each of the 50 global drawer routes and record results in S17 evidence.
