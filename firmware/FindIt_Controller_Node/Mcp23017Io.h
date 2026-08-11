// MCP23017 input/output abstraction.
//
// Register-level over Wire, so no extra library is required. The MCP23017 is
// the node's local GPIO expansion (drawer buttons, reed switches, ACK inputs).
// It never drives the WS2812 chain.
//
// Every node may sit at address 0x20 because each controller has its own
// physical I2C bus (05_Hardware_Quick_Tests/03_MCP23017_I2C/README.md).
#pragma once

#include <Arduino.h>
#include <stdint.h>

class Mcp23017Io {
 public:
  bool begin(uint8_t address, uint8_t sdaPin, uint8_t sclPin);
  bool present() const { return present_; }
  bool probe();

  // All 16 pins as inputs with pull-ups - the drawer-button baseline.
  bool configureAllInputsPullup();
  bool readInputs(uint16_t *out);

  // Returns true when the debounced input word changed since the last poll.
  bool poll(uint32_t nowMs, uint16_t *changed);
  uint16_t lastInputs() const { return lastStable_; }

 private:
  bool write8(uint8_t reg, uint8_t value);
  bool read8(uint8_t reg, uint8_t *value);

  uint8_t address_ = 0x20;
  bool present_ = false;
  uint16_t lastStable_ = 0xFFFF;
  uint16_t lastRaw_ = 0xFFFF;
  uint32_t lastChangeMs_ = 0;
};

extern Mcp23017Io mcp;
