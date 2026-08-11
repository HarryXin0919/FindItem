#include <Wire.h>
constexpr int SDA_PIN = 4;
constexpr int SCL_PIN = 5;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN);
  Serial.println("I2C scan");
  int found = 0;
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf("Found 0x%02X\n", address);
      found++;
    }
  }
  Serial.printf("Devices: %d\n", found);
  Serial.println("Expected MCP23017: 0x20 when A0/A1/A2 are LOW.");
}
void loop() { delay(5000); }
