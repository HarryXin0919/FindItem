package dev.finditem.catalog;

/**
 * The part catalog couldn't be read or parsed. Surfaced as HTTP 503 (retryable)
 * rather than a generic 500, matching the Python backend's catalog_unavailable /
 * catalog_invalid responses so the frontend can tell "try again" from "broken".
 */
public class CatalogException extends RuntimeException {
    public final int status;
    public final String code;

    public CatalogException(int status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }
}
