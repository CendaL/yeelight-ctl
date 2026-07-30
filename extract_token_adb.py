"""
Extract Mi Home device tokens from Android phone via ADB.

Prerequisites:
  - Bulb added to Mi Home app on your phone
  - USB Debugging enabled
  - Phone connected via USB
  - ADB installed and phone authorized

Usage:
    python extract_token_adb.py
"""

import subprocess
import sys
import os
import json
import re
import tempfile


ADB = None

def find_adb() -> str:
    """Find the ADB executable."""
    global ADB
    if ADB:
        return ADB

    # Try PATH first
    try:
        subprocess.run(["adb", "version"], capture_output=True, timeout=5)
        ADB = "adb"
        return ADB
    except FileNotFoundError:
        pass

    # Common install locations on Windows
    import glob
    candidates = glob.glob(
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*platform*\platform-tools\adb.exe"
        )
    ) + glob.glob(
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")
    ) + glob.glob(
        r"C:\Program Files\Google\platform-tools\adb.exe"
    )

    for path in candidates:
        if os.path.isfile(path):
            ADB = path
            print(f"  Found ADB at: {path}")
            return ADB

    print("ADB not found. Install it with: winget install Google.PlatformTools")
    sys.exit(1)


def run_adb(args: list[str]) -> str:
    """Run an ADB command and return stdout."""
    adb = find_adb()
    result = subprocess.run(
        [adb] + args,
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0 and result.stderr:
        print(f"  adb error: {result.stderr.strip()}")
    return result.stdout.strip()


def check_adb():
    """Verify ADB connection."""
    output = run_adb(["devices"])
    lines = [l for l in output.split("\n") if "\tdevice" in l]
    if not lines:
        print("No device found. Make sure:")
        print("  - Phone is connected via USB")
        print("  - USB Debugging is enabled")
        print("  - You authorized this PC on the phone")
        sys.exit(1)
    device_id = lines[0].split("\t")[0]
    print(f"Connected to: {device_id}")
    return device_id


def method_shared_prefs():
    """Try to read device info from Mi Home shared preferences / sdcard logs."""
    print("\n[Method 1] Checking Mi Home logs on sdcard...")

    # Mi Home sometimes writes device list to sdcard
    paths_to_try = [
        "/sdcard/SmartHome/logs/",
        "/sdcard/Android/data/com.xiaomi.smarthome/files/",
        "/sdcard/Android/data/com.xiaomi.smarthome/cache/",
    ]

    for path in paths_to_try:
        output = run_adb(["shell", "ls", path])
        if "No such file" not in output and output:
            print(f"  Found: {path}")
            print(f"  Contents: {output[:200]}")

    # Try to grep for token patterns in accessible storage
    print("\n  Searching for device tokens in accessible storage...")
    output = run_adb([
        "shell",
        "grep -r 'token' /sdcard/SmartHome/ 2>/dev/null || echo 'not found'"
    ])
    if output and output != "not found":
        print(f"  Found references:\n{output[:500]}")
        return output
    return None


def method_miio_db():
    """Try to pull the miio database directly (needs root or permissive SELinux)."""
    print("\n[Method 2] Trying to read Mi Home database...")

    db_paths = [
        "/data/data/com.xiaomi.smarthome/databases/miio2.db",
        "/data/data/com.xiaomi.smarthome/databases/device_record.db",
    ]

    tmpdir = tempfile.mkdtemp()
    for db_path in db_paths:
        local_path = os.path.join(tmpdir, os.path.basename(db_path))
        output = run_adb(["pull", db_path, local_path])
        if "error" not in output.lower() and os.path.exists(local_path):
            print(f"  Pulled: {db_path}")
            return local_path

    # Try with run-as (works if app is debuggable)
    for db_path in db_paths:
        db_name = os.path.basename(db_path)
        output = run_adb([
            "shell",
            f"run-as com.xiaomi.smarthome cat databases/{db_name} 2>/dev/null | base64"
        ])
        if output and "error" not in output.lower() and len(output) > 100:
            import base64
            local_path = os.path.join(tmpdir, db_name)
            with open(local_path, "wb") as f:
                f.write(base64.b64decode(output))
            print(f"  Got {db_name} via run-as")
            return local_path

    print("  Cannot access database directly (phone not rooted)")
    return None


def method_backup():
    """Extract token via ADB backup of Mi Home app."""
    print("\n[Method 3] ADB backup extraction...")
    print("  This may show a 'Back up my data' prompt on your phone.")
    print("  If prompted, do NOT set a password — just tap 'Back up'.\n")

    tmpdir = tempfile.mkdtemp()
    backup_path = os.path.join(tmpdir, "mihome.ab")

    adb = find_adb()
    result = subprocess.run(
        [adb, "backup", "-f", backup_path, "-noapk", "com.xiaomi.smarthome"],
        capture_output=True, text=True, timeout=120
    )

    if not os.path.exists(backup_path):
        print("  Backup failed or was denied.")
        return None

    size = os.path.getsize(backup_path)
    if size < 1000:
        print(f"  Backup file too small ({size} bytes) — app likely blocked backup.")
        return None

    print(f"  Backup saved: {backup_path} ({size} bytes)")

    # Try to decompress the backup
    # Android backup format: 4-line header, then deflate-compressed tar
    try:
        import zlib
        with open(backup_path, "rb") as f:
            header = b""
            for _ in range(4):
                header += f.readline()
            compressed = f.read()

        try:
            decompressed = zlib.decompress(compressed)
        except zlib.error:
            # Try raw deflate (no header)
            decompressed = zlib.decompress(compressed, -zlib.MAX_WBITS)

        # Search for token pattern in the decompressed data
        text = decompressed.decode("latin-1")
        # Look for 32-char hex tokens near "token" keyword
        token_matches = re.findall(r'"token"\s*[":,]\s*"([0-9a-fA-F]{32})"', text)
        if token_matches:
            print(f"\n  Found token(s): {token_matches}")
            return token_matches

        # Also try to find JSON device records
        device_matches = re.findall(r'\{[^}]*"localip"[^}]*"token"[^}]*\}', text)
        if device_matches:
            for m in device_matches[:5]:
                print(f"\n  Device record: {m[:200]}")
            return device_matches

        # Save decompressed for manual inspection
        dump_path = os.path.join(tmpdir, "backup_dump.txt")
        with open(dump_path, "w", encoding="latin-1") as f:
            f.write(text)
        print(f"  No tokens found automatically. Raw dump saved to: {dump_path}")
        print("  You can search it manually for 'token'.")

    except Exception as e:
        print(f"  Could not decompress backup: {e}")

    return None


def method_network_dump():
    """Guide user to use Mi Home's built-in network traffic logging."""
    print("\n[Method 4] Mi Home network log...")
    print("  Some Mi Home versions log device tokens to a file.")
    print("  Checking for log files...\n")

    # Check common Mi Home log locations
    output = run_adb([
        "shell",
        "find /sdcard/ -name '*.log' -path '*SmartHome*' -o "
        "-name '*.log' -path '*xiaomi*' -o "
        "-name 'device_list*' -path '*xiaomi*' "
        "2>/dev/null"
    ])

    if output:
        print(f"  Found log files:\n{output}\n")
        for logfile in output.split("\n"):
            logfile = logfile.strip()
            if logfile:
                content = run_adb(["shell", f"cat '{logfile}' 2>/dev/null"])
                tokens = re.findall(r'[0-9a-fA-F]{32}', content)
                if tokens:
                    print(f"  Potential tokens in {logfile}: {set(tokens)}")
    else:
        print("  No log files found.")
    return None


def main():
    print("=== Mi Home Token Extractor (ADB) ===\n")

    check_adb()

    # Try each method
    result = method_shared_prefs()
    if not result:
        result = method_miio_db()
    if not result:
        result = method_backup()
    if not result:
        method_network_dump()

    if not result:
        print("\n" + "=" * 50)
        print("Automatic extraction didn't find tokens.")
        print("\nManual alternative:")
        print("  1. Open Mi Home app")
        print("  2. Tap the bulb > tap '...' menu > General Settings")
        print("  3. Look for 'Network Info' — note the IP and MAC")
        print("  4. Install 'Mi Home Token Viewer' from GitHub/XDA")
        print("     or use an older Mi Home APK (v5.4.54) that shows tokens")


if __name__ == "__main__":
    main()
