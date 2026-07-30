"""
mitmproxy addon that captures Mi Home device tokens from cloud API responses.

This script runs inside mitmproxy. Do NOT run it directly with python.

Setup:
  1. Find your PC's local IP (ipconfig -> look for your ethernet/wifi IP)
  2. Start mitmproxy:
       mitmdump -s intercept_token.py -p 8888
  3. On your Android phone:
       - Connect to the same WiFi as your PC
       - Go to WiFi settings -> tap your network -> Advanced -> Proxy -> Manual
       - Set proxy host to your PC's IP, port to 8888
       - Open browser, go to http://mitm.it
       - Download the Android CA certificate
       - Install it: Settings > Security > Install from storage (or Encryption & Credentials > Install a certificate > CA certificate)
  4. Open Mi Home app — pull down to refresh device list
  5. Watch this terminal for captured tokens

After getting the token:
  - Remove the proxy from your phone's WiFi settings
  - Delete the mitmproxy CA certificate from your phone
"""

import json
import re


def response(flow):
    """Inspect HTTP responses for Xiaomi device tokens."""
    url = flow.request.pretty_url

    # Look for Xiaomi cloud API device list responses
    if "api.io.mi.com" not in url and "mi.com" not in url:
        return

    content_type = flow.response.headers.get("content-type", "")

    try:
        if "json" in content_type or "text" in content_type:
            body = flow.response.get_text()
        else:
            body = flow.response.get_text(errors="replace")
    except Exception:
        body = flow.response.content.decode("latin-1", errors="replace")

    if not body:
        return

    # Check if this response contains device info
    has_device_data = any(kw in body for kw in ["token", "localip", "device_list", "devlist"])

    if has_device_data:
        print(f"\n{'='*60}")
        print(f"FOUND DEVICE DATA in: {url}")
        print(f"{'='*60}")

        # Try to parse as JSON
        try:
            data = json.loads(body)
            devices = []

            # Navigate common response structures
            if "result" in data:
                result = data["result"]
                if isinstance(result, dict) and "list" in result:
                    devices = result["list"]
                elif isinstance(result, list):
                    devices = result

            if devices:
                for dev in devices:
                    if isinstance(dev, dict):
                        name = dev.get("name", "Unknown")
                        model = dev.get("model", "N/A")
                        ip = dev.get("localip", "N/A")
                        token = dev.get("token", "N/A")
                        mac = dev.get("mac", "N/A")
                        did = dev.get("did", "N/A")

                        print(f"\n  Device: {name}")
                        print(f"  Model:  {model}")
                        print(f"  IP:     {ip}")
                        print(f"  Token:  {token}")
                        print(f"  MAC:    {mac}")
                        print(f"  DID:    {did}")

                        # Save to file
                        save_token(dev)
            else:
                # Couldn't parse structure, dump raw
                print(f"\nRaw response (first 2000 chars):\n{body[:2000]}")
        except json.JSONDecodeError:
            # Not JSON, search for token patterns
            tokens = re.findall(r'"token"\s*:\s*"([0-9a-fA-F]{32,})"', body)
            if tokens:
                print(f"\nFound token(s) in non-JSON response: {tokens}")
            else:
                print(f"\nResponse mentions device keywords but no tokens found")
                print(f"First 500 chars: {body[:500]}")

    # Also log any URL that hits the mi.com API for debugging
    elif "device" in url.lower() or "home" in url.lower():
        print(f"[API] {flow.request.method} {url} -> {flow.response.status_code}")


def save_token(device: dict):
    """Save discovered device to bulb_config.json."""
    token = device.get("token", "")
    ip = device.get("localip", "")
    model = device.get("model", "")

    if not token or len(token) < 32:
        return

    # Check if this looks like a yeelight/bulb
    is_bulb = any(kw in model.lower() for kw in ["yeelink", "light", "bulb", "color"])

    if is_bulb:
        config = {
            "ip": ip,
            "token": token,
            "model": model,
            "name": device.get("name", ""),
            "mode": "miio",
        }
        with open("bulb_config.json", "w") as f:
            json.dump(config, f, indent=2)
        print(f"\n  >> BULB TOKEN SAVED to bulb_config.json!")
        print(f"  >> Token: {token}")
