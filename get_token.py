"""
Find your Mi Smart LED Bulb and extract its connection details.

Tries multiple methods:
  1. Yeelight LAN discovery (no token needed if LAN control is enabled)
  2. miio local network discovery (gets token from the device directly)
  3. Xiaomi Cloud login (handles 2FA via in-session verification)

Usage:
    python get_token.py
"""

import hashlib
import hmac
import base64
import json
import re
import sys
import time
import getpass
import socket
import struct
from urllib.parse import urlencode
import webbrowser

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests


# ── Method 1: Yeelight SSDP Discovery ──────────────────────────────────────

def discover_yeelight(timeout=5) -> list:
    """Discover Yeelight-compatible bulbs on the local network via SSDP."""
    msg = (
        'M-SEARCH * HTTP/1.1\r\n'
        'HOST: 239.255.255.250:1982\r\n'
        'MAN: "ssdp:discover"\r\n'
        'ST: wifi_bulb\r\n'
    ).encode()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    sock.sendto(msg, ("239.255.255.250", 1982))

    devices = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            text = data.decode()
            info = {"ip": addr[0], "port": addr[1]}
            for line in text.split("\r\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    info[key.strip().lower()] = val.strip()
            devices.append(info)
        except socket.timeout:
            break
    sock.close()
    return devices


# ── Method 2: miio Handshake Discovery ──────────────────────────────────────

def discover_miio(ip=None, timeout=5) -> list:
    """Discover miio devices via UDP handshake. If ip is given, probe that IP only."""
    hello = bytes.fromhex(
        "21310020ffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    target = (ip, 54321) if ip else ("255.255.255.255", 54321)
    sock.sendto(hello, target)

    devices = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            if len(data) >= 32:
                token_bytes = data[16:32]
                token_hex = token_bytes.hex()
                # All zeros or all ff means token is hidden
                if token_hex == "0" * 32 or token_hex == "f" * 32:
                    token_hex = None
                devices.append({
                    "ip": addr[0],
                    "token": token_hex,
                    "device_id": str(struct.unpack(">I", data[8:12])[0]),
                })
        except socket.timeout:
            break
    sock.close()
    return devices


# ── Method 3: Xiaomi Cloud ──────────────────────────────────────────────────

XIAOMI_API_URL = "https://{server}.api.io.mi.com/app"
SERVERS = ["de", "us", "ru", "tw", "sg", "cn", "i2"]
USER_AGENT = ("Android-7.1.1-1.0.0-ONEPLUS A3010-136-QNaXkqc5bkMO "
              "APP/xiaomi.smarthome APPV/62830")


def cloud_login(username, password):
    """Login to Xiaomi Cloud. Returns (session, user_id, ssecurity, service_token) or None."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    password_hash = hashlib.md5(password.encode()).hexdigest().upper()

    def _attempt_login():
        resp = session.get(
            "https://account.xiaomi.com/pass/serviceLogin",
            params={"sid": "xiaomiio", "_json": "true"}
        )
        data = json.loads(resp.text.replace("&&&START&&&", ""))
        sign = data.get("_sign", "")

        resp = session.post(
            "https://account.xiaomi.com/pass/serviceLoginAuth2",
            data={
                "hash": password_hash,
                "callback": "https://sts.api.io.mi.com/sts",
                "_json": "true",
                "sid": "xiaomiio",
                "_sign": sign,
                "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
                "user": username,
            }
        )
        return json.loads(resp.text.replace("&&&START&&&", ""))

    auth = _attempt_login()

    if auth.get("code") != 0:
        print(f"  Cloud login failed: {auth.get('desc', 'Unknown error')}")
        return None

    # Handle identity verification
    notification_url = auth.get("notificationUrl", "")
    location = auth.get("location", "")

    if notification_url and not location:
        print("\n  Xiaomi requires identity verification.")

        # Try to complete verification within our session
        try:
            # Load the verification page in our session
            verify_resp = session.get(notification_url, allow_redirects=True)
            verify_html = verify_resp.text

            # Check if there's an SMS/email verification form
            # Try to extract the verification endpoint and handle it
            print("  The verification page has loaded in our session.")
            print("  Opening browser for you to see the verification page...")
            webbrowser.open(notification_url)
            print()
            print("  IMPORTANT: Complete verification, then come back here.")
            print("  If it asks you to verify on your phone (Mi Home app),")
            print("  open Mi Home and approve the notification.\n")
            input("  Press Enter after completing verification... ")

            # After verification, the server marks our account as verified.
            # We need to re-attempt the auth with the SAME session.
            time.sleep(2)

            # Try multiple times - verification may take a moment to propagate
            for attempt in range(3):
                print(f"  Retry attempt {attempt + 1}...")
                auth = _attempt_login()
                location = auth.get("location", "")
                if location:
                    break
                time.sleep(3)

        except Exception as e:
            print(f"  Verification handling error: {e}")
            return None

    location = auth.get("location", "")
    if not location:
        print("  Could not complete login after verification.")
        return None

    user_id = auth.get("userId")
    ssecurity = auth.get("ssecurity")
    if not user_id or not ssecurity:
        print(f"  Missing auth fields. Keys: {list(auth.keys())}")
        return None

    resp = session.get(location)
    service_token = session.cookies.get("serviceToken")
    if not service_token:
        print("  Failed to get service token.")
        return None

    return session, user_id, ssecurity, service_token


def cloud_get_devices(user_id, ssecurity, service_token, server):
    """Fetch device list from a Xiaomi Cloud server region."""
    url = XIAOMI_API_URL.format(server=server) + "/home/device_list"
    nonce = base64.b64encode(
        int(time.time() // 60).to_bytes(8, "big") + b'\x00' * 4
    ).decode()

    signed_nonce = base64.b64encode(
        hmac.new(
            base64.b64decode(ssecurity),
            nonce.encode(),
            hashlib.sha256
        ).digest()
    ).decode()

    params = {"data": json.dumps({"getVirtualModel": False, "getHuamiDevices": 0})}
    params_str = urlencode(params)

    signature = hmac.new(
        base64.b64decode(signed_nonce),
        ("POST\n/home/device_list\n" + params_str + "\n" + nonce).encode(),
        hashlib.sha256
    ).digest()

    headers = {
        "User-Agent": USER_AGENT,
        "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
        "Cookie": f"userId={user_id};serviceToken={service_token}",
        "mishare-sns-token": signed_nonce,
        "signature": base64.b64encode(signature).decode(),
        "nonce": nonce,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        resp = requests.post(url, data=params_str, headers=headers, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            return result.get("result", {}).get("list", [])
    except Exception as e:
        print(f"    Server {server}: {e}")
    return []


# ── Main ────────────────────────────────────────────────────────────────────

def save_config(config):
    with open("bulb_config.json", "w") as f:
        json.dump(config, f, indent=2)
    print(f"\nSaved to bulb_config.json:")
    for k, v in config.items():
        print(f"  {k}: {v}")


def main():
    print("=== Mi Smart LED Bulb Finder ===\n")

    # ── Method 1: Yeelight LAN discovery ──
    print("[1/3] Yeelight LAN discovery (no token needed)...")
    print("      Make sure LAN Control is enabled in Mi Home app:")
    print("      Mi Home > tap bulb > Settings (gear) > LAN Control > ON\n")
    bulbs = discover_yeelight(timeout=5)
    if bulbs:
        print(f"  Found {len(bulbs)} Yeelight device(s):\n")
        for i, b in enumerate(bulbs, 1):
            print(f"  [{i}] {b['ip']}:{b.get('port', 55443)}")
            print(f"      Model: {b.get('model', 'N/A')}")
            print(f"      Name:  {b.get('name', 'N/A')}")
            print(f"      Power: {b.get('power', 'N/A')}")
            print()
        print("  >> Yeelight LAN mode works WITHOUT a token!")
        print("  >> Use bulb.py with --mode yeelight\n")
        if len(bulbs) == 1:
            choice = 0
        else:
            choice = int(input("  Which device? ")) - 1
        b = bulbs[choice]
        save_config({
            "ip": b["ip"],
            "mode": "yeelight",
            "model": b.get("model", "unknown"),
        })
        return

    print("  No Yeelight devices found.")
    print("  (Enable LAN Control in Mi Home, or continue to try other methods)\n")

    # ── Method 2: miio local discovery ──
    print("[2/3] miio local network discovery...")
    miio_devices = discover_miio(timeout=5)
    if miio_devices:
        found_with_token = [d for d in miio_devices if d.get("token")]
        if found_with_token:
            print(f"  Found {len(found_with_token)} device(s) with visible tokens:\n")
            for i, d in enumerate(found_with_token, 1):
                print(f"  [{i}] IP: {d['ip']}")
                print(f"      Token: {d['token']}")
                print(f"      Device ID: {d['device_id']}")
                print()
            if len(found_with_token) == 1:
                choice = 0
            else:
                choice = int(input("  Which device? ")) - 1
            d = found_with_token[choice]
            save_config({
                "ip": d["ip"],
                "token": d["token"],
                "mode": "miio",
            })
            return
        else:
            print(f"  Found {len(miio_devices)} device(s) but tokens are hidden.")
            for d in miio_devices:
                print(f"    IP: {d['ip']} | Device ID: {d['device_id']}")
            print()
    else:
        print("  No miio devices found on local network.\n")

    # ── Method 3: Xiaomi Cloud ──
    print("[3/3] Xiaomi Cloud token extraction...")
    username = input("  Mi Home email or phone: ")
    password = getpass.getpass("  Password: ")

    print("\n  Logging in...")
    result = cloud_login(username, password)
    if not result:
        print("\n  Cloud login failed.")
        print("\n  === Alternative: Manual token extraction ===")
        print("  If none of the above methods worked, you can extract the")
        print("  token from the Mi Home Android app:")
        print("  1. On Android, enable USB debugging")
        print("  2. Run: adb backup -f backup.ab com.xiaomi.smarthome")
        print("  3. Use 'android-backup-extractor' to unpack backup.ab")
        print("  4. Find the token in databases/miio2.db (device_record table)")
        print("  5. Create bulb_config.json manually:")
        print('     {"ip": "BULB_IP", "token": "YOUR_TOKEN", "mode": "miio"}')
        return

    _, user_id, ssecurity, service_token = result
    print("  Login successful!\n")

    print("  Scanning server regions...")
    all_devices = []
    for server in SERVERS:
        devices = cloud_get_devices(user_id, ssecurity, service_token, server)
        if devices:
            print(f"    [{server}] Found {len(devices)} device(s)")
            for d in devices:
                d["_server"] = server
            all_devices.extend(devices)

    if not all_devices:
        print("  No devices found in any region.")
        return

    print(f"\n  Found {len(all_devices)} device(s):\n")
    for i, dev in enumerate(all_devices, 1):
        print(f"  [{i}] {dev.get('name', 'Unknown')}")
        print(f"      Model:  {dev.get('model', 'N/A')}")
        print(f"      IP:     {dev.get('localip', 'N/A')}")
        print(f"      Token:  {dev.get('token', 'N/A')}")
        print(f"      MAC:    {dev.get('mac', 'N/A')}")
        print(f"      Server: {dev.get('_server', 'N/A')}")
        print()

    choice = int(input("  Which device is your bulb? ")) - 1
    dev = all_devices[choice]
    save_config({
        "ip": dev.get("localip"),
        "token": dev.get("token"),
        "model": dev.get("model"),
        "name": dev.get("name"),
        "mode": "miio",
    })


if __name__ == "__main__":
    main()
