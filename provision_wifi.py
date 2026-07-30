"""Send home WiFi credentials to the bulb so it joins your network.

Fill in .env with your WIFI_SSID and WIFI_PASSWORD, then run:
    python provision_wifi.py
"""

import os
import sys

# Load .env manually (no extra dependency)
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

ssid = os.environ.get("WIFI_SSID")
password = os.environ.get("WIFI_PASSWORD")

if not ssid or not password or "your_wifi" in ssid:
    print("Edit .env and set WIFI_SSID and WIFI_PASSWORD first.")
    sys.exit(1)

from miio import Device

# The bulb acts as an access point at this IP while in setup mode.
BULB_IP = os.environ.get("BULB_AP_IP", "192.168.4.1")
# Token read during setup-mode discovery (see setup_bulb.py). Never hardcode
# a real token here — keep it in .env so it does not end up in version control.
TOKEN = os.environ.get("BULB_TOKEN")

if not TOKEN:
    print("Set BULB_TOKEN in .env (the setup-mode token from setup_bulb.py).")
    sys.exit(1)

print(f"Sending WiFi credentials to bulb at {BULB_IP}...")
print(f"SSID: {ssid}")

device = Device(BULB_IP, TOKEN)
result = device.send("miIO.config_router", {
    "ssid": ssid,
    "passwd": password,
    "uid": 0,
})
print(f"Result: {result}")
print()
print("The bulb should now join your home WiFi.")
print("Next steps:")
print("  1. Reconnect your PC to your home WiFi")
print("  2. Find the bulb's new IP from your router admin page")
print("  3. Update 'ip' in bulb_config.json")
print("  4. Run: python bulb.py")
