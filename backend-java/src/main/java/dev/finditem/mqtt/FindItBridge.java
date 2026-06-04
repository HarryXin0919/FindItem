package dev.finditem.mqtt;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken;
import org.eclipse.paho.client.mqttv3.MqttCallbackExtended;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.locks.ReentrantLock;

/**
 * MQTT bridge: translates backend business commands into
 * {@code findit/device/{id}/command}, subscribes to {@code findit/device/+/status}
 * to track per-device state, and keeps an in-memory event log.
 *
 * Mirrors the audited Python reference (backend/app/mqtt_bridge.py):
 * - one ringing task per device at a time, enforced by an ATOMIC check-and-set
 *   ({@link #tryStart}) so two concurrent starts can't both win;
 * - device "busy" has a freshness check — a starting/ringing state older than
 *   duration + grace is treated as a stale/offline device and auto-unlocked, so a
 *   single search can never wedge an item forever;
 * - if the start command can't be published (broker down), the placeholder is
 *   rolled back so the item isn't left stuck;
 * - the broker connection is non-blocking: the backend boots (and the REST API
 *   works) even if the broker isn't up yet, retrying in the background.
 */
@Component
public class FindItBridge {

    private static final Logger log = LoggerFactory.getLogger(FindItBridge.class);

    private static final String CMD_TOPIC = "findit/device/%s/command";
    private static final String STATUS_SUB = "findit/device/+/status";
    private static final int MAX_EVENTS = 200;
    /** a starting/ringing state older than (duration + this) is treated as stale -> not busy. */
    private static final long STALE_GRACE_SEC = 10;

    private final ObjectMapper om = new ObjectMapper();
    private final Map<String, Map<String, Object>> deviceStatus = new HashMap<>();
    private final Deque<Map<String, Object>> events = new ArrayDeque<>();
    private final ReentrantLock lock = new ReentrantLock();

    private final String host;
    private final int port;
    private final String user;
    private final String pass;
    private volatile MqttClient client;

    public FindItBridge(
            @Value("${mqtt.host:127.0.0.1}") String host,
            @Value("${mqtt.port:1883}") int port,
            @Value("${mqtt.user:findit_backend}") String user,
            @Value("${mqtt.pass:findit123}") String pass) {
        this.host = host;
        this.port = port;
        this.user = user;
        this.pass = pass;
    }

    // ---------- lifecycle ----------
    @PostConstruct
    public void start() {
        // Never throw from @PostConstruct: a missing broker must not stop the app from
        // booting (the REST API still works; we reconnect in the background).
        try {
            String uri = "tcp://" + host + ":" + port;
            client = new MqttClient(uri, "findit-backend-" + shortId(6), null);
            client.setCallback(new MqttCallbackExtended() {
                @Override
                public void connectComplete(boolean reconnect, String serverURI) {
                    try {
                        client.subscribe(STATUS_SUB, 1);
                        log.info("subscribed to {}", STATUS_SUB);
                    } catch (MqttException e) {
                        log.warn("subscribe failed", e);
                    }
                }

                @Override
                public void connectionLost(Throwable cause) {
                    log.warn("MQTT connection lost (auto-reconnect will retry)", cause);
                }

                @Override
                public void messageArrived(String topic, MqttMessage message) {
                    onMessage(topic, message);
                }

                @Override
                public void deliveryComplete(IMqttDeliveryToken token) {
                }
            });
            connectInBackground();
        } catch (Exception e) {
            log.warn("MQTT init failed (API still works, will retry): {}", e.getMessage());
        }
    }

