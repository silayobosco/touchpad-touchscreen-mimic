"""Cross-platform Active Window Geometry Tracker & IPC Controller (Linux KWin/DBus, Windows Win32, macOS Quartz)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .platform_backend import LocalSocketIPCServer, get_os_name

try:
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    HAS_QTDBUS = True
except ImportError:
    HAS_QTDBUS = False

DBUS_SERVICE = "org.antigravity.TouchMimic"
DBUS_PATH = "/TouchMimic"
KWIN_SCRIPT_NAME = "touchmimic_kwin_tracker"


@dataclass
class WindowGeometry:
    caption: str = "Full Screen"
    resource_class: str = "screen"
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, max(1, self.width), max(1, self.height))

    def summary(self) -> str:
        title = self.caption if len(self.caption) <= 32 else self.caption[:29] + "..."
        return f"{title} ({self.width}x{self.height} @ {self.x},{self.y})"


KWIN_TRACKER_JS = """
var trackedWindow = null;

function sendWindowGeometry(w) {
    if (!w) return;
    var cls = String(w.resourceClass || "").toLowerCase();
    var cap = String(w.caption || "");
    if (cls.indexOf("touchmimic") !== -1 || cap.indexOf("TouchMimic") === 0) {
        return;
    }
    if (w.specialWindow || w.desktopWindow || w.dock) {
        return;
    }
    var g = w.frameGeometry;
    if (!g || g.width <= 10 || g.height <= 10) return;
    var payload = JSON.stringify({
        caption: cap,
        resourceClass: String(w.resourceClass || ""),
        x: Math.round(g.x),
        y: Math.round(g.y),
        width: Math.round(g.width),
        height: Math.round(g.height)
    });
    callDBus("org.antigravity.TouchMimic", "/TouchMimic", "local.touchmimic.DBusBridge", "reportActiveWindow", payload);
}

function onGeometryChanged() {
    if (trackedWindow) {
        sendWindowGeometry(trackedWindow);
    }
}

function bindWindow(w) {
    if (trackedWindow && trackedWindow.frameGeometryChanged) {
        try {
            trackedWindow.frameGeometryChanged.disconnect(onGeometryChanged);
        } catch (e) {}
    }
    trackedWindow = w;
    if (trackedWindow) {
        sendWindowGeometry(trackedWindow);
        if (trackedWindow.frameGeometryChanged) {
            try {
                trackedWindow.frameGeometryChanged.connect(onGeometryChanged);
            } catch (e) {}
        }
    }
}

workspace.windowActivated.connect(function(w) {
    bindWindow(w);
});

