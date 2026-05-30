<div align="center">

# FindItem · 寻物

**Light up the bin that holds your part.**
A decentralized parts locator — search a part, the right storage box rings and glows.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
![Platform](https://img.shields.io/badge/MCU-ESP32-informational)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Transport](https://img.shields.io/badge/transport-MQTT-660066)

[English](#english) · [中文](#中文)

</div>

---

## English

### What it does

FindItem turns any set of storage bins into a searchable, addressable system. You
search for a part from your phone or a web page; the bin that holds it **lights an
LED and sounds a buzzer** so you can grab it in seconds. Each bin runs independently
(decentralized) and is driven over MQTT, so the system scales from one drawer to a
whole wall without a central controller in the loop.

Built for makerspaces, labs, and robotics teams — anywhere "which box is it in?" is
a daily question.

### How it works

```
phone / web  ──HTTPS──▶  FastAPI  ──MQTT──▶  Mosquitto  ──MQTT──▶  ESP32 (LED + buzzer)
```

The frontend only ever talks HTTPS to the backend. The backend is the single place
that publishes MQTT commands to devices — clients never touch the broker directly.

### Features

- 🔦 **Visual + audio locate** — WS2812 / LED lights the target bin, buzzer confirms
- 🔕 **Per-search buzzer toggle** — light-only mode for quiet rooms, end to end
- 👥 **Multi-user** — concurrent searches, each user gets a distinct color
- 🔁 **Live status & event feed** — poll device state and a recent-activity timeline
- 🧩 **Hot-reloadable catalog** — edit `config/items.json`, no backend restart
- 🔒 **Auth'd MQTT + HTTPS** — broker requires credentials; self-signed certs for LAN

### Tech stack

| Layer | Tech |
|---|---|
| Firmware | ESP32 · Arduino (C++) · PubSubClient · ArduinoJson |
| Backend | Python · FastAPI · paho-mqtt **— or** Java · Spring Boot · Eclipse Paho (see [`backend-java/`](./backend-java)) |
| Broker | Mosquitto (MQTT) |
| Frontend | Vanilla HTML / CSS / JS |

> Two interchangeable backends ship with the same REST + MQTT contract: the Python
> one in [`backend/`](./backend) and a Java / Spring Boot one in
> [`backend-java/`](./backend-java). Run either against the same firmware, broker,
> and frontend.

### Quick start (Windows + PowerShell)

> Requires Python, Mosquitto, and Git for Windows (ships with `openssl`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

.\scripts\init-mqtt-passwd.ps1   # create the MQTT password file
.\scripts\gen-certs.ps1          # generate a self-signed HTTPS cert
.\scripts\start-all.ps1          # start Mosquitto + the backend
```

The backend listens on `https://0.0.0.0:8443`. On the same LAN, open
`https://<your-laptop-ip>:8443` from a phone (accept the self-signed cert warning).

### Flash the firmware

1. Install the ESP32 board package in Arduino IDE.
2. Install libraries **PubSubClient** and **ArduinoJson**.
3. Open `esp32/findit_esp32.ino` and edit the config block at the top:
   `WIFI_SSID` / `WIFI_PASSWORD`, `MQTT_HOST` (your laptop's LAN IP), and a unique
   `DEVICE_ID` matching an entry in `config/items.json`.
4. Select board + port, upload. The device connects to Wi-Fi → MQTT on boot.

Default wiring (override the macros at the top of the `.ino`):

| Component | GPIO | Notes |
|---|---|---|
| LED | 2 | active-high (onboard LED works) |
| Buzzer | 5 | passive buzzer, driven with `tone()` at 2 kHz |
| Button | 4 | to GND, `INPUT_PULLUP`, press = low (stops the ring) |

### API

**HTTPS REST** (frontend ↔ backend)

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/items` | catalog (hot-reloaded) |
| `POST` | `/api/search-events` | start / stop a locate |
| `GET`  | `/api/devices` | all device states |
| `GET`  | `/api/devices/{id}` | one device |
| `GET`  | `/api/events?limit=N` | recent event feed |

`POST /api/search-events`:

```json
{ "user_id": "u1", "user_name": "Alice", "item_id": "FINDIT-001", "action": "start", "buzzer": true, "duration": 15 }
```

`buzzer:false` → light only. Omit `duration` to use the item's default. A busy device
returns `409 device_busy`.

**MQTT** (backend ↔ ESP32)

| Topic | Direction |
|---|---|
| `findit/device/{id}/command` | backend → device |
| `findit/device/{id}/status` | device → backend |

```json
// command
{"cmd":"start","item_id":"FINDIT-001","event_id":"...","duration":15,"buzzer":true}
// status
{"state":"ringing","device_id":"esp32-001","buzzer_on":true}
{"state":"idle","stop_reason":"button"}   // button | timeout | backend
```

### Project layout

```
backend/app/main.py         FastAPI app & REST endpoints
backend/app/mqtt_bridge.py  MQTT client, device state, event log
frontend/index.html         web client (with buzzer toggle)
config/items.json           part catalog (hot-reloaded)
mosquitto/mosquitto.conf    broker config
esp32/findit_esp32.ino      device firmware
scripts/*.ps1               cert / password / launch helpers
```

### Security

`mosquitto/passwords` and the TLS keys under `certs/` are gitignored and never
committed. The `findit123` in the code is a **placeholder default** — change it
before any real deployment and update both the ESP32 sketch and the backend's
`MQTT_PASS`. The demo uses a self-signed certificate; for a public domain, terminate
HTTPS with Let's Encrypt or a Cloudflare Tunnel.

### License

[MIT](./LICENSE) © 2026 Harry Xin ([@HarryXin0919](https://github.com/HarryXin0919))

---

## 中文

### 是什么

FindItem 把一组存储箱变成可搜索、可寻址的系统。你在手机或网页上搜一个零件,装着
它的箱子就会**亮灯 + 响铃**,几秒内拿到。每个箱子独立运行(去中心化),通过 MQTT
驱动,从一个抽屉到一整面墙都能扩展,无需中心控制器实时介入。

适合创客空间、实验室、机器人队 —— 任何"这玩意儿在哪个箱"是日常问题的场景。

### 工作原理

```
手机 / 网页  ──HTTPS──▶  FastAPI  ──MQTT──▶  Mosquitto  ──MQTT──▶  ESP32(LED + 蜂鸣器)
```

前端只通过 HTTPS 与后端通信;后端是唯一向设备发布 MQTT 命令的地方 —— 客户端**永远
不直连 broker**。

### 功能

- 🔦 **声光定位** —— LED 点亮目标箱,蜂鸣器确认
- 🔕 **逐次蜂鸣器开关** —— 安静场合可只亮灯,开关贯通全链路
- 👥 **多用户并发** —— 同时查找,每人一个独立颜色
- 🔁 **实时状态与事件流** —— 轮询设备状态 + 近期活动时间线
- 🧩 **热加载物品库** —— 改 `config/items.json` 不用重启后端
- 🔒 **MQTT 鉴权 + HTTPS** —— broker 要求账密,局域网用自签证书

### 技术栈

| 层 | 技术 |
|---|---|
| 固件 | ESP32 · Arduino(C++)· PubSubClient · ArduinoJson |
| 后端 | Python · FastAPI · paho-mqtt |
| Broker | Mosquitto(MQTT) |
| 前端 | 原生 HTML / CSS / JS |

### 快速开始(Windows + PowerShell)

> 需要 Python、Mosquitto、Git for Windows(自带 `openssl`)。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

.\scripts\init-mqtt-passwd.ps1   # 生成 MQTT 密码文件
.\scripts\gen-certs.ps1          # 生成自签 HTTPS 证书
.\scripts\start-all.ps1          # 启动 Mosquitto + 后端
```

后端监听 `https://0.0.0.0:8443`。同一局域网下,手机打开
`https://<笔记本-IP>:8443`(自签证书首次会弹安全警告,选"继续访问")。

### 烧录固件

1. Arduino IDE 装好 ESP32 板包。
2. 安装库 **PubSubClient** 和 **ArduinoJson**。
3. 打开 `esp32/findit_esp32.ino`,改顶部配置块:`WIFI_SSID` / `WIFI_PASSWORD`、
   `MQTT_HOST`(笔记本局域网 IP)、唯一的 `DEVICE_ID`(与 `config/items.json` 对齐)。
4. 选板子 + 端口,上传。上电后自动连 Wi-Fi → MQTT。

默认接线(改顶部宏即可):

| 元件 | GPIO | 说明 |
|---|---|---|
| LED | 2 | 高电平亮(板载 LED 也行) |
| 蜂鸣器 | 5 | 无源蜂鸣器,`tone()` 输出 2 kHz |
| 按钮 | 4 | 接 GND,`INPUT_PULLUP`,按下 = 低(停止响铃) |

### 安全说明

`mosquitto/passwords` 和 `certs/` 下的密钥都已 gitignore,不会提交。代码里的
`findit123` 只是**占位默认密码** —— 正式部署前请更换,并同步改 ESP32 固件与后端的
`MQTT_PASS`。demo 用自签证书;要上正式域名,用 Let's Encrypt 或 Cloudflare Tunnel
终止 HTTPS。

### 许可

[MIT](./LICENSE) © 2026 Harry Xin([@HarryXin0919](https://github.com/HarryXin0919))
