#include <WiFi.h>

const char* SSID = "REPLACE_LOCALLY";
const char* PASSWORD = "REPLACE_LOCALLY";

void setup() {
  Serial.begin(115200);
  delay(1000);
  WiFi.mode(WIFI_STA);
  WiFi.begin(SSID, PASSWORD);
  Serial.print("Connecting");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500); Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("PASS IP: "); Serial.println(WiFi.localIP());
  } else {
    Serial.println("FAIL WiFi timeout");
  }
}
void loop() { delay(1000); }
