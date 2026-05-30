package dev.finditem.model;

/**
 * Request body for POST /api/search-events (Java 8 — plain POJO with snake_case
 * fields so Jackson binds the JSON keys directly).
 *
 * {@code buzzer} defaults to true and {@code duration} is optional (falls back to
 * the item's {@code duration_sec}); both are boxed so absence is distinguishable.
 */
public class SearchEvent {
    public String user_id;
    public String user_name;
    public String item_id;
    public String action;     // "start" | "stop"
    public Boolean buzzer;
    public Integer duration;

    public String getUser_id()   { return user_id; }
    public String getUser_name() { return user_name; }
    public String getItem_id()   { return item_id; }
    public String getAction()    { return action; }
    public Boolean getBuzzer()   { return buzzer; }
    public Integer getDuration() { return duration; }

    public void setUser_id(String v)   { this.user_id = v; }
    public void setUser_name(String v) { this.user_name = v; }
    public void setItem_id(String v)   { this.item_id = v; }
    public void setAction(String v)    { this.action = v; }
    public void setBuzzer(Boolean v)   { this.buzzer = v; }
    public void setDuration(Integer v) { this.duration = v; }
}
