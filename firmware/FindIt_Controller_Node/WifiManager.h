// Wi-Fi connection manager - non-blocking, never busy-waits in loop().
#pragma once

#include <Arduino.h>
#include <stdint.h>

class WifiManager {
 public:
  void begin(const char *ssid, const char *password);
  void tick(uint32_t nowMs);
  bool connected() const;
  const char *ipAddress();
  int32_t rssi() const;

 private:
  const char *ssid_ = nullptr;
  const char *password_ = nullptr;
  uint32_t lastAttemptMs_ = 0;
  bool started_ = false;
  char ip_[20] = {0};
};

extern WifiManager wifiManager;
