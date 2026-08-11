#include <Adafruit_NeoPixel.h>
constexpr int DATA_PIN = 3;
constexpr int LED_COUNT = 10;
Adafruit_NeoPixel pixels(LED_COUNT, DATA_PIN, NEO_GRB + NEO_KHZ800);

void setup() {
  Serial.begin(115200);
  pixels.begin();
  pixels.setBrightness(32);
  for (int i = 0; i < LED_COUNT; ++i) {
    pixels.clear();
    pixels.setPixelColor(i, pixels.Color(0, 80, 255));
    pixels.show();
    Serial.printf("LED %d ON\n", i);
    delay(500);
  }
  pixels.clear(); pixels.show();
  Serial.println("PASS sequence complete");
}
void loop() {}
