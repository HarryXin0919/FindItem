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
  // 只在有有效值时才带 current_item / current_event_id;
  // 否则(如重连时 idle)发空串会把后端缓存里的有效上下文冲掉。
  if (currentItemId.length())  doc["current_item"]     = currentItemId;
  if (currentEventId.length()) doc["current_event_id"] = currentEventId;
  doc["buzzer_on"]        = buzzerOn;
  if (stopReason) doc["stop_reason"] = stopReason;
  char buf[256];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  // PubSubClient's length-aware publish() takes const uint8_t*; cast the char
  // buffer explicitly — the ESP32 core compiles without -fpermissive, so the
  // implicit char*→uint8_t* conversion that AVR tolerates is a hard error here.
  mqtt.publish(statusTopic().c_str(), (const uint8_t*)buf, n, /*retain=*/false);
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
    if (dur < 1 || dur > 120) dur = 15;      // 设备侧夹紧:别信任直接发来的越界时长(否则可能响到地老天荒)
    startRing(String(item), String(eid), dur, buz);
  } else if (strcmp(cmd, "stop") == 0) {
    if (currentState != "idle") stopRing("backend");
  }
}

// =============== 6. 非阻塞连接维护 ===============
// 关键:绝不在 loop() 里死等连接。WiFi/MQTT 断了也要让按钮、到时自停照常运行,
// 否则响铃途中一旦断网,蜂鸣器就停不下来。
unsigned long lastWifiTry = 0;
unsigned long lastMqttTry = 0;

void serviceWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiTry < 3000) return;   // 每 3s 试一次,不阻塞
  lastWifiTry = millis();
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void serviceMqtt() {
  if (WiFi.status() != WL_CONNECTED) return;    // 没 IP 先别连 MQTT
  if (mqtt.connected()) return;
  if (millis() - lastMqttTry < 1500) return;    // 每 1.5s 单次尝试,不死循环
  lastMqttTry = millis();
  String clientId = String("findit-") + DEVICE_ID;
  if (mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
    mqtt.subscribe(cmdTopic().c_str(), 1);
    publishStatus();  // 一连上就让后端看到我
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
  // 启动时给 WiFi 一个有上限的等待(~10s);连不上也继续进 loop,由 serviceWifi 后台重连,
  // 不再因为 AP 没开/密码错就永久卡死在 setup。
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) delay(300);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setKeepAlive(30);
}

void loop() {
  // 连接维护是非阻塞的,后面这些状态机逻辑每一圈都必然执行 ——
  // 这正是「断网也能按按钮停 / 到时自停」的保证。
  serviceWifi();
  serviceMqtt();
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
