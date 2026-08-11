package dev.finditem;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * FindItem backend (Java / Spring Boot).
 *
 * A drop-in alternative to the Python/FastAPI backend: identical REST endpoints
 * and the same MQTT command/status contract. The frontend, firmware, broker
 * config, and config/items.json are shared unchanged.
 */
@SpringBootApplication
public class FindItemApplication {
    public static void main(String[] args) {
        SpringApplication.run(FindItemApplication.class, args);
    }
}
