"""
Mi Smart LED Bulb Essential (White and Color) controller.

Supports two modes:
  - yeelight: LAN control via Yeelight protocol (no token needed)
  - miio: Control via miio protocol (requires token)

Usage:
    python bulb.py                          # uses bulb_config.json
    python bulb.py --ip IP --token TOKEN    # miio mode
    python bulb.py --ip IP --mode yeelight  # yeelight LAN mode
"""

import argparse
import json
import sys
from pathlib import Path


def load_config() -> dict:
    config_path = Path(__file__).parent / "bulb_config.json"
    if not config_path.exists():
        print("bulb_config.json not found. Run get_token.py first, or pass --ip and --token.")
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


class MiioBulb:
    """Control bulb via miio protocol (needs token)."""

    def __init__(self, ip: str, token: str):
        from miio import Yeelight
        self.device = Yeelight(ip, token)

    def info(self):
        from miio.exceptions import DeviceException
        try:
            info = self.device.info()
            print(f"Connected (miio): {info}")
        except DeviceException as e:
            print(f"Connection failed: {e}")
            sys.exit(1)

    def on(self):
        self.device.on()

    def off(self):
        self.device.off()

    def set_brightness(self, val: int):
        self.device.set_brightness(max(1, min(100, val)))

    def set_color_temp(self, kelvin: int):
        self.device.set_color_temp(max(1700, min(6500, kelvin)))

    def set_rgb(self, r: int, g: int, b: int):
        r, g, b = [max(0, min(255, c)) for c in (r, g, b)]
        # Derive brightness from the max channel value
        brightness = max(r, g, b)
        if brightness == 0:
            self.device.set_brightness(1)
            return
        # Normalize RGB to full intensity for color, use max channel as brightness
        scale = 255 / brightness
        nr, ng, nb = int(r * scale), int(g * scale), int(b * scale)
        self.device.set_rgb((nr, ng, nb))
        self.device.set_brightness(max(1, round(brightness / 255 * 100)))

    def set_hsv(self, hue: int, sat: int):
        self.device.set_hsv([hue, sat])

    def status(self):
        s = self.device.status()
        print(f"  Power:      {s.is_on}")
        print(f"  Brightness: {s.brightness}%")
        print(f"  Color temp: {s.color_temp}K")
        print(f"  RGB:        {s.rgb}")


class YeelightBulb:
    """Control bulb via Yeelight LAN protocol (no token needed)."""

    def __init__(self, ip: str):
        from yeelight import Bulb
        self.device = Bulb(ip)

    def info(self):
        try:
            props = self.device.get_properties()
            print(f"Connected (yeelight LAN): {props}")
        except Exception as e:
            print(f"Connection failed: {e}")
            print("Make sure LAN Control is enabled in Mi Home app.")
            sys.exit(1)

    def on(self):
        self.device.turn_on()

    def off(self):
        self.device.turn_off()

    def set_brightness(self, val: int):
        self.device.set_brightness(max(1, min(100, val)))

    def set_color_temp(self, kelvin: int):
        self.device.set_color_temp(max(1700, min(6500, kelvin)))

    def set_rgb(self, r: int, g: int, b: int):
        r, g, b = [max(0, min(255, c)) for c in (r, g, b)]
        self.device.set_rgb(r, g, b)

    def set_hsv(self, hue: int, sat: int):
        self.device.set_hsv(hue, sat)

    def status(self):
        props = self.device.get_properties()
        for k, v in zip(["power", "bright", "ct", "rgb", "hue", "sat",
                          "color_mode", "flowing", "delayoff", "flow_params",
                          "music_on", "name"], props):
            print(f"  {k}: {v}")


def interactive(bulb):
    """Interactive control loop."""
    print("\nCommands:")
    print("  on / off")
    print("  brightness <1-100>")
    print("  temp <1700-6500>")
    print("  rgb <r> <g> <b>")
    print("  hsv <hue> <sat>")
    print("  status")
    print("  quit\n")

    while True:
        try:
            cmd = input("> ").strip().lower().split()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cmd:
            continue

        try:
            if cmd[0] == "on":
                bulb.on()
                print("ON")
            elif cmd[0] == "off":
                bulb.off()
                print("OFF")
            elif cmd[0] == "brightness" and len(cmd) == 2:
                bulb.set_brightness(int(cmd[1]))
                print(f"Brightness: {cmd[1]}%")
            elif cmd[0] == "temp" and len(cmd) == 2:
                bulb.set_color_temp(int(cmd[1]))
                print(f"Color temp: {cmd[1]}K")
            elif cmd[0] == "rgb" and len(cmd) == 4:
                r, g, b = int(cmd[1]), int(cmd[2]), int(cmd[3])
                bulb.set_rgb(r, g, b)
                print(f"Color: ({r}, {g}, {b})")
            elif cmd[0] == "hsv" and len(cmd) == 3:
                bulb.set_hsv(int(cmd[1]), int(cmd[2]))
                print(f"HSV: hue={cmd[1]}, sat={cmd[2]}")
            elif cmd[0] == "status":
                bulb.status()
            elif cmd[0] in ("quit", "exit", "q"):
                break
            else:
                print("Unknown command.")
        except Exception as e:
            print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Control Mi Smart LED Bulb")
    parser.add_argument("--ip", help="Bulb IP address")
    parser.add_argument("--token", help="Device token (32 hex chars)")
    parser.add_argument("--mode", choices=["miio", "yeelight"],
                        help="Control mode (default: from config)")
    args = parser.parse_args()

    if args.ip and (args.token or args.mode == "yeelight"):
        ip = args.ip
        token = args.token
        mode = args.mode or ("yeelight" if not token else "miio")
    else:
        config = load_config()
        ip = args.ip or config["ip"]
        token = args.token or config.get("token")
        mode = args.mode or config.get("mode", "miio")

    print(f"Connecting to {ip} ({mode} mode)...")

    if mode == "yeelight":
        bulb = YeelightBulb(ip)
    else:
        if not token:
            print("Token required for miio mode. Run get_token.py or use --mode yeelight.")
            sys.exit(1)
        bulb = MiioBulb(ip, token)

    bulb.info()
    interactive(bulb)


if __name__ == "__main__":
    main()
