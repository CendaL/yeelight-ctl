# yeelight-ctl

Control **Xiaomi / Yeelight WiFi smart bulbs** from your
computer using [`python-miio`](https://github.com/rytilahti/python-miio) and the
Yeelight LAN protocol.

Includes tools for the whole lifecycle: onboarding a factory-reset bulb onto your
WiFi, discovering its token, an interactive control CLI, and a screen-color sync
("ambilight") mode.

## Features

- **Interactive CLI** — on/off, brightness, color temperature, RGB, and HSV control.
- **Two control modes** — `miio` (needs the device token) and `yeelight` (LAN control, no token).
- **Token discovery** — via Yeelight LAN discovery, miio handshake, or Xiaomi Cloud login.
- **Screen sync** — real-time ambient lighting that matches your screen's dominant color.
- **First-time setup** — provision a factory-reset bulb onto your home WiFi.

## Requirements

- Python 3.8+
- A Xiaomi/Yeelight color bulb on the same network
- Dependencies from `requirements.txt`

## Install

```bash
pip install -r requirements.txt
```

## Setup

Copy the environment template and fill in your details:

```bash
cp .env.example .env
```

### 1. Onboard a new / factory-reset bulb (optional)

If the bulb isn't on your WiFi yet:

```bash
python setup_bulb.py
```

Factory-reset the bulb (toggle power on/off ~5 times until it pulses), connect your
PC to the bulb's temporary `yeelink-light-xxxx` WiFi hotspot, then run the script.
It discovers the bulb, reads its token, and sends your home WiFi credentials so the
bulb joins your network.

`provision_wifi.py` is a lower-level alternative that reads `WIFI_SSID`,
`WIFI_PASSWORD`, `BULB_TOKEN`, and `BULB_AP_IP` from `.env`.

### 2. Get the bulb's token and IP

```bash
python get_token.py
```

Tries, in order: Yeelight LAN discovery → miio local handshake → Xiaomi Cloud login
(handles 2FA). On success it writes `bulb_config.json` with the IP, token, and model.

> **Tip:** If you enable *LAN Control* in the Mi Home app (tap bulb → Settings gear →
> LAN Control → ON), you can skip tokens entirely and use `--mode yeelight`.

## Usage

### Control the bulb

```bash
python bulb.py                          # uses bulb_config.json
python bulb.py --ip 192.168.1.12 --token YOUR_TOKEN
python bulb.py --ip 192.168.1.12 --mode yeelight   # LAN mode, no token
```

Interactive commands:

```
on / off
brightness <1-100>
temp <1700-6500>
rgb <r> <g> <b>
hsv <hue> <sat>
status
quit
```

### Screen color sync (ambilight)

```bash
python screen_sync.py              # match screen's dominant color
python screen_sync.py --fps 8      # faster updates
python screen_sync.py --edges      # sample screen edges only (ambilight style)
python screen_sync.py --sat 1.5    # boost saturation
```

Press `Ctrl+C` to stop.

## Project layout

| File | Purpose |
|------|---------|
| `bulb.py` | Interactive control CLI (miio + yeelight modes) |
| `get_token.py` | Discover the bulb and extract its token/IP |
| `setup_bulb.py` | Onboard a factory-reset bulb onto your WiFi |
| `provision_wifi.py` | Send WiFi credentials to a bulb in setup mode (reads `.env`) |
| `screen_sync.py` | Real-time screen-to-bulb color sync |
| `extract_token_adb.py` | Alternative: pull tokens from Mi Home via ADB |
| `intercept_token.py` | Alternative: capture tokens via mitmproxy addon |
| `.env.example` | Template for local secrets (copy to `.env`) |

## License

[MIT](LICENSE)
