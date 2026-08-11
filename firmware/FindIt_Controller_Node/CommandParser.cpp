#include "CommandParser.h"

#include <ArduinoJson.h>

SeenCommands seenCommands;

const char *rejectionMessage(ParseResult result) {
  switch (result) {
    case ParseResult::WrongTopic:
      return "topic does not belong to this controller";
    case ParseResult::Unparsable:
      return "unparsable payload";
    case ParseResult::MissingCommandId:
      return "missing command_id";
    case ParseResult::ControllerMismatch:
      return "controller_id mismatch";
    case ParseResult::LedIndexOutOfRange:
      return "local_led_index out of range";
    case ParseResult::UnknownAction:
      return "unknown action";
    case ParseResult::Ok:
    default:
      return "";
  }
}

static CommandAction actionFrom(const char *value) {
  if (value == nullptr || value[0] == '\0') return CommandAction::Locate;
  if (strcmp(value, "locate") == 0) return CommandAction::Locate;
  if (strcmp(value, "cancel") == 0) return CommandAction::Cancel;
  if (strcmp(value, "test") == 0) return CommandAction::Test;
  return CommandAction::Unknown;
}

ParseResult parseCommand(const char *topic, const char *payload, LocateCommand *out) {
  // 1. topic
  if (topic == nullptr || strcmp(topic, TOPIC_COMMAND) != 0) {
    return ParseResult::WrongTopic;
  }

  // 2. parse
  JsonDocument doc;
  if (deserializeJson(doc, payload) != DeserializationError::Ok || !doc.is<JsonObject>()) {
    return ParseResult::Unparsable;
  }

  // 3. command_id
  // `.as<const char *>()` on both branches: ArduinoJson 7 returns a MemberProxy
  // from `doc[...]`, and a proxy and a literal have no common type in a `?:`.
  const char *commandId =
      doc["command_id"].is<const char *>() ? doc["command_id"].as<const char *>() : nullptr;
  if (commandId == nullptr || commandId[0] == '\0') {
    return ParseResult::MissingCommandId;
  }
  strlcpy(out->commandId, commandId, sizeof(out->commandId));

  // 4. controller identity - re-validated on the device (MQTT contract 2)
  const char *controllerId =
      doc["controller_id"].is<const char *>() ? doc["controller_id"].as<const char *>() : nullptr;
  if (controllerId == nullptr || strcmp(controllerId, CONTROLLER_ID) != 0) {
    return ParseResult::ControllerMismatch;
  }
  strlcpy(out->controllerId, controllerId, sizeof(out->controllerId));

  // 5. LED index - re-validated on the device (MQTT contract 2)
  if (!doc["local_led_index"].is<int>()) {
    return ParseResult::LedIndexOutOfRange;
  }
  const int index = doc["local_led_index"].as<int>();
  if (index < 0 || index >= static_cast<int>(LEDS_PER_CONTROLLER)) {
    return ParseResult::LedIndexOutOfRange;
  }
  out->ledIndex = index;

  // 6. action
  const char *action =
      doc["action"].is<const char *>() ? doc["action"].as<const char *>() : "locate";
  out->action = actionFrom(action);
  if (out->action == CommandAction::Unknown) {
    return ParseResult::UnknownAction;
  }

  const char *pattern =
      doc["pattern"].is<const char *>() ? doc["pattern"].as<const char *>() : "solid";
  strlcpy(out->pattern, pattern, sizeof(out->pattern));

  uint32_t duration = doc["duration_ms"].is<uint32_t>() ? doc["duration_ms"].as<uint32_t>()
                                                        : DEFAULT_LOCATE_DURATION_MS;
  if (duration == 0 || duration > MAX_LOCATE_DURATION_MS) duration = DEFAULT_LOCATE_DURATION_MS;
  out->durationMs = duration;

  return ParseResult::Ok;
}

bool SeenCommands::contains(const char *commandId) const {
  if (commandId == nullptr) return false;
  for (uint8_t i = 0; i < SEEN_COMMAND_WINDOW; ++i) {
    if (ids_[i][0] != '\0' && strcmp(ids_[i], commandId) == 0) return true;
  }
  return false;
}

void SeenCommands::remember(const char *commandId) {
  if (commandId == nullptr || commandId[0] == '\0') return;
  strlcpy(ids_[next_], commandId, COMMAND_ID_MAX_LEN);
  next_ = static_cast<uint8_t>((next_ + 1) % SEEN_COMMAND_WINDOW);
}

void SeenCommands::clear() {
  memset(ids_, 0, sizeof(ids_));
  next_ = 0;
}
