package dev.finditem.web;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Serves the shared H5 frontend at "/", same as the Python backend.
 */
@Controller
public class WebController {

    private final Path frontend;

    public WebController(@Value("${finditem.frontend:../frontend/index.html}") String frontendPath) {
        this.frontend = Path.of(frontendPath);
    }

    @GetMapping(value = "/", produces = MediaType.TEXT_HTML_VALUE)
    @ResponseBody
    public byte[] index() throws IOException {
        return Files.readAllBytes(frontend);
    }
}
