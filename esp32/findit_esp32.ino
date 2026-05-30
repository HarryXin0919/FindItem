/*
 * FindIt ESP32 端
 *
 * 硬件接线:
 *   LED       -> GPIO 2  (板载 LED 也可)
 *   蜂鸣器     -> GPIO 5  (无源蜂鸣器更好听,有源就 digitalWrite)
 *   停止按钮   -> GPIO 4  -> GND  (用 INPUT_PULLUP)
 *
 * 依赖库(Arduino IDE 库管理器搜):
 *   PubSubClient (Nick O'Leary)
 *   ArduinoJson (Benoit Blanchon)
 *
 * 每台设备只需要改 DEVICE_ID。命令载荷里的 buzzer 字段决定要不要响铃。
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// =============== 1. 配置区(逐台修改) ===============
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST     = "192.168.1.100";   // 笔记本 LAN IP
const uint16_t MQTT_PORT  = 1883;
const char* MQTT_USER     = "findit_backend";
const char* MQTT_PASS     = "findit123";
const char* DEVICE_ID     = "esp32-001";        // 每台不同!和 items.json device_id 对齐

const int PIN_LED    = 2;
const int PIN_BUZZER = 5;
const int PIN_BUTTON = 4;

// =============== 2. 状态机 ===============
String   currentState   = "idle";    // idle / starting / ringing
String   currentEventId = "";
String   currentItemId  = "";
bool     buzzerOn       = false;
unsigned long ringStartedAt = 0;
unsigned long ringDurationMs = 0;
int      lastButton     = HIGH;

WiFiClient   netClient;
PubSubClient mqtt(netClient);

String cmdTopic()    { return String("findit/device/") + DEVICE_ID + "/command"; }
String statusTopic() { return String("findit/device/") + DEVICE_ID + "/status"; }

// =============== 3. 发送状态 ===============
void publishStatus(const char* stopReason = nullptr) {
  StaticJsonDocument<256> doc;
  doc["state"]            = currentState;
  doc["device_id"]        = DEVICE_ID;
  doc["current_item"]     = currentItemId;
  doc["current_event_id"] = currentEventId;
  doc["buzzer_on"]        = buzzerOn;
  if (stopReason) doc["stop_reason"] = stopReason;
  char buf[256];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(statusTopic().c_str(), buf, n, /*retain=*/false);
}

// =============== 4. 行为 ===============
void startRing(const String& itemId, const String& eventId, int durationSec, bool wantBuzzer) {
  currentState   = "ringing";
  currentItemId  = itemId;
  currentEventId = eventId;
  buzzerOn       = wantBuzzer;
  ringStartedAt  = millis();
  ringDurationMs = (unsigned long)durationSec * 1000UL;

  digitalWrite(PIN_LED, HIGH);
  if (buzzerOn) {
    tone(PIN_BUZZER, 2000);  // 2 kHz 方波 —— 无源蜂鸣器要这个
  } else {
    noTone(PIN_BUZZER);
    digitalWrite(PIN_BUZZER, LOW);
  }
  publishStatus();
}

void stopRing(const char* reason) {
  currentState = "idle";
  digitalWrite(PIN_LED, LOW);
  noTone(PIN_BUZZER);
  digitalWrite(PIN_BUZZER, LOW);
  buzzerOn = false;
  publishStatus(reason);
  currentItemId  = "";
  currentEventId = "";
}

// =============== 5. MQTT 命令回调 ===============
void onMessage(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, payload, length)) return;
  const char* cmd = doc["cmd"];
  if (!cmd) return;

  if (strcmp(cmd, "start") == 0) {
    // 设备已忙,忽略(后端已用 device_busy 拒过一道)
    if (currentState == "ringing" || currentState == "starting") return;
    const char* item = doc["item_id"] | "";
    const char* eid  = doc["event_id"] | "";
    int  dur = doc["duration"] | 15;
    bool buz = doc["buzzer"]   | true;       // 默认响 —— 兼容旧载荷
    startRing(String(item), String(eid), dur, buz);
  } else if (strcmp(cmd, "stop") == 0) {
    if (currentState != "idle") stopRing("backend");
  }
}

// =============== 6. MQTT 连接保活 ===============
void ensureMqtt() {
  while (!mqtt.connected()) {
    String clientId = String("findit-") + DEVICE_ID;
    if (mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
      mqtt.subscribe(cmdTopic().c_str(), 1);
      publishStatus();  // 上电一次,后端马上看到我
    } else {
      delay(1500);
    }
  }
}

// =============== 7. Setup / Loop ===============
void setup() {
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  digitalWrite(PIN_LED, LOW);
  digitalWrite(PIN_BUZZER, LOW);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(300);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setKeepAlive(30);
}

void loop() {
  ensureMqtt();
  mqtt.loop();

  // 物理按钮 —— 下降沿触发,"按按钮停"
  int b = digitalRead(PIN_BUTTON);
  if (lastButton == HIGH && b == LOW) {
    if (currentState == "ringing") stopRing("button");
  }
  lastButton = b;

  // 到时自停
  if (currentState == "ringing" && millis() - ringStartedAt > ringDurationMs) {
    stopRing("timeout");
  }
}
