package dev.finditem.mqtt;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Bridge robustness: atomic busy check-and-set + publish-failure rollback (no broker needed). */
class FindItBridgeTest {

    /** A bridge whose publish() is stubbed, so no real MQTT client/broker is needed. */
    private FindItBridge bridge(final boolean publishOk) {
        return new FindItBridge("127.0.0.1", 1883, "u", "p") {
            @Override
            protected boolean publish(String topic, Map<String, Object> payload) {
                return publishOk;
            }
        };
    }

    @Test
    void tryStartIsAtomicAndBusyBlocksSecond() {
        FindItBridge b = bridge(true);
        String eid = b.tryStart("d1", "i1", "u1", "Harry", 15, true);
        assertNotNull(eid);
        assertTrue(b.isBusy("d1"));
        assertNull(b.tryStart("d1", "i2", "u2", "Bob", 15, false)); // already ringing -> refused
    }

    @Test
    void publishFailureRollsBack() {
        FindItBridge b = bridge(false);
        assertNull(b.tryStart("d1", "i1", "u1", "H", 15, true)); // command not sent (broker down)
        assertFalse(b.isBusy("d1"));                             // rolled back, item not wedged
    }
}
