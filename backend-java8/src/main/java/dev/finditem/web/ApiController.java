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

import java.util.HashMap;
import java.util.Map;

/**
 * REST endpoints (Java 8). Mirrors the Python/FastAPI backend exactly:
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
        if (isBlank(body.user_id) || isBlank(body.user_name)
                || isBlank(body.item_id) || body.action == null) {
            return ResponseEntity.badRequest().body(err("invalid_body"));
        }
        if (!"start".equals(body.action) && !"stop".equals(body.action)) {
            return ResponseEntity.badRequest().body(err("invalid_action"));
        }
        // Validate duration range for EVERY request (parity with Python's Field(ge=1, le=120),
        // which rejects an out-of-range duration regardless of action).
        if (body.duration != null && (body.duration.intValue() < 1 || body.duration.intValue() > 120)) {
            return ResponseEntity.badRequest().body(err("invalid_duration"));
        }

        Map<String, Object> item = catalog.findItem(body.item_id);
        if (item == null) {
            Map<String, Object> e = err("unknown_item");
            e.put("item_id", body.item_id);
            return ResponseEntity.status(404).body(e);
        }
        Object deviceObj = item.get("device_id");
        String deviceId = deviceObj == null ? null : String.valueOf(deviceObj);
        if (isBlank(deviceId) || "null".equals(deviceId)) {   // parity with Python: 500 on missing device_id
            Map<String, Object> e = err("item_missing_device_id");
            e.put("item_id", body.item_id);
            return ResponseEntity.status(500).body(e);
        }
        boolean buzzer = body.buzzer == null || body.buzzer.booleanValue();

        if ("start".equals(body.action)) {
            int duration;
            if (body.duration != null) {
                duration = body.duration.intValue();   // already range-checked above
            } else {
                duration = asInt(item.get("duration_sec"), 15);
            }
            // Atomic check-and-set: a null result means the device is already ringing (409).
            String eventId = bridge.tryStart(deviceId, body.item_id,
                    body.user_id, body.user_name, duration, buzzer);
            if (eventId == null) {
                Map<String, Object> e = err("device_busy");
                e.put("device_id", deviceId);
                e.put("state", bridge.deviceState(deviceId));
                return ResponseEntity.status(409).body(e);
            }
            Map<String, Object> ok = new HashMap<String, Object>();
            ok.put("ok", true);
            ok.put("event_id", eventId);
            ok.put("device_id", deviceId);
            ok.put("duration", duration);
            ok.put("buzzer", buzzer);
            return ResponseEntity.ok(ok);
        }

        // action == "stop"
        bridge.sendStop(deviceId, body.item_id);
        Map<String, Object> ok = new HashMap<String, Object>();
        ok.put("ok", true);
        ok.put("device_id", deviceId);
        return ResponseEntity.ok(ok);
    }

    @GetMapping("/devices")
    public Map<String, Object> devices() {
        Map<String, Object> out = new HashMap<String, Object>();
        out.put("devices", bridge.allDeviceStates());
        return out;
    }

    @GetMapping("/devices/{deviceId}")
    public Map<String, Object> device(@PathVariable String deviceId) {
        return bridge.deviceState(deviceId);
    }

    @GetMapping("/events")
    public Map<String, Object> events(@RequestParam(defaultValue = "50") int limit) {
        Map<String, Object> out = new HashMap<String, Object>();
        out.put("events", bridge.recentEvents(limit));
        return out;
    }

    /** Catalog read/parse failure -> 503 (retryable), mirroring the Python backend. */
    @ExceptionHandler(CatalogException.class)
    public ResponseEntity<Object> handleCatalog(CatalogException ex) {
        Map<String, Object> m = err(ex.code);
        m.put("reason", ex.getMessage());
        return ResponseEntity.status(ex.status).body(m);
    }

    // ---------- helpers ----------
    private static boolean isBlank(String s) {
        return s == null || s.trim().isEmpty();
    }

    private static Map<String, Object> err(String code) {
        Map<String, Object> m = new HashMap<String, Object>();
        m.put("error", code);
        return m;
    }

    private static int asInt(Object o, int fallback) {
        if (o instanceof Number) {
            return ((Number) o).intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(o));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }
}
