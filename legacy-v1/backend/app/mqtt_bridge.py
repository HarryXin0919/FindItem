"""MQTT 桥:把后端业务命令翻译成 findit/device/.../command,
并订阅 findit/device/+/status 维护设备状态 + 事件流水。

设计要点:
- 一个 device_id 同时只能有一个 ringing 任务 -> device_busy 策略
- 状态变化由 ESP32 通过 status 主题反推回来,后端只发命令不假设结果
- 事件流水是内存 deque,demo 重启会清空;真实生产可换数据库
- 未收到设备状态时保留 state=unknown;命令能否下发以 MQTT publish 结果为准
- publish 失败时只回滚本次设备占位与本次 started 事件,不影响其他设备的并发事件
- stop 命令不乐观改 idle,只有设备回报 idle 才算停止
- MQTT payload 严格校验:非对象、非字符串 state、畸形字段均安全拒绝/降级
"""
from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from typing import Any

import paho.mqtt.client as mqtt

log = logging.getLogger("finditem.mqtt")

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

DEVICE_TOPIC_CMD = "findit/device/{device_id}/command"
DEVICE_TOPIC_STATUS_SUB = "findit/device/+/status"

STALE_GRACE_SEC = 10
MAX_RING_DURATION = 300

KNOWN_STATES = {"idle", "starting", "ringing", "unknown"}
BUSY_STATES = {"starting", "ringing"}


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
        self._events: deque[dict[str, Any]] = deque(maxlen=200)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        try:
            self._client.connect_async(self._host, self._port, keepalive=30)
        except Exception as e:
            log.warning("MQTT connect_async 失败(将后台重试): %s", e)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # ---------- MQTT 回调 ----------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        failed = getattr(reason_code, "is_failure", None)
        if failed is None:
            failed = bool(reason_code) and reason_code != 0
        if failed:
            log.error("MQTT 连接失败:%s —— 命令无法下发,请检查 broker 地址/账号密码", reason_code)
            return
        log.info("MQTT 已连接,订阅 %s", DEVICE_TOPIC_STATUS_SUB)
        client.subscribe(DEVICE_TOPIC_STATUS_SUB, qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            self._handle_status_message(msg)
        except Exception:
            log.exception("_on_message 处理异常,已丢弃")

    def _handle_status_message(self, msg) -> None:
        parts = msg.topic.split("/")
        if len(parts) != 4 or parts[0] != "findit" or parts[3] != "status":
            return
        device_id = parts[2]
        if not DEVICE_ID_RE.match(device_id):
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        valid_state: str | None = None
        raw_state = payload.get("state")
        if raw_state is not None:
            if not isinstance(raw_state, str):
                return
            if raw_state in KNOWN_STATES:
                valid_state = raw_state
            else:
                valid_state = "unknown"

        clean_payload: dict[str, Any] = {}
        for k, v in payload.items():
            if k == "state":
                continue
            if k == "_ring_duration":
                if self._is_valid_duration(v):
                    clean_payload[k] = int(v)
                continue
            if k in ("current_item", "current_event_id", "current_user_id", "current_user_name"):
                if isinstance(v, str):
                    clean_payload[k] = v
                continue
            clean_payload[k] = v

        with self._lock:
            prev = self._device_status.get(device_id, {})

            prev_state = prev.get("state", "unknown")

            if valid_state is not None and prev_state in BUSY_STATES and valid_state == "unknown":
                return

            if valid_state is not None:
                merged = {**prev, **clean_payload, "device_id": device_id, "updated_at": time.time()}
                merged["state"] = valid_state
            else:
                merged = {**prev, **clean_payload, "device_id": device_id, "updated_at": time.time()}
                if "state" not in merged:
                    merged["state"] = "unknown"

            self._device_status[device_id] = merged

            new_state = merged.get("state")

            if new_state == "ringing" and prev_state == "starting":
                self._events.appendleft({
                    "type": "device_ringing",
                    "device_id": device_id,
                    "event_id": prev.get("current_event_id"),
                    "item_id": prev.get("current_item"),
                    "user_id": prev.get("current_user_id"),
                    "user_name": prev.get("current_user_name"),
                    "ts": time.time(),
                })

            if new_state == "idle" and prev_state in BUSY_STATES:
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
                for k in ("current_item", "current_event_id", "current_user_id",
                          "current_user_name", "buzzer_on", "_ring_duration"):
                    merged.pop(k, None)

    @staticmethod
    def _is_valid_duration(v: Any) -> bool:
        if isinstance(v, bool):
            return False
        if not isinstance(v, (int, float)):
            return False
        if v != v or v == float("inf") or v == float("-inf"):
            return False
        if v <= 0:
            return False
        if v > MAX_RING_DURATION:
            return False
        return True

    # ---------- 公共查询 ----------
    def _is_busy_locked(self, device_id: str) -> bool:
        s = self._device_status.get(device_id, {})
        if s.get("state") not in BUSY_STATES:
            return False
        dur = s.get("_ring_duration", 15)
        if not isinstance(dur, (int, float)) or dur <= 0:
            dur = 15
        updated = s.get("updated_at", 0)
        if time.time() - updated > dur + STALE_GRACE_SEC:
            s["state"] = "idle"
            self._events.appendleft({
                "type": "stopped",
                "device_id": device_id,
                "event_id": s.get("current_event_id"),
                "item_id": s.get("current_item"),
                "user_id": s.get("current_user_id"),
                "user_name": s.get("current_user_name"),
                "stop_reason": "timeout",
                "ts": time.time(),
            })
            for k in ("current_item", "current_event_id", "current_user_id",
                      "current_user_name", "buzzer_on", "_ring_duration"):
                s.pop(k, None)
            return False
        return True

    def is_busy(self, device_id: str) -> bool:
        with self._lock:
            return self._is_busy_locked(device_id)

    def device_state(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            if device_id in self._device_status:
                self._is_busy_locked(device_id)
                return dict(self._device_status[device_id])
            return {"device_id": device_id, "state": "unknown"}

    def all_device_states(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            # 前端轮询此接口;读取时顺便清理没有收到设备确认的过期占位。
            for device_id in list(self._device_status):
                self._is_busy_locked(device_id)
            return {k: dict(v) for k, v in self._device_status.items()}

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[:limit]

    # ---------- 发命令 ----------
    def try_start(
        self,
        device_id: str,
        item_id: str,
        user_id: str,
        user_name: str,
        duration: int,
        buzzer: bool,
    ) -> str | None:
        """原子地「检查忙→占位→发命令」。

        返回 event_id 表示成功;返回 None 表示失败(调用方根据上下文决定 409 还是 503)。
        check-and-set 在同一把锁里完成,杜绝两个并发 start 都通过 busy 判断的竞态。
        publish 失败时回滚设备快照并只删除本次事件,不影响其他设备的并发事件。
        """
        event_id = uuid.uuid4().hex[:12]
        snapshot: dict[str, Any] | None = None

        with self._lock:
            if self._is_busy_locked(device_id):
                return None

            snapshot = copy.deepcopy(self._device_status.get(device_id, {}))

            self._device_status[device_id] = {
                **self._device_status.get(device_id, {}),
                "device_id": device_id,
                "state": "starting",
                "current_item": item_id,
                "current_event_id": event_id,
                "current_user_id": user_id,
                "current_user_name": user_name,
                "buzzer_on": bool(buzzer),
                "_ring_duration": int(duration),
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

        publish_ok = True
        publish_error = None
        try:
            payload = {
                "cmd": "start",
                "item_id": item_id,
                "event_id": event_id,
                "duration": int(duration),
                "buzzer": bool(buzzer),
            }
            info = self._client.publish(
                DEVICE_TOPIC_CMD.format(device_id=device_id),
                json.dumps(payload),
                qos=1,
            )
            if getattr(info, "rc", 0) != mqtt.MQTT_ERR_SUCCESS:
                publish_ok = False
                publish_error = f"rc={info.rc}"
        except Exception as e:
            publish_ok = False
            publish_error = str(e)

        if not publish_ok:
            log.warning("start publish 返回失败(%s),核对 %s 的设备确认后再回滚", publish_error, device_id)
            with self._lock:
                cur = self._device_status.get(device_id, {})
                # MQTT 回调线程可能在 publish() 返回错误前已经收到同一事件的
                # ringing 确认。设备确认比本地 rc 更强,此时命令实际已送达。
                if (
                    cur.get("current_event_id") == event_id
                    and cur.get("state") == "ringing"
                ):
                    log.warning(
                        "publish 返回失败,但设备 %s 已确认事件 %s;按成功处理",
                        device_id,
                        event_id,
                    )
                    return event_id

                if (
                    cur.get("current_event_id") == event_id
                    and cur.get("state") == "starting"
                ):
                    if snapshot:
                        self._device_status[device_id] = copy.deepcopy(snapshot)
                    else:
                        self._device_status.pop(device_id, None)
                self._events = deque(
                    (
                        event for event in self._events
                        if not (
                            event.get("type") == "started"
                            and event.get("device_id") == device_id
                            and event.get("event_id") == event_id
                        )
                    ),
                    maxlen=self._events.maxlen,
                )
            return None
        return event_id

    def send_stop(self, device_id: str, item_id: str) -> bool:
        """发送停止命令。不乐观改 idle —— 只有设备回报 idle 才算停止。

        返回 True 表示 publish 成功,False 表示失败。
        """
        try:
            payload = {"cmd": "stop", "item_id": item_id}
            info = self._client.publish(
                DEVICE_TOPIC_CMD.format(device_id=device_id),
                json.dumps(payload),
                qos=1,
            )
            if getattr(info, "rc", 0) != mqtt.MQTT_ERR_SUCCESS:
                log.warning("stop 命令未发出(rc=%s),设备 %s", info.rc, device_id)
                return False
        except Exception as e:
            log.warning("stop 命令抛出异常(%s),设备 %s", e, device_id)
            return False
        return True
