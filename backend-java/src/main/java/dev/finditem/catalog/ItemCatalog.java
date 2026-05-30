package dev.finditem.catalog;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * Reads the part catalog from config/items.json on every call, so editing the
 * file is picked up without restarting the backend (hot reload).
 */
@Component
public class ItemCatalog {

    private final ObjectMapper om = new ObjectMapper();
    private final Path path;

    public ItemCatalog(@Value("${finditem.catalog:../config/items.json}") String catalogPath) {
        this.path = Path.of(catalogPath);
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> load() {
        try {
            return om.readValue(Files.readAllBytes(path), Map.class);
        } catch (IOException e) {
            throw new UncheckedIOException("cannot read catalog at " + path, e);
        }
    }

    /** Returns the item map for {@code itemId}, or null if not found. */
    @SuppressWarnings("unchecked")
    public Map<String, Object> findItem(String itemId) {
        Object items = load().get("items");
        if (items instanceof List<?> list) {
            for (Object o : list) {
                if (o instanceof Map<?, ?> m && itemId.equals(m.get("id"))) {
                    return (Map<String, Object>) m;
                }
            }
        }
        return null;
    }
}
