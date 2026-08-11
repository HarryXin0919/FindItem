#include "WifiManager.h"

#include <WiFi.h>

#include "Config.h"

WifiManager wifiManager;

void WifiManager::begin(const char *ssid, const char *password) {
  ssid_ = ssid;
  password_ = password;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid_, password_);
  started_ = true;
  lastAttemptMs_ = millis();
}

void WifiManager::tick(uint32_t nowMs) {
  if (!started_) return;
  if (WiFi.status() == WL_CONNECTED) return;
  if (nowMs - lastAttemptMs_ < WIFI_RETRY_INTERVAL_MS) return;
  lastAttemptMs_ = nowMs;
  WiFi.disconnect();
  WiFi.begin(ssid_, password_);
}

bool WifiManager::connected() const { return WiFi.status() == WL_CONNECTED; }

const char *WifiManager::ipAddress() {
  strlcpy(ip_, WiFi.localIP().toString().c_str(), sizeof(ip_));
  return ip_;
}

int32_t WifiManager::rssi() const { return WiFi.RSSI(); }
