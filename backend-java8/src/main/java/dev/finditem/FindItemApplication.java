package dev.finditem;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * FindItem backend (Java 8 / Spring Boot 2.7).
 *
 * Same behaviour as the Java 17 version in ../backend-java, kept Java-8 compatible
 * (no records, no var, javax.* instead of jakarta.*) so it compiles and runs on
 * an existing JDK 8 install without upgrading the toolchain.
 */
@SpringBootApplication
public class FindItemApplication {
    public static void main(String[] args) {
        SpringApplication.run(FindItemApplication.class, args);
    }
}
