#include "Ws2812Service.h"

#include "Config.h"

Ws2812Service leds;

namespace {
// WS2812B timing at 100 ns RMT resolution.
constexpr uint32_t kRmtResolutionHz = 10000000;  // 10 MHz -> 0.1 us per tick
constexpr uint16_t kT0H = 3;   // 0.3 us
constexpr uint16_t kT0L = 9;   // 0.9 us
constexpr uint16_t kT1H = 6;   // 0.6 us
constexpr uint16_t kT1L = 6;   // 0.6 us
constexpr uint8_t kBrightness = 160;
}  // namespace

void Ws2812Service::begin(uint8_t pin, uint8_t count) {
  pin_ = pin;
  count_ = count > 10 ? 10 : count;
  begun_ = rmtInit(pin_, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, kRmtResolutionHz);
  clear();
}

void Ws2812Service::setPixel(uint8_t index, uint8_t r, uint8_t g, uint8_t b) {
  if (index >= count_) return;
  // WS2812 wire order is GRB.
  grb_[index * 3 + 0] = g;
  grb_[index * 3 + 1] = r;
  grb_[index * 3 + 2] = b;
}

void Ws2812Service::show() {
  if (!begun_) return;
  rmt_data_t symbols[24 * 10];
  size_t n = 0;
  for (uint8_t i = 0; i < count_ * 3; ++i) {
    for (int8_t bit = 7; bit >= 0; --bit) {
      const bool one = (grb_[i] >> bit) & 0x01;
      symbols[n].level0 = 1;
      symbols[n].duration0 = one ? kT1H : kT0H;
      symbols[n].level1 = 0;
      symbols[n].duration1 = one ? kT1L : kT0L;
      ++n;
    }
  }
  rmtWrite(pin_, symbols, n, RMT_WAIT_FOR_EVER);
}

bool Ws2812Service::locate(uint8_t index, uint32_t durationMs, const char *pattern) {
  if (index >= count_) return false;
  if (durationMs == 0 || durationMs > MAX_LOCATE_DURATION_MS) {
    durationMs = DEFAULT_LOCATE_DURATION_MS;
  }

  // Exactly one pixel: clear everything first. One controller shows one drawer.
  memset(grb_, 0, sizeof(grb_));
  setPixel(index, 255, 200, 40);
  show();

  active_ = static_cast<int8_t>(index);
  expiresAt_ = millis() + durationMs;
  blinking_ = pattern != nullptr && strcmp(pattern, "blink") == 0;
  blinkOn_ = true;
  lastBlink_ = millis();
  return true;
}

void Ws2812Service::testAll() {
  for (uint8_t i = 0; i < count_; ++i) setPixel(i, kBrightness, kBrightness, kBrightness);
  show();
  active_ = -1;
  expiresAt_ = millis() + 2000;
  blinking_ = false;
}

void Ws2812Service::clear() {
  memset(grb_, 0, sizeof(grb_));
  show();
  active_ = -1;
  expiresAt_ = 0;
  blinking_ = false;
}

void Ws2812Service::tick(uint32_t nowMs) {
  if (expiresAt_ != 0 && nowMs >= expiresAt_) {
    clear();
    return;
  }
  if (blinking_ && active_ >= 0 && nowMs - lastBlink_ >= 400) {
    lastBlink_ = nowMs;
    blinkOn_ = !blinkOn_;
    memset(grb_, 0, sizeof(grb_));
    if (blinkOn_) setPixel(static_cast<uint8_t>(active_), 255, 200, 40);
    show();
  }
}
