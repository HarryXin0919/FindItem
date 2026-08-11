/*
  FindIt complete one-node quick test.
  Libraries:
  - PubSubClient
  - Adafruit MCP23X17
  - Adafruit NeoPixel
*/
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_MCP23X17.h>
#include <Adafruit_NeoPixel.h>

const char* WIFI_SSID = "REPLACE_LOCALLY";
const char* WIFI_PASSWORD = "REPLACE_LOCALLY";
const char* MQTT_HOST = "192.168.1.100";
const uint16_t MQTT_PORT = 1883;
const char* CONTROLLER_ID = "CTRL-01";

constexpr uint8_t SDA_PIN = 4;
constexpr uint8_t SCL_PIN = 5;
constexpr uint8_t LED_DATA_PIN = 3;
constexpr uint8_t LED_COUNT = 10;
constexpr uint8_t MCP_ADDRESS = 0x20;

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Adafruit_MCP23X17 mcp;
Adafruit_NeoPixel pixels(LED_COUNT, LED_DATA_PIN, NEO_GRB + NEO_KHZ800);

String commandTopic() { return "findit/controllers/" + String(CONTROLLER_ID) + "/command"; }
String ackTopic() { return "findit/controllers/" + String(CONTROLLER_ID) + "/ack"; }

void allOff() { pixels.clear(); pixels.show(); }

void lightIndex(int index) {
  if (index < 0 || index >= LED_COUNT) return;
  allOff();
  pixels.setPixelColor(index, pixels.Color(0, 80, 255));
  pixels.show();
}

void onMessage(char* topic, byte* payload, unsigned int length) {
  // Quick-test protocol: payload is a single ASCII digit 0..9.
  if (length != 1 || payload[0] < '0' || payload[0] > '9') {
    mqtt.publish(ackTopic().c_str(), "{\"state\":\"rejected\"}");
    return;
  }
  int index = payload[0] - '0';
  lightIndex(index);
  Serial.printf("LED %d active\n", index);
  mqtt.publish(ackTopic().c_str(), "{\"state\":\"completed\"}");
}

void connectNetwork() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWiFi connected");
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  while (!mqtt.connected()) {
    if (!mqtt.connect(CONTROLLER_ID)) delay(1000);
  }
  mqtt.subscribe(commandTopic().c_str());
  Serial.println("MQTT connected");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(SDA_PIN, SCL_PIN);
  if (!mcp.begin_I2C(MCP_ADDRESS, &Wire)) {
    Serial.println("FAIL MCP23017");
    while (true) delay(1000);
  }
  mcp.pinMode(8, INPUT_PULLUP); // example drawer input

  pixels.begin();
  pixels.setBrightness(32);
  allOff();

  connectNetwork();
  Serial.println("COMPLETE NODE READY");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED || !mqtt.connected()) connectNetwork();
  mqtt.loop();

  static int lastInput = HIGH;
  int input = mcp.digitalRead(8);
  if (input != lastInput) {
    lastInput = input;
    Serial.printf("MCP input GPB0=%d\n", input);
  }
}
