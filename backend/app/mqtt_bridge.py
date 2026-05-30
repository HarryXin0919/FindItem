"""MQTT 桥:把后端业务命令翻译成 findit/device/.../command,
并订阅 findit/device/+/status 维护设备状态 + 事件流水。

设计要点:
- 一个 device_id 同时只能有一个 ringing 任务 -> device_busy 策略
- 状态变化由 ESP32 通过 status 主题反推回来,后端只发命令不假设结果
- 事件流水是内存 deque,demo 重启会清空;真实生产可换数据库
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from typing import Any

import paho.mqtt.client as mqtt

# topic 模板 —— 和 mosquitto_pub 测试命令完全一致
DEVICE_TOPIC_CMD = "findit/device/{device_id}/command"
DEVICE_TOPIC_STATUS_SUB = "findit/device/+/status"


class FindItBridge:
    def __init__(self, host: str, port: int, user: str, password: str):
        self._host = host
        self._port = port
        self._client = mqtt.Client(
            client_id=f"findit-backend-{uuid.uuid4().hex[:6]}",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._client.username_pw_set(user, password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        self._lock = threading.Lock()
        self._device_status: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=200)  # 最新在前

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._client.connect(self._host, self._port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # ---------- MQTT 回调 ----------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(DEVICE_TOPIC_STATUS_SUB, qos=1)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        # 期望: findit / device / {device_id} / status
        if len(parts) != 4 or parts[0] != "findit" or parts[3] != "status":
            return
        device_id = parts[2]
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        with self._lock:
            prev = self._device_status.get(device_id, {})
            merged = {**prev, **payload, "device_id": device_id, "updated_at": time.time()}
            self._device_status[device_id] = merged

            # 设备从响铃回到 idle => 记一条 stopped 事件,附原因
            if payload.get("state") == "idle" and prev.get("state") in ("starting", "ringing"):
                self._events.appendleft({
                    "type": "stopped",
                    "device_id": device_id,
                    "event_id": prev.get("current_event_id"),
                    "item_id": prev.get("current_item"),
                    "user_id": prev.get("current_user_id"),
                    "user_name": prev.get("current_user_name"),
                    "stop_reason": payload.get("stop_reason", "auto"),
                    "ts": time.time(),
                })

    # ---------- 公共查询 ----------
    def is_busy(self, device_id: str) -> bool:
        with self._lock:
            s = self._device_status.get(device_id, {})
            return s.get("state") in ("starting", "ringing")

    def device_state(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._device_status.get(device_id, {"device_id": device_id, "state": "unknown"}))

    def all_device_states(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._device_status.items()}

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[:limit]

    # ---------- 发命令 ----------
    def send_start(
        self,
        device_id: str,
        item_id: str,
        user_id: str,
        user_name: str,
        duration: int,
        buzzer: bool,
    ) -> str:
        """publish start;返回 event_id 让前端能 join 事件。"""
        event_id = uuid.uuid4().hex[:12]
        payload = {
            "cmd": "start",
            "item_id": item_id,
            "event_id": event_id,
            "duration": int(duration),
            "buzzer": bool(buzzer),  # << 蜂鸣器 toggle 在这里下到设备
        }
        self._client.publish(
            DEVICE_TOPIC_CMD.format(device_id=device_id),
            json.dumps(payload),
            qos=1,
        )
        with self._lock:
            self._device_status[device_id] = {
                "device_id": device_id,
                "state": "starting",
                "current_item": item_id,
                "current_event_id": event_id,
                "current_user_id": user_id,
                "current_user_name": user_name,
                "buzzer_on": bool(buzzer),
                "updated_at": time.time(),
            }
            self._events.appendleft({
                "type": "started",
                "device_id": device_id,
                "event_id": event_id,
                "item_id": item_id,
                "user_id": user_id,
                "user_name": user_name,
                "buzzer": bool(buzzer),
                "duration": int(duration),
                "ts": time.time(),
            })
        return event_id

    def send_stop(self, device_id: str, item_id: str) -> None:
        payload = {"cmd": "stop", "item_id": item_id}
        self._client.publish(
            DEVICE_TOPIC_CMD.format(device_id=device_id),
            json.dumps(payload),
            qos=1,
        )
        # 状态真正回 idle 由 ESP32 推 status 时更新
