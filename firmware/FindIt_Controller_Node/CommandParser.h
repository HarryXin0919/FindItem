// Command parsing and idempotency.
//
// The validation ORDER here is deliberately identical to
// `SimulatedController.handle_command` in 09_Code/simulator/five_node_simulator.py,
// including the rejection messages. S10 finding F18: the simulator is the only
// executable reference for this contract, so if the two drift the software
// proof from S01-S10 stops transferring to the physical node.
//
//   1. topic must be this controller's own command topic
//   2. payload must parse as a JSON object       -> "unparsable payload"
//   3. command_id must be present                -> "missing command_id"
//   4. controller_id must equal ours             -> "controller_id mismatch"
//   5. local_led_index must be 0..9              -> "local_led_index out of range"
//   6. action must be known                      -> "unknown action"
//   7. a repeated command_id is acknowledged but changes nothing
//   8. apply
#pragma once

#include <Arduino.h>
#include <stdint.h>

#include "Config.h"

enum class CommandAction : uint8_t { Locate, Cancel, Test, Unknown };

struct LocateCommand {
  char commandId[COMMAND_ID_MAX_LEN] = {0};
  char controllerId[16] = {0};
  int ledIndex = -1;
  CommandAction action = CommandAction::Unknown;
  char pattern[12] = {0};
  uint32_t durationMs = DEFAULT_LOCATE_DURATION_MS;
};

enum class ParseResult : uint8_t {
  Ok,
  WrongTopic,
  Unparsable,
  MissingCommandId,
  ControllerMismatch,
  LedIndexOutOfRange,
  UnknownAction,
};

// Message matching the simulator's ACK text for the same rejection.
const char *rejectionMessage(ParseResult result);

// Steps 1-6. Never touches the LEDs.
ParseResult parseCommand(const char *topic, const char *payload, LocateCommand *out);

// Bounded idempotency window (finding F20 - a device cannot remember forever).
class SeenCommands {
 public:
  bool contains(const char *commandId) const;
  void remember(const char *commandId);
  void clear();

 private:
  char ids_[SEEN_COMMAND_WINDOW][COMMAND_ID_MAX_LEN] = {{0}};
  uint8_t next_ = 0;
};

extern SeenCommands seenCommands;
