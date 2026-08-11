/*
  FindIt shared controller firmware.
  Locked node: ESP32-C3 + MCP23017 + 10 x WS2812
  One source, five identities - select with -DFINDIT_CONTROLLER_INDEX=1..5.

  Modules (06_Technical_Documents/FIRMWARE_ARCHITECTURE.md):
    Config.h          identity, topics, pins, timings, secret strategy
    WifiManager       non-blocking connection manager
    MqttTransport     transport; subscribes to this node's command topic only
    Mcp23017Io        local GPIO expansion (inputs, pull-ups, debounce)
    Ws2812Service     ten-pixel chain, non-blocking locate animation
    CommandParser     parsing + validation + bounded idempotency window
    Diagnostics       serial diagnostics, no secrets

  Physical upload begins in S12-S15. This sketch compiles and is provisionable
  without any hardware attached.
*/
#include <Arduino.h>

#include "CommandParser.h"
#include "Config.h"
#include "Diagnostics.h"
#include "Mcp23017Io.h"
#include "MqttTransport.h"
#include "Ws2812Service.h"
#include "WifiManager.h"

static uint32_t heartbeatSeq = 0;
static uint32_t lastHeartbeatMs = 0;
static uint32_t lastStatusMs = 0;

// Device side of the command contract. Validation order is identical to
// SimulatedController.handle_command - see CommandParser.h.
static void onCommand(const char *topic, const char *payload) {
  LocateCommand command;
  const ParseResult result = parseCommand(topic, payload, &command);

  if (result != ParseResult::Ok) {
    // A rejected command never touches the LEDs.
    mqtt.publishAck(command.commandId, "rejected", rejectionMessage(result));
    diag.logRejection(rejectionMessage(result));
    return;
  }

  if (seenCommands.contains(command.commandId)) {
    // Acknowledge again, change nothing (MQTT contract section 4).
    mqtt.publishAck(command.commandId, "completed", "duplicate command_id ignored");
    return;
  }
  seenCommands.remember(command.commandId);

  mqtt.publishAck(command.commandId, "received", "");

  bool applied = false;
  switch (command.action) {
    case CommandAction::Locate:
      applied = leds.locate(static_cast<uint8_t>(command.ledIndex), command.durationMs,
                            command.pattern);
      break;
    case CommandAction::Cancel:
      leds.clear();
      applied = true;
      break;
    case CommandAction::Test:
      leds.testAll();
      applied = true;
      break;
    default:
      applied = false;
      break;
  }

  if (applied) {
    mqtt.publishAck(command.commandId, "completed", "locate applied");
    diag.logLocate(command.commandId, command.ledIndex);
  } else {
    mqtt.publishAck(command.commandId, "rejected", "could not apply command");
  }
}

void setup() {
  diag.begin(115200);
  diag.banner();

  leds.begin(PIN_WS2812_DATA, LEDS_PER_CONTROLLER);
  const bool mcpOk = mcp.begin(MCP23017_ADDRESS, PIN_I2C_SDA, PIN_I2C_SCL);
  if (mcpOk) mcp.configureAllInputsPullup();
  diag.logMcp(mcpOk);

  wifiManager.begin(FINDIT_WIFI_SSID, FINDIT_WIFI_PASSWORD);
  mqtt.begin(FINDIT_MQTT_HOST, FINDIT_MQTT_PORT, FINDIT_MQTT_USERNAME, FINDIT_MQTT_PASSWORD,
             onCommand);
}

void loop() {
  const uint32_t now = millis();

  wifiManager.tick(now);
  mqtt.tick(now);
  leds.tick(now);

  uint16_t changed = 0;
  if (mcp.present() && mcp.poll(now, &changed)) {
    diag.logInputChange(changed, mcp.lastInputs());
  }

  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    if (mqtt.connected()) mqtt.publishHeartbeat(++heartbeatSeq, now / 1000, wifiManager.rssi());
  }

  if (now - lastStatusMs >= STATUS_INTERVAL_MS) {
    lastStatusMs = now;
    if (mqtt.connected()) {
      mqtt.publishStatus(true, leds.activeIndex(), mcp.present(), wifiManager.ipAddress(),
                         wifiManager.rssi(), secretsProvisioned());
    }
  }
}
