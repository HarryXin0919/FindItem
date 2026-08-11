// FindIt shared controller firmware - identity and build-time configuration.
//
// ONE source, five identities. The identity is chosen at build time with
//
//     -DFINDIT_CONTROLLER_INDEX=<1..5>
//
// and nothing else differs between the five nodes. Do not fork this sketch.
//
// Secrets never live in this repository. `Secrets.h` is git-ignored; copy
// `Secrets.h.example` to `Secrets.h` locally and fill it in. If it is absent
// the firmware still compiles, with obvious placeholder values, so CI and a
// fresh clone can both build.
#pragma once

#include <stdint.h>

// ---------------------------------------------------------------- identity --

#ifndef FINDIT_CONTROLLER_INDEX
#define FINDIT_CONTROLLER_INDEX 1
#endif

#if FINDIT_CONTROLLER_INDEX < 1 || FINDIT_CONTROLLER_INDEX > 5
#error "FINDIT_CONTROLLER_INDEX must be 1..5 - the topology is locked by ADR-001"
#endif

static const uint8_t CONTROLLER_COUNT = 5;
static const uint8_t LEDS_PER_CONTROLLER = 10;
static const uint8_t TOTAL_DRAWERS = CONTROLLER_COUNT * LEDS_PER_CONTROLLER;

// Derived, never hand-written: a typo cannot put two nodes on the same range.
static const uint8_t CONTROLLER_INDEX = FINDIT_CONTROLLER_INDEX;
static const uint8_t GLOBAL_DRAWER_START =
    (FINDIT_CONTROLLER_INDEX - 1) * LEDS_PER_CONTROLLER + 1;
static const uint8_t GLOBAL_DRAWER_END = GLOBAL_DRAWER_START + LEDS_PER_CONTROLLER - 1;

#define FINDIT_STRINGIFY_(x) #x
#define FINDIT_STRINGIFY(x) FINDIT_STRINGIFY_(x)
#define FINDIT_CONTROLLER_ID "CTRL-0" FINDIT_STRINGIFY(FINDIT_CONTROLLER_INDEX)

static const char *const CONTROLLER_ID = FINDIT_CONTROLLER_ID;
static const char *const FW_VERSION = "fw-0.11.0";
static const char *const CONFIG_VERSION = "cfg-1";

// ------------------------------------------------------------------- topics --
// Must match 02_Core_Documents/05_MQTT_Protocol_and_Controller_Contract_CN and
// app/services/mqtt_client.py exactly.

#define FINDIT_TOPIC_ROOT "findit/controllers/"
static const char *const TOPIC_COMMAND = FINDIT_TOPIC_ROOT FINDIT_CONTROLLER_ID "/command";
static const char *const TOPIC_ACK = FINDIT_TOPIC_ROOT FINDIT_CONTROLLER_ID "/ack";
static const char *const TOPIC_STATUS = FINDIT_TOPIC_ROOT FINDIT_CONTROLLER_ID "/status";
static const char *const TOPIC_HEARTBEAT = FINDIT_TOPIC_ROOT FINDIT_CONTROLLER_ID "/heartbeat";

// --------------------------------------------------------------------- pins --
// Baseline from 06_Technical_Documents/FIRMWARE_ARCHITECTURE.md. Check board
// specific pin availability before final wiring on a non-generic ESP32-C3.

static const uint8_t PIN_I2C_SDA = 4;
static const uint8_t PIN_I2C_SCL = 5;
static const uint8_t PIN_WS2812_DATA = 3;
static const uint8_t MCP23017_ADDRESS = 0x20;  // A0/A1/A2 to GND; buses are independent

// ------------------------------------------------------------------ timings --

static const uint32_t HEARTBEAT_INTERVAL_MS = 10000;
static const uint32_t STATUS_INTERVAL_MS = 30000;
static const uint32_t WIFI_RETRY_INTERVAL_MS = 5000;
static const uint32_t MQTT_RETRY_INTERVAL_MS = 3000;
static const uint32_t DEFAULT_LOCATE_DURATION_MS = 30000;
static const uint32_t MAX_LOCATE_DURATION_MS = 600000;

// Bounded idempotency window. The simulator remembers every command_id it has
// ever seen; a device cannot, so it keeps the last N (finding F20).
static const uint8_t SEEN_COMMAND_WINDOW = 16;
static const uint8_t COMMAND_ID_MAX_LEN = 48;

// ------------------------------------------------------------------ secrets --
// Never commit real values. `Secrets.h` is git-ignored.

#if defined(__has_include)
#if __has_include("Secrets.h")
#include "Secrets.h"
#define FINDIT_HAVE_SECRETS 1
#endif
#endif

#ifndef FINDIT_WIFI_SSID
#define FINDIT_WIFI_SSID "REPLACE_LOCALLY"
#endif
#ifndef FINDIT_WIFI_PASSWORD
#define FINDIT_WIFI_PASSWORD "REPLACE_LOCALLY"
#endif
#ifndef FINDIT_MQTT_HOST
#define FINDIT_MQTT_HOST "192.168.1.10"
#endif
#ifndef FINDIT_MQTT_PORT
#define FINDIT_MQTT_PORT 1883
#endif
#ifndef FINDIT_MQTT_USERNAME
#define FINDIT_MQTT_USERNAME ""
#endif
#ifndef FINDIT_MQTT_PASSWORD
#define FINDIT_MQTT_PASSWORD ""
#endif

// True when the build is using placeholders rather than provisioned secrets.
// The firmware refuses to claim it is provisioned when it is not.
static inline bool secretsProvisioned() {
#ifdef FINDIT_HAVE_SECRETS
  return true;
#else
  return false;
#endif
}
