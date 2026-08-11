#include "Mcp23017Io.h"

#include <Wire.h>

Mcp23017Io mcp;

namespace {
constexpr uint8_t REG_IODIRA = 0x00;
constexpr uint8_t REG_IODIRB = 0x01;
constexpr uint8_t REG_GPPUA = 0x0C;
constexpr uint8_t REG_GPPUB = 0x0D;
constexpr uint8_t REG_GPIOA = 0x12;
constexpr uint8_t REG_GPIOB = 0x13;
constexpr uint32_t DEBOUNCE_MS = 25;
}  // namespace

bool Mcp23017Io::begin(uint8_t address, uint8_t sdaPin, uint8_t sclPin) {
  address_ = address;
  Wire.begin(sdaPin, sclPin);
  Wire.setClock(400000);
  return probe();
}

bool Mcp23017Io::probe() {
  Wire.beginTransmission(address_);
  present_ = (Wire.endTransmission() == 0);
  return present_;
}

bool Mcp23017Io::write8(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address_);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool Mcp23017Io::read8(uint8_t reg, uint8_t *value) {
  Wire.beginTransmission(address_);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(static_cast<int>(address_), 1) != 1) return false;
  *value = Wire.read();
  return true;
}

bool Mcp23017Io::configureAllInputsPullup() {
  if (!present_ && !probe()) return false;
  const bool ok = write8(REG_IODIRA, 0xFF) && write8(REG_IODIRB, 0xFF) &&
                  write8(REG_GPPUA, 0xFF) && write8(REG_GPPUB, 0xFF);
  if (ok) readInputs(&lastStable_);
  lastRaw_ = lastStable_;
  return ok;
}

bool Mcp23017Io::readInputs(uint16_t *out) {
  uint8_t a = 0, b = 0;
  if (!read8(REG_GPIOA, &a) || !read8(REG_GPIOB, &b)) {
    present_ = false;
    return false;
  }
  *out = static_cast<uint16_t>(b) << 8 | a;
  return true;
}

bool Mcp23017Io::poll(uint32_t nowMs, uint16_t *changed) {
  uint16_t raw = 0;
  if (!readInputs(&raw)) return false;

  if (raw != lastRaw_) {
    lastRaw_ = raw;
    lastChangeMs_ = nowMs;
    return false;  // still settling
  }
  if (raw != lastStable_ && (nowMs - lastChangeMs_) >= DEBOUNCE_MS) {
    *changed = static_cast<uint16_t>(lastStable_ ^ raw);
    lastStable_ = raw;
    return true;
  }
  return false;
}
