from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import NODE_DESCRIPTION, Settings, get_settings
from app.logging_config import configure_logging
from app.routes_controllers import router as controllers_router
from app.routes_search import router as search_router

settings: Settings = get_settings()

configure_logging()

def announce_simulated_devices() -> None:
    """In simulator mode the five nodes exist from the moment the process
    starts, so apply their status once instead of leaving the dashboard
    reporting `unknown` for devices that are demonstrably running."""
    from app.database import SessionLocal
    from app.routes_controllers import get_device_bus
    from app.services.command_service import CommandService
    from app.services.mqtt_client import FakePublisher

    bus = get_device_bus()
    if bus is None:
        return
    with SessionLocal() as session:
        service = CommandService(session, FakePublisher())
        for payload in bus.status_payloads():
            try:
                service.handle_status(payload)
            except Exception:  # a missing seed is not a reason to fail startup
                session.rollback()
                return
        session.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    announce_simulated_devices()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

# The Vite dev server runs on a different origin during development. Locked to
# localhost only - this is not an authentication mechanism and S07 must not add
# one.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(search_router)
app.include_router(controllers_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Must stay dependency-free so it answers even if
    PostgreSQL or MQTT are down - readiness is a separate, later concern."""
    return {"status": "ok"}


@app.get("/api/architecture")
def architecture() -> dict:
    """The locked topology, served from configuration rather than literals."""
    return {
        "controllers": settings.controller_count,
        "leds_per_controller": settings.leds_per_controller,
        "total_drawers": settings.total_drawers,
        "node": NODE_DESCRIPTION,
        "topology": settings.architecture,
    }


@app.get("/api/config")
def config() -> dict:
    """Non-secret runtime configuration, for operators and the S10 gate.

    Passwords are never included; see `Settings.safe_dict`.
    """
    return settings.safe_dict()
