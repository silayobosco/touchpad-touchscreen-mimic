"""CLI entrypoint and DBus client for TouchMimic."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Optional

from .linux_input import (
    check_permissions,
    discover_input_devices,
    grant_permissions_via_pkexec,
)
from .window_tracker import DBUS_PATH, DBUS_SERVICE


def call_daemon_dbus(method: str) -> Optional[dict]:
    """Call a method on the running TouchMimic daemon over session DBus."""
    try:
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv[:1])
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        iface = QDBusInterface(DBUS_SERVICE, DBUS_PATH, "local.touchmimic.DBusBridge", bus)
        if not iface.isValid():
            return None
        reply = iface.call(method)
        if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
            raw = reply.arguments()[0]
            if isinstance(raw, str):
                return json.loads(raw)
    except Exception:
        pass
    return None


def print_status(data: dict) -> None:
    state = "🟢 ACTIVE (Touchscreen Mode)" if data.get("active") else "⚪ STANDBY (Normal Touchpad)"
    tw = data.get("target_window", {})
    print(f"Status         : {state}")
    print(f"Touchpad       : {data.get('device_name')} ({data.get('device_path')})")
    print(f"Emulation Mode : {data.get('emulation_mode')}")
    print(f"Target Mode    : {data.get('target_mode')}")
    print(
        f"Target Window  : {tw.get('caption')} "
        f"({tw.get('width')}x{tw.get('height')} @ {tw.get('x')},{tw.get('y')})"
    )


def run_gui(start_enabled: bool = False, minimize_to_hud: bool = False) -> int:
    from PyQt6.QtWidgets import QApplication
    from .gui import TouchMimicController

    app = QApplication(sys.argv)
    app.setApplicationName("TouchMimic")
    app.setDesktopFileName("touchmimic")

    # If an instance is already running, just send it the requested state and show it
    existing = call_daemon_dbus("Status")
    if existing is not None:
        if start_enabled:
            call_daemon_dbus("Enable")
        elif not minimize_to_hud:
            call_daemon_dbus("ShowGui")
        return 0

    controller = TouchMimicController(
        start_enabled=start_enabled,
        minimize_to_hud=minimize_to_hud,
    )
    return app.exec()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="touchmimic",
        description="Turn your touchpad into an on-demand 1:1 touchscreen mimic for TikTok, Android Emulators, and touch apps.",
    )
    sub = parser.add_subparsers(dest="command")

    gui_p = sub.add_parser("gui", help="Launch the TouchMimic GUI & background daemon (default)")
    gui_p.add_argument("--active", action="store_true", help="Start with Touch Mode immediately active")
    gui_p.add_argument("--hud-only", action="store_true", help="Start minimized to the floating HUD pill")

    sub.add_parser("toggle", help="Toggle Touchscreen Mimic Mode ON/OFF")
    sub.add_parser("on", help="Enable Touchscreen Mimic Mode")
    sub.add_parser("off", help="Disable Touchscreen Mimic Mode (restore normal touchpad)")
    sub.add_parser("pin", help="Pin / unpin the currently active window as the 1:1 touch target")
    sub.add_parser("status", help="Show current daemon status and detected touchpad hardware")
    sub.add_parser("permissions", help="Grant access to /dev/input/event* and /dev/uinput via pkexec")

    args = parser.parse_args(argv)
    cmd = args.command or "gui"

    if cmd == "permissions":
        ok, msg = grant_permissions_via_pkexec(install_udev=True)
        print(msg)
        return 0 if ok else 1

    if cmd == "status":
        resp = call_daemon_dbus("Status")
        if resp is not None:
            print_status(resp)
            return 0
        print("Daemon Status  : Not running")
        ok, perm_msg = check_permissions()
        print(f"Permissions    : {perm_msg}")
        tps, kbs = discover_input_devices()
        print("Detected Touchpads:")
        for tp in tps:
            print(f"  - {tp.name} ({tp.path}) [bus=0x{tp.bus:x}, readable={tp.readable}]")
        return 0

    if cmd in ("toggle", "on", "off", "pin"):
        method_map = {
            "toggle": "Toggle",
            "on": "Enable",
            "off": "Disable",
            "pin": "TogglePin",
        }
        resp = call_daemon_dbus(method_map[cmd])
        if resp is not None:
            print_status(resp)
            return 0

        # If daemon is not running and user ran `touchmimic toggle` or `touchmimic on`,
        # spawn the daemon in the background with Touch Mode enabled!
        if cmd in ("toggle", "on"):
            print("TouchMimic daemon not running — launching in HUD mode with Touch Mode ON...")
            root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
            subprocess.Popen(
                [sys.executable, "-m", "touchmimic", "gui", "--active", "--hud-only"],
                cwd=root_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return 0
        print("TouchMimic daemon is not currently running.")
        return 0

    if cmd == "gui":
        return run_gui(
            start_enabled=getattr(args, "active", False),
            minimize_to_hud=getattr(args, "hud_only", False),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())