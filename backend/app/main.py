"""FastAPI 入口。

启动命令:
  python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8443 \
      --ssl-keyfile certs/server.key --ssl-certfile certs/server.crt

环境变量(可选):
  MQTT_HOST  默认 127.0.0.1
  MQTT_PORT  默认 1883
  MQTT_USER  默认 findit_backend
  MQTT_PASS  默认 findit123
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .mqtt_bridge import FindItBridge

# 项目根 = .../findit_mvp(从 backend/app/main.py 往上 3 层)
BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE_DIR / "config" / "items.json"
FRONTEND_DIR = BASE_DIR / "frontend"

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "findit_backend")
MQTT_PASS = os.environ.get("MQTT_PASS", "findit123")

bridge = FindItBridge(MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge.start()
    try:
        yield
    finally:
        bridge.stop()


app = FastAPI(title="FindIt MVP", lifespan=lifespan)


def _load_items() -> dict:
    """每次都重读 -> 改 items.json 不重启 FastAPI。"""
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


# ============ Pydantic 模型 ============

class SearchEvent(BaseModel):
    user_id: str = Field(..., min_length=1)
    user_name: str = Field(..., min_length=1)
    item_id: str = Field(..., min_length=1)
    action: Literal["start", "stop"]
    # 蜂鸣器开关 —— 每次 Find 时由前端勾选,默认响铃
    buzzer: bool = True
    # 可选覆盖 items.json 里的默认时长
    duration: int | None = Field(default=None, ge=1, le=120)


# ============ REST 端点 ============

@app.get("/api/items")
def get_items():
    return _load_items()


@app.post("/api/search-events")
def post_event(body: SearchEvent):
    items = _load_items()["items"]
    item = next((x for x in items if x["id"] == body.item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_item", "item_id": body.item_id})
    device_id = item["device_id"]

    if body.action == "start":
        # device_busy 策略
        if bridge.is_busy(device_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "device_busy",
                    "device_id": device_id,
                    "state": bridge.device_state(device_id),
                },
            )
        duration = body.duration or item.get("duration_sec", 15)
        event_id = bridge.send_start(
            device_id=device_id,
            item_id=body.item_id,
            user_id=body.user_id,
            user_name=body.user_name,
            duration=duration,
            buzzer=body.buzzer,
        )
        return {
            "ok": True,
            "event_id": event_id,
            "device_id": device_id,
            "duration": duration,
            "buzzer": body.buzzer,
        }

    # action == "stop"
    bridge.send_stop(device_id, body.item_id)
    return {"ok": True, "device_id": device_id}


@app.get("/api/devices")
def all_devices():
    """前端轮询用 —— 状态轮询。"""
    return {"devices": bridge.all_device_states()}


@app.get("/api/devices/{device_id}")
def device_state(device_id: str):
    return bridge.device_state(device_id)


@app.get("/api/events")
def get_events(limit: int = 50):
    """多用户事件时间线 —— 按 user_id 上色。"""
    return {"events": bridge.recent_events(limit=limit)}


# ============ 飞书 H5 前端托管 ============

@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# /static/* 提供前端资源(目前其实就一个 index.html,留口子未来加 js/css)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
