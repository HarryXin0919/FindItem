// Serial diagnostics.
//
// Prints identity, topics and state. Never prints a Wi-Fi or MQTT credential -
// only whether the build was provisioned at all. Mirrors the backend's
// redaction rule (app/logging_config.py) on the device side.
#pragma once

#include <Arduino.h>
#include <stdint.h>

class Diagnostics {
 public:
  void begin(uint32_t baud);
  void banner();
  void logMcp(bool present);
  void logLocate(const char *commandId, int ledIndex);
  void logRejection(const char *reason);
  void logInputChange(uint16_t changedMask, uint16_t state);
};

extern Diagnostics diag;
