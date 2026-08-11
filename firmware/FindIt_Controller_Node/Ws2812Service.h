// WS2812 LED service - ten pixels on one ESP32-C3 data pin.
//
// Driven through the ESP32 Arduino core's RMT wrapper, so the project needs no
// extra LED library. The MCP23017 never generates WS2812 timing (ADR-001 and
// 01_Project_Baseline/PROJECT_BASELINE.md).
//
// The locate animation is non-blocking: `tick()` is called from loop() and the
// LED turns itself off when its duration expires, so a lost cancel command
// cannot leave a drawer lit forever.
#pragma once

#include <Arduino.h>
#include <stdint.h>

class Ws2812Service {
 public:
  void begin(uint8_t pin, uint8_t count);

  // Light exactly one pixel and clear the rest. Returns false if the index is
  // out of range - the caller must have validated it already.
  bool locate(uint8_t index, uint32_t durationMs, const char *pattern);
  void testAll();
  void clear();
  void tick(uint32_t nowMs);

  int8_t activeIndex() const { return active_; }
  bool isLit() const { return active_ >= 0; }

 private:
  void show();
  void setPixel(uint8_t index, uint8_t r, uint8_t g, uint8_t b);

  uint8_t pin_ = 0;
  uint8_t count_ = 0;
  bool begun_ = false;
  int8_t active_ = -1;
  uint32_t expiresAt_ = 0;
  uint32_t lastBlink_ = 0;
  bool blinkOn_ = true;
  bool blinking_ = false;
  uint8_t grb_[30] = {0};  // 3 bytes per pixel, 10 pixels
};

extern Ws2812Service leds;
