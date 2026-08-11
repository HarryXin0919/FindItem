# Complete Node Quick Test

Target hardware:
`ESP32-C3 + MCP23017 + 10 x WS2812`

Run only after component quick tests pass.

Checklist:
- [ ] serial boot
- [ ] Wi-Fi
- [ ] MQTT connect
- [ ] MCP23017 0x20 detected
- [ ] ten LED index test
- [ ] command targets correct LED
- [ ] MCP input event is reported
- [ ] reconnect after router/broker restart
- [ ] 30 consecutive locate commands without wrong LED
