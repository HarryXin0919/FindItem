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
        Map<String, Object> data;
        try {
            data = om.readValue(Files.readAllBytes(path), Map.class);
        } catch (java.nio.file.NoSuchFileException e) {
            throw new CatalogException(503, "catalog_unavailable", "items.json not found at " + path);
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new CatalogException(503, "catalog_invalid", "items.json parse error: " + e.getOriginalMessage());
        } catch (IOException e) {
            throw new CatalogException(503, "catalog_unavailable", "cannot read catalog: " + e.getMessage());
        }
        // Parseable but malformed shape (e.g. items missing / not an array) -> 503 catalog_invalid,
        // matching the Python backend's isinstance check.
        if (data == null || !(data.get("items") instanceof List<?>)) {
            throw new CatalogException(503, "catalog_invalid", "items.json missing items list");
        }
        return data;
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
