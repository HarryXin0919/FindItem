package dev.finditem.web;

import dev.finditem.catalog.CatalogException;
import dev.finditem.catalog.ItemCatalog;
import dev.finditem.mqtt.FindItBridge;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** REST-contract tests (bridge + catalog mocked; no broker, no network). */
@WebMvcTest(ApiController.class)
class ApiControllerTest {

    @Autowired
    MockMvc mvc;
    @MockBean
    ItemCatalog catalog;
    @MockBean
    FindItBridge bridge;

    private static final String START =
            "{\"user_id\":\"u1\",\"user_name\":\"Harry\",\"item_id\":\"FINDIT-001\",\"action\":\"start\"}";

    private Map<String, Object> item() {
        Map<String, Object> m = new HashMap<String, Object>();
        m.put("id", "FINDIT-001");
        m.put("device_id", "esp32-001");
        m.put("duration_sec", 15);
        return m;
    }

    @Test
    void itemsOk() throws Exception {
        Map<String, Object> cat = new HashMap<String, Object>();
        cat.put("items", Arrays.asList(item()));
        when(catalog.load()).thenReturn(cat);
        mvc.perform(get("/api/items")).andExpect(status().isOk());
    }

    @Test
    void itemsCatalogUnavailableReturns503() throws Exception {
        when(catalog.load()).thenThrow(new CatalogException(503, "catalog_unavailable", "missing"));
        mvc.perform(get("/api/items"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("catalog_unavailable"));
    }

    @Test
    void startOk() throws Exception {
        when(catalog.findItem("FINDIT-001")).thenReturn(item());
        when(bridge.tryStart(anyString(), anyString(), anyString(), anyString(), anyInt(), anyBoolean()))
                .thenReturn("evt-1");
        mvc.perform(post("/api/search-events").contentType(MediaType.APPLICATION_JSON).content(START))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.event_id").value("evt-1"));
    }

    @Test
    void startBusyReturns409() throws Exception {
        when(catalog.findItem("FINDIT-001")).thenReturn(item());
        when(bridge.deviceState(anyString())).thenReturn(new HashMap<String, Object>());
        when(bridge.tryStart(anyString(), anyString(), anyString(), anyString(), anyInt(), anyBoolean()))
                .thenReturn(null);
        mvc.perform(post("/api/search-events").contentType(MediaType.APPLICATION_JSON).content(START))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("device_busy"));
    }

    @Test
    void unknownItemReturns404() throws Exception {
        when(catalog.findItem("NOPE")).thenReturn(null);
        String body = "{\"user_id\":\"u1\",\"user_name\":\"H\",\"item_id\":\"NOPE\",\"action\":\"start\"}";
        mvc.perform(post("/api/search-events").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("unknown_item"));
    }

    @Test
    void invalidActionReturns400() throws Exception {
        String body = "{\"user_id\":\"u1\",\"user_name\":\"H\",\"item_id\":\"FINDIT-001\",\"action\":\"boom\"}";
        mvc.perform(post("/api/search-events").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }
}