if (workspace.activeWindow) {
    bindWindow(workspace.activeWindow);
}
"""


class DBusBridge(QObject):
    """Exposes DBus methods on Linux and forwards commands from LocalSocketIPCServer on all platforms."""

    windowUpdated = pyqtSignal(object)  # emits WindowGeometry
    commandReceived = pyqtSignal(str)   # "toggle", "on", "off", "pin", "show"

    def __init__(self, status_provider: Callable[[], dict], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._status_provider = status_provider

    @pyqtSlot(str)
    def reportActiveWindow(self, payload: str) -> None:
        try:
            data = json.loads(payload)
            geom = WindowGeometry(
                caption=str(data.get("caption") or "Active Window"),
                resource_class=str(data.get("resourceClass") or ""),
                x=int(data.get("x", 0)),
                y=int(data.get("y", 0)),
                width=max(1, int(data.get("width", 1920))),
                height=max(1, int(data.get("height", 1080))),
            )
            self.windowUpdated.emit(geom)
        except Exception:
            pass

    @pyqtSlot(result=str)
    def Toggle(self) -> str:
        self.commandReceived.emit("toggle")
        return json.dumps(self._status_provider())

    @pyqtSlot(result=str)
    def Enable(self) -> str:
        self.commandReceived.emit("on")
        return json.dumps(self._status_provider())

    @pyqtSlot(result=str)
    def Disable(self) -> str:
        self.commandReceived.emit("off")
        return json.dumps(self._status_provider())

    @pyqtSlot(result=str)
    def TogglePin(self) -> str:
        self.commandReceived.emit("pin")
        return json.dumps(self._status_provider())

    @pyqtSlot(result=str)
    def ShowGui(self) -> str:
        self.commandReceived.emit("show")
        return json.dumps(self._status_provider())

    @pyqtSlot(result=str)
    def Status(self) -> str:
        return json.dumps(self._status_provider())


class WindowTracker(QObject):
    """Manages active window tracking, window pinning, and IPC across Linux, Windows, and macOS."""

    targetChanged = pyqtSignal(object)  # WindowGeometry

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        status_provider: Callable[[], dict],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.os_name = get_os_name()

        # Target mode: "active_window", "pinned_window", "fullscreen"
        self.target_mode: str = "active_window"
        self.last_active_window = WindowGeometry(
            caption="Full Screen",
            resource_class="screen",
            x=0,
            y=0,
            width=screen_width,
            height=screen_height,
        )
        self.pinned_window: Optional[WindowGeometry] = None

        self.bridge = DBusBridge(status_provider, self)
        self.bridge.windowUpdated.connect(self._on_window_updated)

        self.ipc_server = LocalSocketIPCServer(
            command_handler=lambda cmd: self.bridge.commandReceived.emit(cmd),
            status_provider=status_provider,
        )

        self._js_temp_path: Optional[str] = None
        self._bus_registered = False
        self._poll_timer: Optional[QTimer] = None

    def start(self) -> bool:
        ipc_ok = self.ipc_server.start()

        if self.os_name == "linux" and HAS_QTDBUS:
            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                ok_svc = bus.registerService(DBUS_SERVICE)
                ok_obj = bus.registerObject(
                    DBUS_PATH,
                    self.bridge,
                    QDBusConnection.RegisterOption.ExportAllSlots,
                )
                self._bus_registered = ok_svc and ok_obj
            self._install_kwin_script()
        elif self.os_name in ("windows", "macos"):
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(300)
            self._poll_timer.timeout.connect(self._poll_os_active_window)
            self._poll_timer.start()
            self._poll_os_active_window()

        return self._bus_registered or ipc_ok

    def stop(self) -> None:
        self.ipc_server.stop()
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        if self.os_name == "linux":
            self._unload_kwin_script()
            if self._js_temp_path and os.path.exists(self._js_temp_path):
                try:
                    os.unlink(self._js_temp_path)
                except OSError:
                    pass
                self._js_temp_path = None

    def _poll_os_active_window(self) -> None:
        info = None
        if self.os_name == "windows":
            from .windows_input import get_windows_foreground_window_rect
            info = get_windows_foreground_window_rect()
        elif self.os_name == "macos":
            from .macos_input import get_macos_frontmost_window_rect
            info = get_macos_frontmost_window_rect()

        if info is not None:
            cap, cls_name, x, y, w, h = info
            geom = WindowGeometry(
                caption=cap,
                resource_class=cls_name,
                x=x,
                y=y,
                width=w,
                height=h,
            )
            if (
                geom.caption != self.last_active_window.caption
                or geom.rect != self.last_active_window.rect
            ):
                self._on_window_updated(geom)

    def _install_kwin_script(self) -> None:
        self._unload_kwin_script()
        try:
            with tempfile.NamedTemporaryFile(
                "w", prefix="touchmimic_kwin_", suffix=".js", delete=False
            ) as f:
                f.write(KWIN_TRACKER_JS)
                self._js_temp_path = f.name

            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.kde.KWin",
                    "--object-path",
                    "/Scripting",
                    "--method",
                    "org.kde.kwin.Scripting.loadScript",
                    self._js_temp_path,
                    KWIN_SCRIPT_NAME,
                ],
                capture_output=True,
                timeout=3,
            )
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.kde.KWin",
                    "--object-path",
                    "/Scripting",
                    "--method",
                    "org.kde.kwin.Scripting.start",
                ],
                capture_output=True,
                timeout=3,
            )
        except Exception:
            pass

    def _unload_kwin_script(self) -> None:
        try:
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.kde.KWin",
                    "--object-path",
                    "/Scripting",
                    "--method",
                    "org.kde.kwin.Scripting.unloadScript",
                    KWIN_SCRIPT_NAME,
                ],
                capture_output=True,
                timeout=2,
            )
        except Exception:
            pass

    def pick_window_interactively(self) -> Optional[WindowGeometry]:
        """Interactive window picker (KWin crosshair on Linux, active window lock on Windows/macOS)."""
        if self.os_name == "linux" and HAS_QTDBUS:
            try:
                bus = QDBusConnection.sessionBus()
                iface = QDBusInterface("org.kde.KWin", "/KWin", "org.kde.KWin", bus)
                reply = iface.call("queryWindowInfo")
                if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
                    info = reply.arguments()[0]
                    if isinstance(info, dict):
                        geom = WindowGeometry(
                            caption=str(info.get("caption") or "Picked Window"),
                            resource_class=str(info.get("resourceClass") or ""),
                            x=int(info.get("x", 0)),
                            y=int(info.get("y", 0)),
                            width=max(1, int(info.get("width", self.screen_width))),
                            height=max(1, int(info.get("height", self.screen_height))),
                        )
                        self.pinned_window = geom
                        self.target_mode = "pinned_window"
                        self.targetChanged.emit(self.current_target())
                        return geom
            except Exception:
                pass
        # Fallback on Windows/macOS: pin the last active non-TouchMimic window
        self.pinned_window = self.last_active_window
        self.target_mode = "pinned_window"
        self.targetChanged.emit(self.current_target())
        return self.pinned_window

    def set_target_mode(self, mode: str) -> None:
        self.target_mode = mode
        if mode == "pinned_window" and self.pinned_window is None:
            self.pinned_window = self.last_active_window
        self.targetChanged.emit(self.current_target())

    def toggle_pin_current_window(self) -> bool:
        if self.target_mode == "pinned_window":
            self.target_mode = "active_window"
            self.targetChanged.emit(self.current_target())
            return False
        else:
            self.pinned_window = self.last_active_window
            self.target_mode = "pinned_window"
            self.targetChanged.emit(self.current_target())
            return True

    def current_target(self) -> WindowGeometry:
        if self.target_mode == "fullscreen":
            return WindowGeometry(
                caption="Full Screen",
                resource_class="screen",
                x=0,
                y=0,
                width=self.screen_width,
                height=self.screen_height,
            )
        if self.target_mode == "pinned_window" and self.pinned_window is not None:
            return self.pinned_window
        return self.last_active_window

    def _on_window_updated(self, geom: WindowGeometry) -> None:
        self.last_active_window = geom
        if (
            self.target_mode == "pinned_window"
            and self.pinned_window is not None
            and geom.resource_class
            and geom.resource_class == self.pinned_window.resource_class
        ):
            self.pinned_window = geom
            self.targetChanged.emit(geom)
        elif self.target_mode == "active_window":
            self.targetChanged.emit(geom)
