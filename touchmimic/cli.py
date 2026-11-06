"""Cross-platform CLI entrypoint and IPC client for TouchMimic (Linux, Windows, macOS)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Optional

from .platform_backend import (
    call_local_socket_ipc,
    check_platform_permissions,
    get_os_name,
    grant_platform_permissions,
)
from .window_tracker import DBUS_PATH, DBUS_SERVICE


def call_daemon_ipc(command_or_method: str) -> Optional[dict]:
    """Call running TouchMimic daemon via localhost TCP IPC (all OSes) or DBus (Linux)."""
    cmd_map = {
        "Status": "status",
        "Toggle": "toggle",
        "Enable": "on",
        "Disable": "off",
        "TogglePin": "pin",
        "ShowGui": "show",
    }
    tcp_cmd = cmd_map.get(command_or_method, command_or_method.lower())
    resp = call_local_socket_ipc(tcp_cmd)
    if resp is not None:
        return resp

    if get_os_name() == "linux":
        try:
            from PyQt6.QtCore import QCoreApplication
            from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

            app = QCoreApplication.instance()
            if app is None:
                app = QCoreApplication(sys.argv[:1])
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return None
            iface = QDBusInterface(
                DBUS_SERVICE, DBUS_PATH, "local.touchmimic.DBusBridge", bus
            )
            if not iface.isValid():
                return None
            reply = iface.call(command_or_method)
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
    print(f"Platform       : {get_os_name()}")
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

    existing = call_daemon_ipc("Status")
    if existing is not None:
        if start_enabled:
            call_daemon_ipc("Enable")
        elif not minimize_to_hud:
            call_daemon_ipc("ShowGui")
        return 0

    _controller = TouchMimicController(
        start_enabled=start_enabled,
        minimize_to_hud=minimize_to_hud,
    )
    return app.exec()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="touchmimic",
        description="Turn your touchpad into an on-demand 1:1 touchscreen mimic on Linux, Windows, and macOS.",
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
    sub.add_parser("permissions", help="Grant required OS input permissions (Linux udev/ACL, macOS Accessibility)")

    args = parser.parse_args(argv)
    cmd = args.command or "gui"

    if cmd == "permissions":
        ok, msg = grant_platform_permissions()
        print(msg)
        return 0 if ok else 1

    if cmd == "status":
        resp = call_daemon_ipc("Status")
        if resp is not None:
            print_status(resp)
            return 0
        os_name = get_os_name()
        print(f"Platform       : {os_name}")
        print("Daemon Status  : Not running")
        ok, perm_msg = check_platform_permissions()
        print(f"Permissions    : {perm_msg}")
        if os_name == "linux":
            from .linux_input import discover_input_devices

            tps, _ = discover_input_devices()
            print("Detected Touchpads:")
            for tp in tps:
                print(f"  - {tp.name} ({tp.path}) [bus=0x{tp.bus:x}, readable={tp.readable}]")
        elif os_name == "windows":
            print("Touchpad Driver: Windows Precision Touchpad + Win32 InjectTouchInput")
        elif os_name == "macos":
            print("Touchpad Driver: Apple MultitouchSupport.framework + Quartz CGEvent")
        return 0

    if cmd in ("toggle", "on", "off", "pin"):
        method_map = {
            "toggle": "Toggle",
            "on": "Enable",
            "off": "Disable",
            "pin": "TogglePin",
        }
        resp = call_daemon_ipc(method_map[cmd])
        if resp is not None:
            print_status(resp)
            return 0

        if cmd in ("toggle", "on"):
            print("TouchMimic daemon not running — launching in HUD mode with Touch Mode ON...")
            root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
            popen_kwargs: dict = {
                "cwd": root_dir,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            else:
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(
                [sys.executable, "-m", "touchmimic", "gui", "--active", "--hud-only"],
                **popen_kwargs,
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
