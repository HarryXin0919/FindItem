package dev.finditem.mqtt;

import com.fasterxml.jackson.databind.ObjectMapper;
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

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;
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
 * findit/device/{id}/command, subscribes to findit/device/+/status to track
 * per-device state, and keeps an in-memory event log.
 *
 * Java 8 build (javax.annotation.*); behaviour matches the Java 17 version.
 */
@Component
public class FindItBridge {

    private static final Logger log = LoggerFactory.getLogger(FindItBridge.class);

    private static final String CMD_TOPIC = "findit/device/%s/command";
    private static final String STATUS_SUB = "findit/device/+/status";
    private static final int MAX_EVENTS = 200;

    private final ObjectMapper om = new ObjectMapper();
    private final Map<String, Map<String, Object>> deviceStatus = new HashMap<String, Map<String, Object>>();
    private final Deque<Map<String, Object>> events = new ArrayDeque<Map<String, Object>>();
    private final ReentrantLock lock = new ReentrantLock();

    private final String host;
    private final int port;
    private final String user;
    private final String pass;
    private MqttClient client;

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
    public void start() throws MqttException {
        String uri = "tcp://" + host + ":" + port;
        client = new MqttClient(uri, "findit-backend-" + shortId(6), null);

        MqttConnectOptions opts = new MqttConnectOptions();
        opts.setUserName(user);
        opts.setPassword(pass.toCharArray());
        opts.setAutomaticReconnect(true);
        opts.setCleanSession(true);
        opts.setKeepAliveInterval(30);

        client.setCallback(new MqttCallbackExtended() {
            public void connectComplete(boolean reconnect, String serverURI) {
                try {
                    client.subscribe(STATUS_SUB, 1);
                    log.info("subscribed to {}", STATUS_SUB);
                } catch (MqttException e) {
                    log.warn("subscribe failed", e);
                }
            }

            public void connectionLost(Throwable cause) {
                log.warn("MQTT connection lost", cause);
            }

            public void messageArrived(String topic, MqttMessage message) {
                onMessage(topic, message);
            }

            public void deliveryComplete(IMqttDeliveryToken token) {
            }
        });

        client.connect(opts);
        log.info("MQTT bridge connected to {}", uri);
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
    private void onMessage(String topic, MqttMessage msg) {
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
                prev = new HashMap<String, Object>();
            }
            Map<String, Object> merged = new HashMap<String, Object>(prev);
            merged.putAll(payload);
            merged.put("device_id", deviceId);
            merged.put("updated_at", now());
            deviceStatus.put(deviceId, merged);

            // device returned to idle from an active state => log a stopped event
            Object state = payload.get("state");
            Object prevState = prev.get("state");
            if ("idle".equals(state) && ("starting".equals(prevState) || "ringing".equals(prevState))) {
                Map<String, Object> ev = new LinkedHashMap<String, Object>();
                ev.put("type", "stopped");
                ev.put("device_id", deviceId);
                ev.put("event_id", prev.get("current_event_id"));
                ev.put("item_id", prev.get("current_item"));
                ev.put("user_id", prev.get("current_user_id"));
                ev.put("user_name", prev.get("current_user_name"));
                Object reason = payload.get("stop_reason");
                ev.put("stop_reason", reason == null ? "auto" : reason);
                ev.put("ts", now());
                pushEvent(ev);
            }
        } finally {
            lock.unlock();
        }
    }

    // ---------- queries ----------
    public boolean isBusy(String deviceId) {
        lock.lock();
        try {
            Map<String, Object> s = deviceStatus.get(deviceId);
            Object st = (s == null) ? null : s.get("state");
            return "starting".equals(st) || "ringing".equals(st);
        } finally {
            lock.unlock();
        }
    }

    public Map<String, Object> deviceState(String deviceId) {
        lock.lock();
        try {
            Map<String, Object> s = deviceStatus.get(deviceId);
            if (s == null) {
                Map<String, Object> unknown = new HashMap<String, Object>();
                unknown.put("device_id", deviceId);
                unknown.put("state", "unknown");
                return unknown;
            }
            return new HashMap<String, Object>(s);
        } finally {
            lock.unlock();
        }
    }

    public Map<String, Map<String, Object>> allDeviceStates() {
        lock.lock();
        try {
            Map<String, Map<String, Object>> out = new HashMap<String, Map<String, Object>>();
            for (Map.Entry<String, Map<String, Object>> e : deviceStatus.entrySet()) {
                out.put(e.getKey(), new HashMap<String, Object>(e.getValue()));
            }
            return out;
        } finally {
            lock.unlock();
        }
    }

    public List<Map<String, Object>> recentEvents(int limit) {
        lock.lock();
        try {
            List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
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
    public String sendStart(String deviceId, String itemId, String userId, String userName,
                            int duration, boolean buzzer) {
        String eventId = shortId(12);

        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("cmd", "start");
        payload.put("item_id", itemId);
        payload.put("event_id", eventId);
        payload.put("duration", duration);
        payload.put("buzzer", buzzer); // buzzer toggle goes down to the device here
        publish(String.format(CMD_TOPIC, deviceId), payload);

        lock.lock();
        try {
            Map<String, Object> st = new HashMap<String, Object>();
            st.put("device_id", deviceId);
            st.put("state", "starting");
            st.put("current_item", itemId);
            st.put("current_event_id", eventId);
            st.put("current_user_id", userId);
            st.put("current_user_name", userName);
            st.put("buzzer_on", buzzer);
            st.put("updated_at", now());
            deviceStatus.put(deviceId, st);

            Map<String, Object> ev = new LinkedHashMap<String, Object>();
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
        return eventId;
    }

    public void sendStop(String deviceId, String itemId) {
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("cmd", "stop");
        payload.put("item_id", itemId);
        publish(String.format(CMD_TOPIC, deviceId), payload);
        // the real return to idle is recorded when the ESP32 reports status
    }

    // ---------- helpers ----------
    private void publish(String topic, Map<String, Object> payload) {
        try {
            MqttMessage m = new MqttMessage(om.writeValueAsBytes(payload));
            m.setQos(1);
            client.publish(topic, m);
        } catch (Exception e) {
            log.warn("publish failed to {}", topic, e);
        }
    }

    /** newest first, capped at MAX_EVENTS; caller holds the lock. */
    private void pushEvent(Map<String, Object> ev) {
        events.addFirst(ev);
        while (events.size() > MAX_EVENTS) {
            events.removeLast();
        }
    }

    private static double now() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static String shortId(int len) {
        return UUID.randomUUID().toString().replace("-", "").substring(0, len);
    }
}
