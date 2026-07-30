"""
Sync Mi Smart LED Bulb color to screen content in real time.

Captures the screen, extracts the dominant color, and sends it
to the bulb. This should have minimal performance impact.

Usage:
    python screen_sync.py              # uses bulb_config.json
    python screen_sync.py --fps 4      # update rate (default: 4)
    python screen_sync.py --sat 1.5    # saturation boost (default: 1.3)
    python screen_sync.py --edges      # sample screen edges only (ambilight style)
"""

import argparse
import colorsys
import json
import sys
import time
import threading
from pathlib import Path

import numpy as np
import mss

from miio import Device


class ScreenCapture:
    """Fast screen capture with downscaling."""

    def __init__(self, edges_only=False):
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1]  # primary monitor
        self.edges_only = edges_only

    def grab(self) -> np.ndarray:
        """Capture screen and return as small numpy RGB array."""
        img = self.sct.grab(self.monitor)
        # Convert to numpy, drop alpha channel
        frame = np.frombuffer(img.raw, dtype=np.uint8).reshape(
            img.height, img.width, 4
        )[:, :, :3]  # BGR from mss

        # Downscale to ~40x30 using strided slicing (faster than resize)
        step_y = max(1, img.height // 30)
        step_x = max(1, img.width // 40)
        small = frame[::step_y, ::step_x]

        if self.edges_only:
            small = self._extract_edges(small)

        # BGR -> RGB
        return small[:, :, ::-1]

    def _extract_edges(self, frame: np.ndarray) -> np.ndarray:
        """Extract border pixels (ambilight style)."""
        h, w = frame.shape[:2]
        border = max(2, min(h, w) // 5)
        top = frame[:border, :]
        bottom = frame[-border:, :]
        left = frame[border:-border, :border]
        right = frame[border:-border, -border:]
        # Stack all border pixels into a single array
        pixels = np.vstack([
            top.reshape(-1, 3),
            bottom.reshape(-1, 3),
            left.reshape(-1, 3),
            right.reshape(-1, 3),
        ])
        return pixels.reshape(-1, 1, 3)


def dominant_color(pixels: np.ndarray, sat_boost: float = 1.3) -> tuple:
    """Extract the dominant vibrant color from pixel array.

    Uses a simplified approach: filters out dark/grey pixels,
    then takes the weighted average biased toward saturated colors.
    Returns (r, g, b) with values 0-255.
    """
    # Flatten to (N, 3)
    flat = pixels.reshape(-1, 3).astype(np.float32)

    # Convert to HSV-like representation for filtering
    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta = max_c - min_c

    # Saturation (0-1 range, where max_c > 0)
    saturation = np.where(max_c > 0, delta / max_c, 0)
    brightness = max_c / 255.0

    # Weight: prefer saturated + bright pixels, ignore dark/grey
    weight = saturation * brightness
    weight = np.power(weight, 2)  # amplify differences

    total_weight = weight.sum()
    if total_weight < 1e-6:
        # Scene is very dark or grey — use simple average
        avg = flat.mean(axis=0)
        return int(avg[0]), int(avg[1]), int(avg[2])

    # Weighted average
    wr = (flat[:, 0] * weight).sum() / total_weight
    wg = (flat[:, 1] * weight).sum() / total_weight
    wb = (flat[:, 2] * weight).sum() / total_weight

    # Boost saturation in the result
    h, s, v = colorsys.rgb_to_hsv(wr / 255, wg / 255, wb / 255)
    s = min(1.0, s * sat_boost)
    r_out, g_out, b_out = colorsys.hsv_to_rgb(h, s, v)

    return int(r_out * 255), int(g_out * 255), int(b_out * 255)


def color_distance(c1: tuple, c2: tuple) -> float:
    """Simple RGB distance between two colors."""
    return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5


class BulbSender:
    """Sends color commands to the bulb in a background thread.

    Deduplicates and rate-limits to avoid flooding the bulb.
    """

    def __init__(self, ip: str, token: str, transition: int = 0):
        self.device = Device(ip, token)
        self._lock = threading.Lock()
        self._pending_color = None
        self._last_sent = (0, 0, 0)
        self._running = False
        self._thread = None
        # Minimum color change to trigger an update (avoids flicker)
        self.threshold = 25.0
        # Transition duration in ms (0 = instant)
        self.transition = transition

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def update(self, r: int, g: int, b: int):
        """Queue a color update (non-blocking)."""
        with self._lock:
            self._pending_color = (r, g, b)

    def _send_loop(self):
        """Background loop that sends queued colors to the bulb."""
        while self._running:
            color = None
            with self._lock:
                if self._pending_color is not None:
                    color = self._pending_color
                    self._pending_color = None

            if color and color_distance(color, self._last_sent) > self.threshold:
                try:
                    r, g, b = color
                    brightness = max(r, g, b)
                    if brightness == 0:
                        self.device.send("set_bright", [1])
                    else:
                        scale = 255 / brightness
                        nr = int(r * scale)
                        ng = int(g * scale)
                        nb = int(b * scale)
                        rgb_int = (nr << 16) | (ng << 8) | nb
                        bright_pct = max(1, round(brightness / 255 * 100))
                        # Send both in quick succession
                        mode = "sudden" if self.transition == 0 else "smooth"
                        ms = max(30, self.transition) if mode == "smooth" else 0
                        self.device.send("set_rgb", [rgb_int, mode, ms])
                        self.device.send("set_bright", [bright_pct, mode, ms])
                    self._last_sent = color
                except Exception as e:
                    print(f"\rBulb error: {e}", end="", flush=True)

            time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description="Sync bulb to screen color")
    parser.add_argument("--fps", type=float, default=4,
                        help="Updates per second (default: 4)")
    parser.add_argument("--sat", type=float, default=1.3,
                        help="Saturation boost multiplier (default: 1.3)")
    parser.add_argument("--edges", action="store_true",
                        help="Sample screen edges only (ambilight style)")
    parser.add_argument("--threshold", type=float, default=25.0,
                        help="Min color change to trigger update (default: 25)")
    parser.add_argument("--transition", type=int, default=100,
                        help="Transition duration in ms (0=instant, default: 0)")
    parser.add_argument("--ip", help="Bulb IP override")
    parser.add_argument("--token", help="Bulb token override")
    args = parser.parse_args()

    # Load config
    config_path = Path(__file__).parent / "bulb_config.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

    ip = args.ip or config.get("ip")
    token = args.token or config.get("token")
    if not ip or not token:
        print("Missing IP or token. Run get_token.py or pass --ip and --token.")
        sys.exit(1)

    interval = 1.0 / args.fps

    print(f"Screen Sync")
    print(f"  Bulb:       {ip}")
    print(f"  Update rate: {args.fps}/sec")
    print(f"  Sat boost:  {args.sat}x")
    print(f"  Mode:       {'edges only' if args.edges else 'full screen'}")
    print(f"  Threshold:  {args.threshold}")
    print(f"\nPress Ctrl+C to stop.\n")

    capture = ScreenCapture(edges_only=args.edges)
    sender = BulbSender(ip, token, transition=args.transition)
    sender.threshold = args.threshold
    sender.start()

    try:
        while True:
            t0 = time.perf_counter()

            pixels = capture.grab()
            r, g, b = dominant_color(pixels, sat_boost=args.sat)
            sender.update(r, g, b)

            print(f"\r  Color: ({r:3d}, {g:3d}, {b:3d})  #{r:02x}{g:02x}{b:02x}", end="", flush=True)

            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n\nStopping...")
        sender.stop()
        print("Done.")


if __name__ == "__main__":
    main()
