#include "MqttTransport.h"

#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "Config.h"
#include "WifiManager.h"

MqttTransport mqtt;

namespace {
WiFiClient wifiClient;
PubSubClient client(wifiClient);
CommandHandler userHandler = nullptr;
char payloadBuffer[512];

void onMessage(char *topic, uint8_t *payload, unsigned int length) {
  const unsigned int n = length < sizeof(payloadBuffer) - 1 ? length : sizeof(payloadBuffer) - 1;
  memcpy(payloadBuffer, payload, n);
  payloadBuffer[n] = '\0';
  if (userHandler != nullptr) userHandler(topic, payloadBuffer);
}
}  // namespace

void MqttTransport::begin(const char *host, uint16_t port, const char *username,
                          const char *password, CommandHandler handler) {
  host_ = host;
  port_ = port;
  username_ = username;
  password_ = password;
  userHandler = handler;
  client.setServer(host_, port_);
  client.setCallback(onMessage);
  client.setBufferSize(768);
}

bool MqttTransport::connected() { return client.connected(); }

bool MqttTransport::reconnect() {
  if (!wifiManager.connected()) return false;

  const bool ok = (username_ != nullptr && username_[0] != '\0')
                      ? client.connect(CONTROLLER_ID, username_, password_)
                      : client.connect(CONTROLLER_ID);
  if (!ok) return false;

  // Own command topic only. No wildcard, ever.
  client.subscribe(TOPIC_COMMAND, 1);
  publishStatus(true, -1, false, wifiManager.ipAddress(), wifiManager.rssi(),
                secretsProvisioned());
  return true;
}

void MqttTransport::tick(uint32_t nowMs) {
  if (client.connected()) {
    client.loop();
    return;
  }
  if (nowMs - lastAttemptMs_ < MQTT_RETRY_INTERVAL_MS) return;
  lastAttemptMs_ = nowMs;
  reconnect();
}

bool MqttTransport::publishAck(const char *commandId, const char *state, const char *message) {
  JsonDocument doc;
  doc["command_id"] = commandId;
  doc["controller_id"] = CONTROLLER_ID;
  doc["state"] = state;
  doc["message"] = message;
  doc["device_time"] = millis();
  char out[320];
  serializeJson(doc, out, sizeof(out));
  return client.publish(TOPIC_ACK, out, false);
}

bool MqttTransport::publishStatus(bool online, int8_t activeLed, bool mcpPresent, const char *ip,
                                  int32_t rssi, bool provisioned) {
  JsonDocument doc;
  doc["controller_id"] = CONTROLLER_ID;
  doc["online"] = online;
  doc["fw_version"] = FW_VERSION;
  doc["config_version"] = CONFIG_VERSION;
  doc["led_count"] = LEDS_PER_CONTROLLER;
  doc["drawer_start"] = GLOBAL_DRAWER_START;
  doc["drawer_end"] = GLOBAL_DRAWER_END;
  doc["active_led"] = activeLed;
  doc["mcp23017"] = mcpPresent;
  doc["ip"] = ip;
  doc["rssi"] = rssi;
  // Whether real credentials were compiled in - never the credentials.
  doc["provisioned"] = provisioned;
  char out[420];
  serializeJson(doc, out, sizeof(out));
  return client.publish(TOPIC_STATUS, out, true);
}

bool MqttTransport::publishHeartbeat(uint32_t seq, uint32_t uptimeS, int32_t rssi) {
  JsonDocument doc;
  doc["controller_id"] = CONTROLLER_ID;
  doc["seq"] = seq;
  doc["uptime_s"] = uptimeS;
  doc["rssi"] = rssi;
  char out[160];
  serializeJson(doc, out, sizeof(out));
  return client.publish(TOPIC_HEARTBEAT, out, false);
}