    /** Retry the initial connect off the main thread; Paho's auto-reconnect handles later drops. */
    private void connectInBackground() {
        MqttConnectOptions opts = new MqttConnectOptions();
        opts.setUserName(user);
        opts.setPassword(pass.toCharArray());
        opts.setAutomaticReconnect(true);
        opts.setCleanSession(true);
        opts.setKeepAliveInterval(30);
        Thread t = new Thread(() -> {
            while (client != null && !client.isConnected()) {
                try {
                    client.connect(opts);
                    log.info("MQTT bridge connected to {}:{}", host, port);
                    return;
                } catch (Exception e) {
                    log.warn("MQTT connect failed, retrying in 3s: {}", e.getMessage());
                    try {
                        Thread.sleep(3000);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
        }, "mqtt-connect");
        t.setDaemon(true);
        t.start();
    }

    @PreDestroy
    public void stop() {
        try {
            if (client != null) {
                client.disconnect();
                client.close();
            }
        } catch (MqttException ignored) {
        }
    }

    // ---------- MQTT inbound ----------
    @SuppressWarnings("unchecked")
    void onMessage(String topic, MqttMessage msg) {
        String[] parts = topic.split("/");
        // expect: findit / device / {device_id} / status
        if (parts.length != 4 || !"findit".equals(parts[0]) || !"status".equals(parts[3])) {
            return;
        }
        String deviceId = parts[2];

        Map<String, Object> payload;
        try {
            payload = om.readValue(msg.getPayload(), Map.class);
        } catch (Exception e) {
            return;
        }

        lock.lock();
        try {
            Map<String, Object> prev = deviceStatus.get(deviceId);
            if (prev == null) {
                prev = new HashMap<>();
            }
            Map<String, Object> merged = new HashMap<>(prev);
            // An idle device re-publishes empty current_item / current_event_id; don't let
            // those empties clobber the cached context (mirrors the Python filter).
            for (Map.Entry<String, Object> e : payload.entrySet()) {
                String k = e.getKey();
                Object v = e.getValue();
                if (("current_item".equals(k) || "current_event_id".equals(k))
                        && (v == null || "".equals(v))) {
                    continue;
                }
                merged.put(k, v);
            }
            merged.put("device_id", deviceId);
            merged.put("updated_at", now());
            deviceStatus.put(deviceId, merged);

            // device returned to idle from an active state => log a stopped event
            Object state = payload.get("state");
            Object prevState = prev.get("state");
            if ("idle".equals(state) && ("starting".equals(prevState) || "ringing".equals(prevState))) {
                Object reason = payload.get("stop_reason");
                pushEvent(stoppedEvent(deviceId, prev, reason == null ? "auto" : reason));
            }
        } finally {
            lock.unlock();
        }
    }

    // ---------- queries ----------
    /** Caller must hold {@link #lock}. Stale (offline) devices are auto-unlocked here. */
    private boolean isBusyLocked(String deviceId) {
        Map<String, Object> s = deviceStatus.get(deviceId);
        if (s == null) {
            return false;
        }
        Object st = s.get("state");
        if (!"starting".equals(st) && !"ringing".equals(st)) {
            return false;
        }
        long dur = asLong(s.get("_ring_duration"), 15L);
        double updated = asDouble(s.get("updated_at"), 0.0);
        if (now() - updated > dur + STALE_GRACE_SEC) {
            // device offline / never came online: status is stale -> unlock + timeout event,
            // otherwise one search would lock the item forever.
            s.put("state", "idle");
            pushEvent(stoppedEvent(deviceId, s, "timeout"));
            return false;
        }
        return true;
    }

    public boolean isBusy(String deviceId) {
        lock.lock();
        try {
            return isBusyLocked(deviceId);
        } finally {
            lock.unlock();
        }
    }

    public Map<String, Object> deviceState(String deviceId) {
        lock.lock();
        try {
            Map<String, Object> s = deviceStatus.get(deviceId);
            if (s == null) {
                Map<String, Object> unknown = new HashMap<>();
                unknown.put("device_id", deviceId);
                unknown.put("state", "unknown");
                return unknown;
            }
            return new HashMap<>(s);
        } finally {
            lock.unlock();
        }
    }

    public Map<String, Map<String, Object>> allDeviceStates() {
        lock.lock();
        try {
            Map<String, Map<String, Object>> out = new HashMap<>();
            for (Map.Entry<String, Map<String, Object>> e : deviceStatus.entrySet()) {
                out.put(e.getKey(), new HashMap<>(e.getValue()));
            }
            return out;
        } finally {
            lock.unlock();
        }
    }

    public List<Map<String, Object>> recentEvents(int limit) {
        lock.lock();
        try {
            List<Map<String, Object>> out = new ArrayList<>();
            for (Map<String, Object> e : events) {
                if (out.size() >= limit) {
                    break;
                }
                out.add(e);
            }
            return out;
        } finally {
            lock.unlock();
        }
    }

    // ---------- commands ----------
    /**
     * Atomically check "is this device free?" and, if so, claim it — returns the new
     * event id, or {@code null} if the device is busy (caller responds 409). The
     * check-and-set happens under one lock, so two concurrent starts can't both win.
     */
    public String tryStart(String deviceId, String itemId, String userId, String userName,
                           int duration, boolean buzzer) {
        String eventId = shortId(12);
        lock.lock();
        try {
            if (isBusyLocked(deviceId)) {
                return null;
            }
            Map<String, Object> st = new LinkedHashMap<>();
            st.put("device_id", deviceId);
            st.put("state", "starting");
            st.put("current_item", itemId);
            st.put("current_event_id", eventId);
            st.put("current_user_id", userId);
            st.put("current_user_name", userName);
            st.put("buzzer_on", buzzer);
            st.put("_ring_duration", (long) duration); // used by the freshness check
            st.put("updated_at", now());
            deviceStatus.put(deviceId, st);

            Map<String, Object> ev = new LinkedHashMap<>();
            ev.put("type", "started");
            ev.put("device_id", deviceId);
            ev.put("event_id", eventId);
            ev.put("item_id", itemId);
            ev.put("user_id", userId);
            ev.put("user_name", userName);
            ev.put("buzzer", buzzer);
            ev.put("duration", duration);
            ev.put("ts", now());
            pushEvent(ev);
        } finally {
            lock.unlock();
        }

        // Publish OUTSIDE the lock (no network I/O while holding it).
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("cmd", "start");
        payload.put("item_id", itemId);
        payload.put("event_id", eventId);
        payload.put("duration", duration);
        payload.put("buzzer", buzzer); // buzzer toggle goes down to the device here
        if (!publish(String.format(CMD_TOPIC, deviceId), payload)) {
            // command not sent (broker down): roll back the placeholder so the item isn't wedged.
            lock.lock();
            try {
                Map<String, Object> cur = deviceStatus.get(deviceId);
                if (cur != null && eventId.equals(cur.get("current_event_id"))) {
                    cur.put("state", "idle");
                }
            } finally {
                lock.unlock();
            }
            log.warn("start command not sent for {} (broker down?); rolled back", deviceId);
            return null;
        }
        return eventId;
    }

    public void sendStop(String deviceId, String itemId) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("cmd", "stop");
        payload.put("item_id", itemId);
        publish(String.format(CMD_TOPIC, deviceId), payload);
        // the real return to idle is recorded when the ESP32 reports status
    }

    // ---------- helpers ----------
    /** Publish; returns true on success. Protected so tests can stub it without a broker. */
    protected boolean publish(String topic, Map<String, Object> payload) {
        try {
            if (client == null || !client.isConnected()) {
                return false;
            }
            MqttMessage m = new MqttMessage(om.writeValueAsBytes(payload));
            m.setQos(1);
            client.publish(topic, m);
            return true;
        } catch (Exception e) {
            log.warn("publish failed to {}", topic, e);
            return false;
        }
    }

    private Map<String, Object> stoppedEvent(String deviceId, Map<String, Object> s, Object reason) {
        Map<String, Object> ev = new LinkedHashMap<>();
        ev.put("type", "stopped");
        ev.put("device_id", deviceId);
        ev.put("event_id", s.get("current_event_id"));
        ev.put("item_id", s.get("current_item"));
        ev.put("user_id", s.get("current_user_id"));
        ev.put("user_name", s.get("current_user_name"));
        ev.put("stop_reason", reason);
        ev.put("ts", now());
        return ev;
    }

    /** newest first, capped at MAX_EVENTS; caller holds the lock. */
    private void pushEvent(Map<String, Object> ev) {
        events.addFirst(ev);
        while (events.size() > MAX_EVENTS) {
            events.removeLast();
        }
    }

    private static long asLong(Object o, long fallback) {
        return (o instanceof Number) ? ((Number) o).longValue() : fallback;
    }

    private static double asDouble(Object o, double fallback) {
        return (o instanceof Number) ? ((Number) o).doubleValue() : fallback;
    }

    private static double now() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static String shortId(int len) {
        return UUID.randomUUID().toString().replace("-", "").substring(0, len);
    }
}
