#include <Wire.h>
#include <Adafruit_MCP23X17.h>

Adafruit_MCP23X17 mcp;

void setup() {
  Serial.begin(115200);
  Wire.begin(4, 5);
  if (!mcp.begin_I2C(0x20, &Wire)) {
    Serial.println("FAIL MCP23017");
    while (true) delay(1000);
  }
  mcp.pinMode(0, OUTPUT);
  mcp.pinMode(8, INPUT_PULLUP);
  Serial.println("PASS MCP23017 initialized");
}
void loop() {
  mcp.digitalWrite(0, HIGH); delay(500);
  mcp.digitalWrite(0, LOW);  delay(500);
  Serial.printf("GPA? input GPB0=%d\n", mcp.digitalRead(8));
}
