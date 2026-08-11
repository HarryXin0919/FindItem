// MQTT transport.
//
// Subscribes to this controller's own command topic ONLY - never a wildcard, so
// a device physically cannot receive another node's command (MQTT contract
// section 4). Publishes ACK, status and heartbeat.
#pragma once

#include <Arduino.h>
#include <stdint.h>

typedef void (*CommandHandler)(const char *topic, const char *payload);

class MqttTransport {
 public:
  void begin(const char *host, uint16_t port, const char *username, const char *password,
             CommandHandler handler);
  void tick(uint32_t nowMs);
  bool connected();

  bool publishAck(const char *commandId, const char *state, const char *message);
  bool publishStatus(bool online, int8_t activeLed, bool mcpPresent, const char *ip,
                     int32_t rssi, bool provisioned);
  bool publishHeartbeat(uint32_t seq, uint32_t uptimeS, int32_t rssi);

 private:
  bool reconnect();

  const char *host_ = nullptr;
  uint16_t port_ = 1883;
  const char *username_ = nullptr;
  const char *password_ = nullptr;
  uint32_t lastAttemptMs_ = 0;
};

extern MqttTransport mqtt;
