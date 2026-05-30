package dev.finditem.model;

/**
 * Request body for POST /api/search-events.
 *
 * JSON keys are snake_case and map 1:1 to these record components:
 * {@code user_id, user_name, item_id, action ("start"|"stop"), buzzer, duration}.
 * {@code buzzer} defaults to true and {@code duration} is optional (falls back to
 * the item's {@code duration_sec}); both are boxed so absence is distinguishable.
 */
public record SearchEvent(
        String user_id,
        String user_name,
        String item_id,
        String action,
        Boolean buzzer,
        Integer duration) {
}
