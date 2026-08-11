#include "Diagnostics.h"

#include "Config.h"

Diagnostics diag;

void Diagnostics::begin(uint32_t baud) {
  Serial.begin(baud);
  delay(1200);
}

void Diagnostics::banner() {
  Serial.println();
  Serial.println("FindIt controller firmware");
  Serial.printf("  identity      : %s (index %u of %u)\n", CONTROLLER_ID, CONTROLLER_INDEX,
                CONTROLLER_COUNT);
  Serial.printf("  drawers       : %u-%u  (%u LEDs)\n", GLOBAL_DRAWER_START, GLOBAL_DRAWER_END,
                LEDS_PER_CONTROLLER);
  Serial.printf("  firmware      : %s / %s\n", FW_VERSION, CONFIG_VERSION);
  Serial.printf("  command topic : %s\n", TOPIC_COMMAND);
  Serial.printf("  ack topic     : %s\n", TOPIC_ACK);
  Serial.printf("  pins          : SDA=%u SCL=%u WS2812=%u\n", PIN_I2C_SDA, PIN_I2C_SCL,
                PIN_WS2812_DATA);
  // Provisioning state only - never the values themselves.
  Serial.printf("  provisioned   : %s\n", secretsProvisioned() ? "yes" : "NO (placeholders)");
  if (!secretsProvisioned()) {
    Serial.println("  copy Secrets.h.example to Secrets.h before physical bring-up");
  }
}

void Diagnostics::logMcp(bool present) {
  Serial.printf("MCP23017 @0x%02X : %s\n", MCP23017_ADDRESS, present ? "PASS" : "not detected");
}

void Diagnostics::logLocate(const char *commandId, int ledIndex) {
  Serial.printf("locate applied : led=%d command_id=%s\n", ledIndex, commandId);
}

void Diagnostics::logRejection(const char *reason) {
  Serial.printf("command rejected: %s\n", reason);
}

void Diagnostics::logInputChange(uint16_t changedMask, uint16_t state) {
  Serial.printf("mcp input change: mask=0x%04X state=0x%04X\n", changedMask, state);
}
