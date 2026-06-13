"""MQTT 桥:把后端业务命令翻译成 findit/device/.../command,
并订阅 findit/device/+/status 维护设备状态 + 事件流水。

设计要点:
- 一个 device_id 同时只能有一个 ringing 任务 -> device_busy 策略
- 状态变化由 ESP32 通过 status 主题反推回来,后端只发命令不假设结果
- 事件流水是内存 deque,demo 重启会清空;真实生产可换数据库
"""
from __future__ import annotations

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

# 设备 id 白名单 —— device_id 来自不可信的 MQTT status 主题,会被原样回显到前端事件流,
# 只放行字母数字与 . _ -,不匹配的整条消息直接丢弃(纵深防御,杜绝 XSS 注入)。
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# topic 模板 —— 和 mosquitto_pub 测试命令完全一致
DEVICE_TOPIC_CMD = "findit/device/{device_id}/command"
DEVICE_TOPIC_STATUS_SUB = "findit/device/+/status"

# 设备状态新鲜度:一个 starting/ringing 状态超过 (duration + 宽限) 还没收到设备更新,
# 就认定设备掉线/根本不在,不再视为 busy —— 否则一次 Find 会把物品永久锁死。
STALE_GRACE_SEC = 10


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
        """非阻塞启动:broker 没起也不会让后端崩。后台线程自动重连,连上后由
        on_connect 重新订阅。"""
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        try:
            self._client.connect_async(self._host, self._port, keepalive=30)
        except Exception as e:  # 域名解析失败等
            log.warning("MQTT connect_async 失败(将后台重试): %s", e)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # ---------- MQTT 回调 ----------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # reason_code 为真/非 0 表示连接失败(认证错误、broker 不可用等),订阅没有意义。
        failed = getattr(reason_code, "is_failure", None)
        if failed is None:
            failed = bool(reason_code) and reason_code != 0
        if failed:
            log.error("MQTT 连接失败:%s —— 命令无法下发,请检查 broker 地址/账号密码", reason_code)
            return
        log.info("MQTT 已连接,订阅 %s", DEVICE_TOPIC_STATUS_SUB)
        client.subscribe(DEVICE_TOPIC_STATUS_SUB, qos=1)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        # 期望: findit / device / {device_id} / status
        if len(parts) != 4 or parts[0] != "findit" or parts[3] != "status":
            return
        device_id = parts[2]
        # device_id 不合法 -> 丢弃,避免污染状态/事件流(纵深防御)
        if not DEVICE_ID_RE.match(device_id):
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        with self._lock:
            prev = self._device_status.get(device_id, {})
            # 设备 idle 时上报的空 id 不要覆盖缓存里的有效值(重连会重发空 id)
            clean = {k: v for k, v in payload.items()
                     if not (k in ("current_item", "current_event_id") and v in ("", None))}
            merged = {**prev, **clean, "device_id": device_id, "updated_at": time.time()}
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
    def _is_busy_locked(self, device_id: str) -> bool:
        """调用方必须已持有 self._lock。带新鲜度判断:状态过期就不算 busy。"""
        s = self._device_status.get(device_id, {})
        if s.get("state") not in ("starting", "ringing"):
            return False
        dur = s.get("_ring_duration", 15)
        updated = s.get("updated_at", 0)
        if time.time() - updated > dur + STALE_GRACE_SEC:
            # 设备掉线/从未上线:状态陈旧,解锁,记一条 timeout 事件
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
            return False
        return True

    def is_busy(self, device_id: str) -> bool:
        with self._lock:
            return self._is_busy_locked(device_id)

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
    def try_start(
        self,
        device_id: str,
        item_id: str,
        user_id: str,
        user_name: str,
        duration: int,
        buzzer: bool,
    ) -> str | None:
        """原子地「检查忙→占位→发命令」。返回 event_id;若设备忙则返回 None(调用方回 409)。
        check-and-set 在同一把锁里完成,杜绝两个并发 start 都通过 busy 判断的竞态。"""
        event_id = uuid.uuid4().hex[:12]
        with self._lock:
            if self._is_busy_locked(device_id):
                return None
            self._device_status[device_id] = {
                "device_id": device_id,
                "state": "starting",
                "current_item": item_id,
                "current_event_id": event_id,
                "current_user_id": user_id,
                "current_user_name": user_name,
                "buzzer_on": bool(buzzer),
                "_ring_duration": int(duration),  # 给新鲜度判断用
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

        payload = {
            "cmd": "start",
            "item_id": item_id,
            "event_id": event_id,
            "duration": int(duration),
            "buzzer": bool(buzzer),  # << 蜂鸣器 toggle 在这里下到设备
        }
        info = self._client.publish(
            DEVICE_TOPIC_CMD.format(device_id=device_id),
            json.dumps(payload),
            qos=1,
        )
        if getattr(info, "rc", 0) != mqtt.MQTT_ERR_SUCCESS:
            # 没发出去(broker 没连上):回滚占位,别把物品锁死
            log.warning("start 命令未发出(rc=%s),回滚 %s 的占位状态", info.rc, device_id)
            with self._lock:
                cur = self._device_status.get(device_id, {})
                if cur.get("current_event_id") == event_id:
                    cur["state"] = "idle"
            return None
        return event_id

    def send_stop(self, device_id: str, item_id: str) -> None:
        payload = {"cmd": "stop", "item_id": item_id}
        self._client.publish(
            DEVICE_TOPIC_CMD.format(device_id=device_id),
            json.dumps(payload),
            qos=1,
        )
        # 乐观地把状态推向 idle,让前端在没有设备回报时也能恢复;
        # 设备真正回 idle 时的 status 会再覆盖一次(幂等)。
        with self._lock:
            cur = self._device_status.get(device_id)
            if cur and cur.get("state") in ("starting", "ringing"):
                cur["state"] = "idle"
                cur["updated_at"] = time.time()
