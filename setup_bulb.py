"""
Setup a factory-reset Mi Smart LED Bulb Essential.

Steps:
  1. Factory reset the bulb (toggle power 5 times quickly)
  2. Connect your PC to the bulb's WiFi AP (e.g. "yeelink-light-xxxxx")
  3. Run this script — it discovers the bulb, grabs the token,
     and sends your home WiFi credentials to the bulb
  4. The bulb joins your home WiFi with the same token

Usage:
    python setup_bulb.py
"""

import json
import socket
import struct
import sys
import time
from pathlib import Path

try:
    from miio import Device
    from miio.exceptions import DeviceException
except ImportError:
    print("python-miio not installed. Run: pip install python-miio")
    sys.exit(1)


def discover(timeout=5):
    """Send miio handshake to known AP-mode IPs and broadcast."""
    hello = bytes.fromhex(
        "21310020ffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )

    # In AP mode the bulb is usually the gateway. Common addresses:
    AP_MODE_IPS = [
        "192.168.4.1",
        "192.168.13.1",   # most Xiaomi/Yeelight devices
        "192.168.8.1",
        "192.168.1.1",
        "10.0.0.1",
        "172.16.0.1",
    ]

    # Also detect the gateway from our own IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.13.1", 80))
        my_ip = s.getsockname()[0]
        s.close()
        # Guess gateway as x.x.x.1
        parts = my_ip.rsplit(".", 1)
        gateway_guess = parts[0] + ".1"
        if gateway_guess not in AP_MODE_IPS:
            AP_MODE_IPS.insert(0, gateway_guess)
    except Exception:
        pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    # Send to all known IPs and broadcast
    for ip in AP_MODE_IPS:
        try:
            sock.sendto(hello, (ip, 54321))
        except Exception:
            pass
    sock.sendto(hello, ("255.255.255.255", 54321))

    devices = []
    seen_ips = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            if len(data) >= 32 and addr[0] not in seen_ips:
                seen_ips.add(addr[0])
                token_hex = data[16:32].hex()
                is_hidden = token_hex in ("0" * 32, "f" * 32)
                devices.append({
                    "ip": addr[0],
                    "token": None if is_hidden else token_hex,
                    "device_id": struct.unpack(">I", data[8:12])[0],
                })
        except socket.timeout:
            break
    sock.close()
    return devices


def configure_wifi(ip: str, token: str, ssid: str, password: str):
    """Send WiFi credentials to the bulb so it joins your home network."""
    device = Device(ip, token)
    try:
        # miio configure_wifi command
        result = device.send("miIO.config_router", {
            "ssid": ssid,
            "passwd": password,
            "uid": 0,
        })
        return result
    except DeviceException as e:
        print(f"Error configuring WiFi: {e}")
        return None


def main():
    print("=== Mi Smart LED Bulb Setup ===\n")
    print("Prerequisites:")
    print("  1. Factory reset the bulb: toggle power ON/OFF 5 times quickly")
    print("     The bulb should start blinking in color cycle mode")
    print("  2. Connect this PC to the bulb's WiFi hotspot")
    print('     Look for a network like "yeelink-light-xxxx"\n')

    input("Press Enter when connected to the bulb's WiFi... ")

    print("\nDiscovering bulb...")
    devices = discover(timeout=8)

    if not devices:
        print("No devices found. Make sure you're connected to the bulb's WiFi.")
        print("The bulb's WiFi usually appears as 'yeelink-light-xxxx'.")
        sys.exit(1)

    # Usually the bulb is at 192.168.13.1 when in AP mode
    print(f"\nFound {len(devices)} device(s):\n")
    for i, d in enumerate(devices, 1):
        token_display = d['token'] if d['token'] else "(hidden)"
        print(f"  [{i}] IP: {d['ip']}")
        print(f"      Token: {token_display}")
        print(f"      Device ID: {d['device_id']}")
        print()

    if len(devices) == 1:
        dev = devices[0]
    else:
        choice = int(input("Which device? ")) - 1
        dev = devices[choice]

    if not dev["token"]:
        print("Token is hidden. The bulb may not be in setup mode.")
        print("Try factory resetting again (toggle power 5 times).")
        sys.exit(1)

    token = dev["token"]
    ip = dev["ip"]
    print(f"Got token: {token}\n")

    # Ask for home WiFi credentials
    print("Now enter your home WiFi details so the bulb can join your network.\n")
    ssid = input("Home WiFi name (SSID): ")
    wifi_pass = input("Home WiFi password: ")

    print(f"\nConfiguring bulb to join '{ssid}'...")
    result = configure_wifi(ip, token, ssid, wifi_pass)

    if result is not None:
        print(f"Result: {result}")
        print("\nThe bulb should now connect to your home WiFi.")
        print("It may take 10-15 seconds. The bulb will stop blinking once connected.\n")
        print("IMPORTANT: Reconnect this PC to your home WiFi now!\n")

        # Save config
        config = {
            "ip": "(will change — check your router for the bulb's new IP)",
            "token": token,
            "mode": "miio",
        }
        config_path = Path(__file__).parent / "bulb_config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"Token saved to bulb_config.json")
        print(f"Token: {token}")
        print()
        print("Next steps:")
        print("  1. Reconnect your PC to your home WiFi")
        print("  2. Find the bulb's new IP from your router's admin page")
        print("     or run: python -c \"from get_token import discover_miio; print(discover_miio())\"")
        print("  3. Update the 'ip' in bulb_config.json")
        print("  4. Run: python bulb.py")
    else:
        # Still save the token even if wifi config failed
        config = {
            "token": token,
            "mode": "miio",
            "ip": ip,
        }
        config_path = Path(__file__).parent / "bulb_config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\nWiFi config failed but token is saved: {token}")
        print("You can re-add the bulb to Mi Home — the token may change though.")
        print("Or retry this script.")


if __name__ == "__main__":
    main()
