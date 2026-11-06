"""Pure-Python macOS Trackpad reader (MultitouchSupport.framework) and Quartz CGEvent emitter via ctypes."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from typing import Callable, Optional, Tuple

IS_MACOS = sys.platform == "darwin"

# Quartz CGEvent constants
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventMouseMoved = 5
kCGEventLeftMouseDragged = 6
kCGMouseButtonLeft = 0
kCGHIDEventTap = 0


if IS_MACOS:
    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class MTPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]

    class MTVector(ctypes.Structure):
        _fields_ = [("position", MTPoint), ("velocity", MTPoint)]

    class MTTouch(ctypes.Structure):
        _fields_ = [
            ("frame", ctypes.c_int32),
            ("timestamp", ctypes.c_double),
            ("identifier", ctypes.c_int32),
            ("state", ctypes.c_int32),  # 3=MakeTouch, 4=Touching, 5=BreakTouch, 7=OutOfRange
            ("fingerId", ctypes.c_int32),
            ("handId", ctypes.c_int32),
            ("normalized", MTVector),
            ("size", ctypes.c_float),
            ("unk1", ctypes.c_int32),
            ("angle", ctypes.c_float),
            ("majorAxis", ctypes.c_float),
            ("minorAxis", ctypes.c_float),
            ("absolute", MTVector),
            ("unk2", ctypes.c_int32),
            ("unk3", ctypes.c_int32),
            ("density", ctypes.c_float),
        ]

    MTContactCallbackFunction = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(MTTouch),
        ctypes.c_int,
        ctypes.c_double,
        ctypes.c_int,
    )


def check_macos_permissions() -> Tuple[bool, str]:
    if not IS_MACOS:
        return False, "Not running on macOS"
    try:
        app_services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = bool(app_services.AXIsProcessTrusted())
        if trusted:
            return True, "Ready (macOS MultitouchSupport + Accessibility Trusted)"
        return (
            False,
            "macOS Accessibility permission needed (System Settings -> Privacy & Security -> Accessibility)",
        )
    except Exception as exc:
        return False, f"macOS framework check failed: {exc}"


def prompt_macos_accessibility_permissions() -> Tuple[bool, str]:
    if not IS_MACOS:
        return False, "Not running on macOS"
    try:
        subprocess.Popen(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ]
        )
        return (
            True,
            "Opened macOS Accessibility Settings. Enable TouchMimic / Terminal, then click Retry.",
        )
    except Exception as exc:
        return False, f"Could not open System Settings: {exc}"


def get_macos_frontmost_window_rect(
    ignore_titles: Tuple[str, ...] = ("TouchMimic", "Python")
) -> Optional[Tuple[str, str, int, int, int, int]]:
    """Query frontmost macOS application window geometry via System Events."""
    if not IS_MACOS:
        return None
    script = (
        'tell application "System Events"\n'
        "  set frontApp to first application process whose frontmost is true\n"
        "  set appName to name of frontApp\n"
        "  tell frontApp\n"
        "    if (count of windows) > 0 then\n"
        "      set winTitle to name of front window\n"
        "      set {xPos, yPos} to position of front window\n"
        "      set {wSize, hSize} to size of front window\n"
        '      return appName & "|" & winTitle & "|" & xPos & "|" & yPos & "|" & wSize & "|" & hSize\n'
        "    end if\n"
        "  end tell\n"
        "end tell"
    )
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if res.returncode == 0 and "|" in res.stdout:
            parts = res.stdout.strip().split("|")
            if len(parts) == 6:
                app_name, win_title, x_s, y_s, w_s, h_s = parts
                for ig in ignore_titles:
                    if ig.lower() in app_name.lower() or ig.lower() in win_title.lower():
                        return None
                caption = f"{app_name} — {win_title}" if win_title else app_name
                return (
                    caption,
                    app_name,
                    int(float(x_s)),
                    int(float(y_s)),
                    max(1, int(float(w_s))),
                    max(1, int(float(h_s))),
                )
    except Exception:
        pass
    return None


class MacOSTouchMimicEngine:
    """Reads Apple Trackpad via MultitouchSupport.framework and emits 1:1 Quartz CGEvents."""

    def __init__(
        self,
        on_state_changed: Optional[Callable[[bool], None]] = None,
        on_touch_update: Optional[Callable[[float, float, bool, int], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.on_state_changed = on_state_changed
        self.on_touch_update = on_touch_update
        self.on_error = on_error

        self.active: bool = False
        self.emulation_mode: str = "pointer_drag"
        self.triple_tap_toggle: bool = True
        self.hotkey_f8_toggle: bool = True
        self.ctrl_alt_t_toggle: bool = True

        self.screen_width: int = 1920
        self.screen_height: int = 1080
        self.target_rect: Tuple[int, int, int, int] = (0, 0, 1920, 1080)
        self.top_inset_px: int = 28
        self.margin_pct: float = 0.03

        self.device_name: str = "Apple Multi-Touch Trackpad"
        self.device_path: str = "MultitouchSupport.framework"

        self._mt_lib = None
        self._cg_lib = None
        self._devices: list[ ctypes.c_void_p ] = []
        self._cb_ref = None
        self._was_touching: bool = False
        self._norm_x: float = 0.5
        self._norm_y: float = 0.5
        self._last_triple_tap: float = 0.0

    def start(self, preferred_device_path: Optional[str] = None) -> Tuple[bool, str]:
        if not IS_MACOS:
            return False, "macOS engine requires darwin platform."
        self.stop()

        try:
            self._mt_lib = ctypes.CDLL(
                "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport"
            )
            self._cg_lib = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
        except Exception as exc:
            msg = f"Failed to load macOS frameworks: {exc}"
            if self.on_error:
                self.on_error(msg)
            return False, msg

        # Configure CGEvent signatures
        self._cg_lib.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            CGPoint,
            ctypes.c_uint32,
        ]
        self._cg_lib.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        self._cg_lib.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        self._cg_lib.CFRelease.argtypes = [ctypes.c_void_p]
        self._cg_lib.CGWarpMouseCursorPosition.argtypes = [CGPoint]
        self._cg_lib.CGAssociateMouseAndMouseCursorPosition.argtypes = [ctypes.c_bool]

        # Configure MultitouchSupport signatures
        self._mt_lib.MTDeviceCreateDefault.restype = ctypes.c_void_p
        self._mt_lib.MTRegisterContactFrameCallback.argtypes = [
            ctypes.c_void_p,
            MTContactCallbackFunction,
        ]
        self._mt_lib.MTDeviceStart.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._mt_lib.MTDeviceStop.argtypes = [ctypes.c_void_p]

        dev = self._mt_lib.MTDeviceCreateDefault()
        if not dev:
            msg = "No Apple Multi-Touch Trackpad detected."
            if self.on_error:
                self.on_error(msg)
            return False, msg

        self._cb_ref = MTContactCallbackFunction(self._mt_callback)
        self._mt_lib.MTRegisterContactFrameCallback(dev, self._cb_ref)
        self._mt_lib.MTDeviceStart(dev, 0)
        self._devices = [dev]
        return True, f"Connected to {self.device_name}"

    def stop(self) -> None:
        self.set_active(False)
        if self._mt_lib and self._devices:
            for dev in self._devices:
                try:
                    self._mt_lib.MTDeviceStop(dev)
                except Exception:
                    pass
        self._devices.clear()

    def set_active(self, active: bool) -> None:
        if self.active == active:
            return
        self.active = active
        if self._cg_lib:
            try:
                if active:
                    # Dissociate relative trackpad delta while in 1:1 absolute touch mode
                    self._cg_lib.CGAssociateMouseAndMouseCursorPosition(False)
                else:
                    if self._was_touching:
                        px, py = self._map_to_screen_px(self._norm_x, self._norm_y)
                        self._post_mouse_event(kCGEventLeftMouseUp, px, py)
                        self._was_touching = False
                    self._cg_lib.CGAssociateMouseAndMouseCursorPosition(True)
            except Exception:
                pass
        if self.on_state_changed:
            self.on_state_changed(self.active)

    def toggle(self) -> bool:
        self.set_active(not self.active)
        return self.active

    def _apply_deadzone(self, norm: float) -> float:
        m = max(0.0, min(0.25, self.margin_pct))
        if m <= 0.0:
            return norm
        return max(0.0, min(1.0, (norm - m) / (1.0 - 2.0 * m)))

    def _map_to_screen_px(self, norm_x: float, norm_y: float) -> Tuple[float, float]:
        nx = self._apply_deadzone(norm_x)
        ny = self._apply_deadzone(norm_y)
        rx, ry, rw, rh = self.target_rect
        inset = min(self.top_inset_px, max(0, rh - 50))
        px = rx + nx * max(1, rw)
        py = ry + inset + ny * max(1, rh - inset)
        return (
            max(0.0, min(float(self.screen_width - 1), px)),
            max(0.0, min(float(self.screen_height - 1), py)),
        )

    def _post_mouse_event(self, ev_type: int, px: float, py: float) -> None:
        if not self._cg_lib:
            return
        pt = CGPoint(px, py)
        self._cg_lib.CGWarpMouseCursorPosition(pt)
        ev = self._cg_lib.CGEventCreateMouseEvent(None, ev_type, pt, kCGMouseButtonLeft)
        if ev:
            self._cg_lib.CGEventPost(kCGHIDEventTap, ev)
            self._cg_lib.CFRelease(ev)

    def _mt_callback(
        self,
        device: int,
        touches_ptr,
        num_touches: int,
        timestamp: float,
        frame: int,
    ) -> int:
        active_contacts = []
        for i in range(num_touches):
            t = touches_ptr[i]
            # States 3 (MakeTouch) and 4 (Touching) indicate physical contact on pad
            if t.state in (3, 4):
                active_contacts.append(t)

        fingers = len(active_contacts)
        if fingers == 3 and self.triple_tap_toggle:
            now = time.monotonic()
            if now - self._last_triple_tap > 0.45:
                self._last_triple_tap = now
                self.toggle()
                return 0

        is_touching = fingers > 0
        if is_touching:
            primary = active_contacts[0]
            self._norm_x = max(0.0, min(1.0, float(primary.normalized.position.x)))
            # MultitouchSupport normalized Y has 0.0 at bottom and 1.0 at top; invert for screen top-down
            self._norm_y = max(0.0, min(1.0, 1.0 - float(primary.normalized.position.y)))

        if self.on_touch_update:
            self.on_touch_update(self._norm_x, self._norm_y, is_touching, fingers)

        if not self.active:
            self._was_touching = False
            return 0

        px, py = self._map_to_screen_px(self._norm_x, self._norm_y)
        if is_touching and not self._was_touching:
            self._post_mouse_event(kCGEventLeftMouseDown, px, py)
        elif is_touching and self._was_touching:
            self._post_mouse_event(kCGEventLeftMouseDragged, px, py)
        elif not is_touching and self._was_touching:
            self._post_mouse_event(kCGEventLeftMouseUp, px, py)

        self._was_touching = is_touching
        return 0
