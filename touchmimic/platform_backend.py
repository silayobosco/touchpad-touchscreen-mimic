"""Cross-platform factory and IPC helpers for Linux, Windows, and macOS."""

from __future__ import annotations

import json
import socket
import sys
import threading
from typing import Callable, Optional, Tuple

IPC_HOST = "127.0.0.1"
IPC_PORT = 45872


def get_os_name() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def check_platform_permissions() -> Tuple[bool, str]:
    os_name = get_os_name()
    if os_name == "linux":
        from .linux_input import check_permissions
        return check_permissions()
    if os_name == "windows":
        from .windows_input import check_windows_permissions
        return check_windows_permissions()
    if os_name == "macos":
        from .macos_input import check_macos_permissions
        return check_macos_permissions()
    return False, f"Unsupported platform: {sys.platform}"


def grant_platform_permissions() -> Tuple[bool, str]:
    os_name = get_os_name()
    if os_name == "linux":
        from .linux_input import grant_permissions_via_pkexec
        return grant_permissions_via_pkexec(install_udev=True)
    if os_name == "windows":
        from .windows_input import grant_windows_permissions
        return grant_windows_permissions()
    if os_name == "macos":
        from .macos_input import prompt_macos_accessibility_permissions
        return prompt_macos_accessibility_permissions()
    return False, f"Unsupported platform: {sys.platform}"


def create_platform_engine(
    on_state_changed: Optional[Callable[[bool], None]] = None,
    on_touch_update: Optional[Callable[[float, float, bool, int], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
):
    os_name = get_os_name()
    if os_name == "linux":
        from .linux_input import TouchpadMimicEngine
        return TouchpadMimicEngine(on_state_changed, on_touch_update, on_error)
    if os_name == "windows":
        from .windows_input import WindowsTouchMimicEngine
        return WindowsTouchMimicEngine(on_state_changed, on_touch_update, on_error)
    if os_name == "macos":
        from .macos_input import MacOSTouchMimicEngine
        return MacOSTouchMimicEngine(on_state_changed, on_touch_update, on_error)
    raise RuntimeError(f"Unsupported OS: {sys.platform}")


class LocalSocketIPCServer:
    """Cross-platform localhost IPC server so CLI commands work on Windows, macOS, and Linux."""

    def __init__(
        self,
        command_handler: Callable[[str], None],
        status_provider: Callable[[], dict],
    ) -> None:
        self._command_handler = command_handler
        self._status_provider = status_provider
        self._sock: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((IPC_HOST, IPC_PORT))
            sock.listen(5)
            sock.settimeout(0.5)
            self._sock = sock
        except OSError:
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._serve_loop, daemon=True, name="TouchMimicIPCServer"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _serve_loop(self) -> None:
        while not self._stop_event.is_set() and self._sock:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    raw = conn.recv(1024).decode("utf-8", errors="ignore").strip().lower()
                    if raw in ("toggle", "on", "off", "pin", "show"):
                        self._command_handler(raw)
                    payload = json.dumps(self._status_provider()).encode("utf-8")
                    conn.sendall(payload)
                except Exception:
                    pass


def call_local_socket_ipc(command: str) -> Optional[dict]:
    """Send a command ('status', 'toggle', 'on', 'off', 'pin', 'show') to running daemon over TCP."""
    try:
        with socket.create_connection((IPC_HOST, IPC_PORT), timeout=0.8) as sock:
            sock.sendall(command.encode("utf-8"))
            data = sock.recv(8192)
            if data:
                return json.loads(data.decode("utf-8"))
    except Exception:
        pass
    return None

