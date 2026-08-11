#include <WiFi.h>
#include <PubSubClient.h>

const char* WIFI_SSID = "REPLACE_LOCALLY";
const char* WIFI_PASSWORD = "REPLACE_LOCALLY";
const char* MQTT_HOST = "192.168.1.100";
const uint16_t MQTT_PORT = 1883;
const char* CONTROLLER_ID = "CTRL-01";

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

String commandTopic() {
  return "findit/controllers/" + String(CONTROLLER_ID) + "/command";
}
String heartbeatTopic() {
  return "findit/controllers/" + String(CONTROLLER_ID) + "/heartbeat";
}

void onMessage(char* topic, byte* payload, unsigned int length) {
  Serial.print("RX "); Serial.print(topic); Serial.print(" -> ");
  for (unsigned int i = 0; i < length; ++i) Serial.print((char)payload[i]);
  Serial.println();
}

bool connectWiFi(unsigned long timeoutMs = 20000) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    delay(500); Serial.print(".");
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

bool connectMqtt() {
  if (!mqtt.connect(CONTROLLER_ID)) return false;
  mqtt.subscribe(commandTopic().c_str());
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  if (!connectWiFi()) {
    Serial.println("FAIL WiFi");
    return;
  }
  Serial.print("WiFi PASS IP="); Serial.println(WiFi.localIP());
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  if (connectMqtt()) {
    Serial.println("MQTT PASS subscribed to own command topic");
  } else {
    Serial.println("FAIL MQTT");
  }
}

unsigned long lastBeat = 0;
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
  if (millis() - lastBeat > 5000) {
    lastBeat = millis();
    mqtt.publish(heartbeatTopic().c_str(), "{\"status\":\"online\"}");
    Serial.println("heartbeat");
  }
}
