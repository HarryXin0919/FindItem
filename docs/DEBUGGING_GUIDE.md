# Debugging Guide

Debug in layers:

1. serial
2. Wi-Fi
3. MQTT
4. MCP23017 I2C
5. MCP23017 GPIO
6. WS2812 one pixel
7. WS2812 ten pixels
8. one complete node
9. five-node routing

Never debug all layers simultaneously.

Common classes of failure:
- wrong COM port / USB mode
- wrong I2C address or RESET state
- missing common ground
- WS2812 DIN/DOUT direction reversed
- unstable 5V power
- MQTT topic mismatch
- duplicate controller IDs
- local/global LED index confusion
