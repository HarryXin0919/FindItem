"""Typed application settings for the FindIt backend.

Every value comes from the environment (or `09_Code/.env`, which is git-ignored).
Nothing in this module may be logged verbatim: use `Settings.safe_dict()` when
you need to show configuration, and `Settings.redacted_database_url` when you
need to show where the database lives.

The locked topology `(ESP32-C3 + MCP23017 + 10 x WS2812) x 5` is expressed here
as three constants that the rest of the backend derives from. They are frozen
and validated so a topology change cannot enter through configuration.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 09_Code/.env - the shared, git-ignored environment file described by
# 09_Code/.env.example.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

ARCHITECTURE = "(ESP32-C3 + MCP23017 + 10 x WS2812) x 5"
NODE_DESCRIPTION = "ESP32-C3 + MCP23017 + 10 x WS2812"


class Settings(BaseSettings):
    """Runtime configuration. Secrets are never given a real default."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_name: str = "FindIt Plan B API"
    app_version: str = "0.2.0"
    environment: str = Field(default="development")

    # --- Locked topology -----------------------------------------------------
    controller_count: int = 5
    leds_per_controller: int = 10

    # --- Database ------------------------------------------------------------
    # The local development credentials are the ones already published in
    # 09_Code/.env.example and docker-compose.postgres.yml. Any real deployment
    # must override DATABASE_URL from the environment.
    database_url: str = "postgresql+psycopg://findit:findit_dev@localhost:5432/findit"

    # --- Device bus ----------------------------------------------------------
    # "simulator" runs the five-node simulator in-process (S01-S10, no hardware).
    # "broker" talks to a real MQTT broker and is not used before S12.
    device_mode: str = "simulator"

    # --- MQTT ----------------------------------------------------------------
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""

    @field_validator("controller_count")
    @classmethod
    def _controllers_locked(cls, v: int) -> int:
        if v != 5:
            raise ValueError(
                "controller_count is locked to 5 by ADR-001; a change needs a new ADR"
            )
        return v

    @field_validator("leds_per_controller")
    @classmethod
    def _leds_locked(cls, v: int) -> int:
        if v != 10:
            raise ValueError(
                "leds_per_controller is locked to 10 by ADR-001; a change needs a new ADR"
            )
        return v

    @field_validator("device_mode")
    @classmethod
    def _known_device_mode(cls, v: str) -> str:
        if v not in {"simulator", "broker"}:
            raise ValueError("device_mode must be 'simulator' or 'broker'")
        return v

    @field_validator("mqtt_port")
    @classmethod
    def _port_in_range(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("mqtt_port must be a valid TCP port")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_drawers(self) -> int:
        return self.controller_count * self.leds_per_controller

    @computed_field  # type: ignore[prop-decorator]
    @property
    def architecture(self) -> str:
        return ARCHITECTURE

    @property
    def redacted_database_url(self) -> str:
        """The database URL with any password replaced by `***`."""
        parts = urlsplit(self.database_url)
        if parts.password is None:
            return self.database_url
        user = parts.username or ""
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{user}:***@{host}{port}" if user else f"***@{host}{port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def safe_dict(self) -> dict[str, object]:
        """Configuration with every secret removed - safe to log or serve."""
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "environment": self.environment,
            "architecture": self.architecture,
            "controller_count": self.controller_count,
            "leds_per_controller": self.leds_per_controller,
            "total_drawers": self.total_drawers,
            "database": self.redacted_database_url,
            "mqtt_host": self.mqtt_host,
            "mqtt_port": self.mqtt_port,
            "mqtt_authenticated": bool(self.mqtt_username),
        }


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton. Cached so .env is read once."""
    return Settings()
