#include <Adafruit_NeoPixel.h>
constexpr int DATA_PIN = 3;
Adafruit_NeoPixel pixels(1, DATA_PIN, NEO_GRB + NEO_KHZ800);

void setup() {
  Serial.begin(115200);
  pixels.begin();
  pixels.setBrightness(32);
  pixels.setPixelColor(0, pixels.Color(255, 0, 0));
  pixels.show();
  Serial.println("Single WS2812 should be RED");
}
void loop() {}
