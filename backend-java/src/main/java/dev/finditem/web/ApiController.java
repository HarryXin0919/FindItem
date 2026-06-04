package dev.finditem.web;

import dev.finditem.catalog.CatalogException;
import dev.finditem.catalog.ItemCatalog;
import dev.finditem.model.SearchEvent;
import dev.finditem.mqtt.FindItBridge;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * REST endpoints. Mirrors the Python/FastAPI backend exactly:
 *   GET  /api/items
 *   POST /api/search-events
 *   GET  /api/devices
 *   GET  /api/devices/{id}
 *   GET  /api/events?limit=N
 */
@RestController
@RequestMapping("/api")
public class ApiController {

    private final ItemCatalog catalog;
    private final FindItBridge bridge;

    public ApiController(ItemCatalog catalog, FindItBridge bridge) {
        this.catalog = catalog;
        this.bridge = bridge;
    }

    @GetMapping("/items")
    public Map<String, Object> items() {
        return catalog.load();
    }

    @PostMapping("/search-events")
    public ResponseEntity<Object> searchEvent(@RequestBody SearchEvent body) {
        if (isBlank(body.user_id()) || isBlank(body.user_name())
                || isBlank(body.item_id()) || body.action() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "invalid_body"));
        }
        if (!"start".equals(body.action()) && !"stop".equals(body.action())) {
            return ResponseEntity.badRequest().body(Map.of("error", "invalid_action"));
        }

        Map<String, Object> item = catalog.findItem(body.item_id());
        if (item == null) {
            return ResponseEntity.status(404)
                    .body(Map.of("error", "unknown_item", "item_id", body.item_id()));
        }
        String deviceId = String.valueOf(item.get("device_id"));
        boolean buzzer = body.buzzer() == null || body.buzzer();

        if ("start".equals(body.action())) {
            int duration;
            if (body.duration() != null) {
                duration = body.duration();
                if (duration < 1 || duration > 120) {
                    return ResponseEntity.badRequest().body(Map.of("error", "invalid_duration"));
                }
            } else {
                duration = asInt(item.get("duration_sec"), 15);
            }
            // Atomic check-and-set: a null result means the device is already ringing (409).
            String eventId = bridge.tryStart(deviceId, body.item_id(),
                    body.user_id(), body.user_name(), duration, buzzer);
            if (eventId == null) {
                return ResponseEntity.status(409).body(Map.of(
                        "error", "device_busy",
                        "device_id", deviceId,
                        "state", bridge.deviceState(deviceId)));
            }
            return ResponseEntity.ok(Map.of(
                    "ok", true,
                    "event_id", eventId,
                    "device_id", deviceId,
                    "duration", duration,
                    "buzzer", buzzer));
        }

        // action == "stop"
        bridge.sendStop(deviceId, body.item_id());
        return ResponseEntity.ok(Map.of("ok", true, "device_id", deviceId));
    }

    @GetMapping("/devices")
    public Map<String, Object> devices() {
        return Map.of("devices", bridge.allDeviceStates());
    }

    @GetMapping("/devices/{deviceId}")
    public Map<String, Object> device(@PathVariable String deviceId) {
        return bridge.deviceState(deviceId);
    }

    @GetMapping("/events")
    public Map<String, Object> events(@RequestParam(defaultValue = "50") int limit) {
        return Map.of("events", bridge.recentEvents(limit));
    }

    /** Catalog read/parse failure -> 503 (retryable), mirroring the Python backend. */
    @ExceptionHandler(CatalogException.class)
    public ResponseEntity<Object> handleCatalog(CatalogException e) {
        return ResponseEntity.status(e.status)
                .body(Map.of("error", e.code, "reason", e.getMessage()));
    }

    // ---------- helpers ----------
    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }

    private static int asInt(Object o, int fallback) {
        if (o instanceof Number n) {
            return n.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(o));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }
}
