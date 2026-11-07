"""Pure-Python Windows Precision Touchpad reader, Touch Injection (InjectTouchInput), and Window Tracker via ctypes."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
from typing import Callable, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# Windows API constants
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13

VK_ESCAPE = 0x1B
VK_F8 = 0x77
VK_T = 0x54
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Synthetic event marker tag ("TMIM" = 0x544D494D)
SYNTHETIC_MARKER = 0x544D494D

# Windows Native Touch Injection Constants
TOUCH_FEEDBACK_DEFAULT = 0x1
PT_TOUCH = 0x00000002
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000
TOUCH_MASK_CONTACTAREA = 0x00000001
TOUCH_MASK_ORIENTATION = 0x00000002
TOUCH_MASK_PRESSURE = 0x00000004


if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32
    try:
        hid = ctypes.WinDLL("hid.dll")
    except Exception:
        hid = None

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wt.LONG),
            ("dy", wt.LONG),
            ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wt.DWORD),
            ("u", INPUT_UNION),
        ]

    class POINTER_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerType", wt.DWORD),
            ("pointerId", ctypes.c_uint32),
            ("frameId", ctypes.c_uint32),
            ("pointerFlags", ctypes.c_uint32),
            ("sourceDevice", wt.HANDLE),
            ("hwndTarget", wt.HWND),
            ("ptPixelLocation", wt.POINT),
            ("ptHimetricLocation", wt.POINT),
            ("ptPixelLocationRaw", wt.POINT),
            ("ptHimetricLocationRaw", wt.POINT),
            ("dwTime", wt.DWORD),
            ("historyCount", ctypes.c_uint32),
            ("inputData", ctypes.c_int32),
            ("dwKeyStates", wt.DWORD),
            ("PerformanceCount", ctypes.c_uint64),
            ("ButtonChangeType", ctypes.c_int),
        ]

    class POINTER_TOUCH_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerInfo", POINTER_INFO),
            ("touchFlags", ctypes.c_uint32),
            ("touchMask", ctypes.c_uint32),
            ("rcContact", wt.RECT),
            ("rcContactRaw", wt.RECT),
            ("orientation", ctypes.c_uint32),
            ("pressure", ctypes.c_uint32),
        ]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wt.POINT),
            ("mouseData", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wt.DWORD),
            ("scanCode", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


def check_windows_permissions() -> Tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not running on Windows"
    try:
        is_admin = bool(shell32.IsUserAnAdmin() != 0)
    except Exception:
        is_admin = False

    if not is_admin:
        return (
            False,
            "Administrator permission required: Windows UIPI blocks touch injection into emulators, browsers, and elevated apps.",
        )
    return True, "Ready (Running with Administrator privileges)"


def grant_windows_permissions() -> Tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not running on Windows"
    try:
        is_admin = bool(shell32.IsUserAnAdmin() != 0)
        if is_admin:
            return True, "Already running with Administrator privileges."

        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
        else:
            exe = sys.executable
            params = f'-m touchmimic {" ".join(sys.argv[1:])}'

        ret = shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if ret > 32:
            # Successfully launched elevated process — gracefully exit this un-elevated instance
            sys.exit(0)
        return False, "UAC prompt was cancelled or elevation was declined."
    except Exception as exc:
        return False, f"Elevation failed: {exc}"


def get_windows_foreground_window_rect(
    ignore_titles: Tuple[str, ...] = ("TouchMimic",)
) -> Optional[Tuple[str, str, int, int, int, int]]:
    """Return (caption, class_name, x, y, width, height) of the active Windows foreground window."""
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    caption = buf.value.strip() or "Active Window"

    cls_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls_buf, 256)
    cls_name = cls_buf.value.strip()

    for ignored in ignore_titles:
        if ignored.lower() in caption.lower() or ignored.lower() in cls_name.lower():
            return None

    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 20 or h <= 20:
        return None
    return (caption, cls_name, rect.left, rect.top, w, h)


class WindowsTouchMimicEngine:
    """Robust Windows Precision Touchpad & Touch Injection Engine.

    Guarantees:
      - Never freezes the system or locks the mouse
      - Never blocks Task Manager, Taskbar, or TouchMimic controls
      - Immune to re-entrancy via synthetic event filtering & guard flags
      - Smooth touch-drag with automatic inactivity release
      - Emergency ESC key to instantly disengage
    """

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
        self.emulation_mode: str = "pointer_drag"  # "pointer_drag" or "native_touch"
        self.triple_tap_toggle: bool = True
        self.hotkey_f8_toggle: bool = True
        self.ctrl_alt_t_toggle: bool = True

        self.screen_width: int = 1920
        self.screen_height: int = 1080
        self.target_rect: Tuple[int, int, int, int] = (0, 0, 1920, 1080)
        self.top_inset_px: int = 32
        self.margin_pct: float = 0.03

        self.device_name: str = "Windows Precision Touchpad"
        self.device_path: str = "HID\\UsagePage_0x0D_Usage_0x05"

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None

        self._is_injecting: bool = False
        self._is_touching: bool = False
        self._norm_x: float = 0.5
        self._norm_y: float = 0.5
        self._last_physical_pt: Optional[Tuple[int, int]] = None

        self._release_timer: Optional[threading.Timer] = None
        self._release_timer_lock = threading.Lock()
        self._last_radar_emit: float = 0.0
        self._touch_initialized: bool = False

    def start(self, preferred_device_path: Optional[str] = None) -> Tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows engine requires win32 platform."
        self.stop()
        self._stop_event.clear()

        if not self._touch_initialized and hasattr(user32, "InitializeTouchInjection"):
            try:
                self._touch_initialized = bool(
                    user32.InitializeTouchInjection(5, TOUCH_FEEDBACK_DEFAULT)
                )
            except Exception:
                self._touch_initialized = False

        self._thread = threading.Thread(
            target=self._win32_loop, daemon=True, name="Win32TouchMimicLoop"
        )
        self._thread.start()
        return True, f"Connected to {self.device_name}"

    def stop(self) -> None:
        self.set_active(False)
        self._stop_event.set()
        if IS_WINDOWS and self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def set_active(self, active: bool) -> None:
        if self.active == active:
            return
        self.active = active
        if not active:
            self._cancel_release_timer()
            if self._is_touching:
                self._emit_touch_up()
            self._last_physical_pt = None
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

    def _map_to_screen_px(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        nx = self._apply_deadzone(norm_x)
        ny = self._apply_deadzone(norm_y)
        rx, ry, rw, rh = self.target_rect
        inset = min(self.top_inset_px, max(0, rh - 50))
        px = int(rx + nx * max(1, rw))
        py = int(ry + inset + ny * max(1, rh - inset))
        return (
            max(0, min(self.screen_width - 1, px)),
            max(0, min(self.screen_height - 1, py)),
        )

    def _is_safe_window_under_cursor(self, pt: wt.POINT) -> bool:
        """Return True if cursor is over TouchMimic, Taskbar, or Task Manager.

        Never intercept input when interacting with system controls or TouchMimic!
        """
        try:
            hwnd = user32.WindowFromPoint(pt)
            if not hwnd:
                return False
            root = user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
            target_hwnd = root if root else hwnd

            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(target_hwnd, cls_buf, 256)
            cls_name = cls_buf.value.lower()

            if cls_name in (
                "shell_traywnd",
                "shell_secondarytraywnd",
                "dv2controlhost",
                "windows.ui.core.corewindow",
                "progman",
                "workerw",
            ):
                return True

            title_len = user32.GetWindowTextLengthW(target_hwnd)
            title_buf = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(target_hwnd, title_buf, title_len + 1)
            title = title_buf.value.lower()

            if "touchmimic" in title or "task manager" in title or "taskmgr" in cls_name:
                return True
        except Exception:
            pass
        return False

    def _emit_touch_down(self, px: int, py: int) -> bool:
        if not IS_WINDOWS:
            return False

        if self.emulation_mode == "native_touch" and self._touch_initialized:
            ti = POINTER_TOUCH_INFO()
            ti.pointerInfo.pointerType = PT_TOUCH
            ti.pointerInfo.pointerId = 0
            ti.pointerInfo.ptPixelLocation.x = px
            ti.pointerInfo.ptPixelLocation.y = py
            ti.pointerInfo.pointerFlags = (
                POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
            )
            ti.touchMask = (
                TOUCH_MASK_CONTACTAREA | TOUCH_MASK_ORIENTATION | TOUCH_MASK_PRESSURE
            )
            ti.rcContact.left = px - 2
            ti.rcContact.top = py - 2
            ti.rcContact.right = px + 2
            ti.rcContact.bottom = py + 2
            ti.orientation = 90
            ti.pressure = 32000
            self._is_injecting = True
            try:
                return bool(user32.InjectTouchInput(1, ctypes.byref(ti)))
            finally:
                self._is_injecting = False

        # Universal pointer drag
        abs_x = int((px / max(1, self.screen_width)) * 65535)
        abs_y = int((py / max(1, self.screen_height)) * 65535)
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.u.mi.dx = abs_x
        inp.u.mi.dy = abs_y
        inp.u.mi.dwFlags = (
            MOUSEEVENTF_MOVE
            | MOUSEEVENTF_ABSOLUTE
            | MOUSEEVENTF_VIRTUALDESK
            | MOUSEEVENTF_LEFTDOWN
        )
        inp.u.mi.dwExtraInfo = SYNTHETIC_MARKER

        self._is_injecting = True
        try:
            res = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            return res > 0
        finally:
            self._is_injecting = False

    def _emit_touch_move(self, px: int, py: int) -> bool:
        if not IS_WINDOWS:
            return False

        if self.emulation_mode == "native_touch" and self._touch_initialized:
            ti = POINTER_TOUCH_INFO()
            ti.pointerInfo.pointerType = PT_TOUCH
            ti.pointerInfo.pointerId = 0
            ti.pointerInfo.ptPixelLocation.x = px
            ti.pointerInfo.ptPixelLocation.y = py
            ti.pointerInfo.pointerFlags = (
                POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
            )
            ti.touchMask = (
                TOUCH_MASK_CONTACTAREA | TOUCH_MASK_ORIENTATION | TOUCH_MASK_PRESSURE
            )
            ti.rcContact.left = px - 2
            ti.rcContact.top = py - 2
            ti.rcContact.right = px + 2
            ti.rcContact.bottom = py + 2
            ti.orientation = 90
            ti.pressure = 32000
            self._is_injecting = True
            try:
                return bool(user32.InjectTouchInput(1, ctypes.byref(ti)))
            finally:
                self._is_injecting = False

        abs_x = int((px / max(1, self.screen_width)) * 65535)
        abs_y = int((py / max(1, self.screen_height)) * 65535)
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.u.mi.dx = abs_x
        inp.u.mi.dy = abs_y
        inp.u.mi.dwFlags = (
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        )
        inp.u.mi.dwExtraInfo = SYNTHETIC_MARKER

        self._is_injecting = True
        try:
            res = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            return res > 0
        finally:
            self._is_injecting = False

    def _emit_touch_up(self) -> None:
        if not IS_WINDOWS or not self._is_touching:
            return
        px, py = self._map_to_screen_px(self._norm_x, self._norm_y)

        if self.emulation_mode == "native_touch" and self._touch_initialized:
            ti = POINTER_TOUCH_INFO()
            ti.pointerInfo.pointerType = PT_TOUCH
            ti.pointerInfo.pointerId = 0
            ti.pointerInfo.ptPixelLocation.x = px
            ti.pointerInfo.ptPixelLocation.y = py
            ti.pointerInfo.pointerFlags = POINTER_FLAG_UP
            self._is_injecting = True
            try:
                user32.InjectTouchInput(1, ctypes.byref(ti))
            finally:
                self._is_injecting = False
                self._is_touching = False
        else:
            abs_x = int((px / max(1, self.screen_width)) * 65535)
            abs_y = int((py / max(1, self.screen_height)) * 65535)
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.u.mi.dx = abs_x
            inp.u.mi.dy = abs_y
            inp.u.mi.dwFlags = (
                MOUSEEVENTF_MOVE
                | MOUSEEVENTF_ABSOLUTE
                | MOUSEEVENTF_VIRTUALDESK
                | MOUSEEVENTF_LEFTUP
            )
            inp.u.mi.dwExtraInfo = SYNTHETIC_MARKER
            self._is_injecting = True
            try:
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            finally:
                self._is_injecting = False
                self._is_touching = False

        if self.on_touch_update:
            self.on_touch_update(self._norm_x, self._norm_y, False, 0)

    def _schedule_release_timer(self) -> None:
        with self._release_timer_lock:
            if self._release_timer:
                self._release_timer.cancel()
            self._release_timer = threading.Timer(0.085, self._on_inactivity_timeout)
            self._release_timer.daemon = True
            self._release_timer.start()

    def _cancel_release_timer(self) -> None:
        with self._release_timer_lock:
            if self._release_timer:
                self._release_timer.cancel()
                self._release_timer = None

    def _on_inactivity_timeout(self) -> None:
        """Called when no new touchpad motion has arrived for 85ms (finger lifted)."""
        if self._is_touching:
            self._emit_touch_up()
        self._last_physical_pt = None

    def _win32_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        last_toggle = 0.0

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, ctypes.c_int, wt.WPARAM, wt.LPARAM
        )

        def low_level_kbd_proc(nCode: int, wParam: int, lParam: int) -> int:
            nonlocal last_toggle
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                now = time.monotonic()

                # Emergency disengage on Escape key
                if kb.vkCode == VK_ESCAPE and self.active:
                    self.set_active(False)
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                if now - last_toggle > 0.35:
                    if self.hotkey_f8_toggle and kb.vkCode == VK_F8:
                        last_toggle = now
                        self.toggle()
                        return 1
                    ctrl = (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0
                    alt = (user32.GetAsyncKeyState(VK_MENU) & 0x8000) != 0
                    if self.ctrl_alt_t_toggle and ctrl and alt and kb.vkCode == VK_T:
                        last_toggle = now
                        self.toggle()
                        return 1
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        def low_level_mouse_proc(nCode: int, wParam: int, lParam: int) -> int:
            if nCode >= 0:
                # 1. Ignore synthetic events injected by TouchMimic to prevent re-entrancy
                if self._is_injecting:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if ms.dwExtraInfo == SYNTHETIC_MARKER:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                # 2. Never intercept input over TouchMimic, Taskbar, or Task Manager
                if self._is_safe_window_under_cursor(ms.pt):
                    if self._is_touching:
                        self._emit_touch_up()
                    self._last_physical_pt = None
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                # 3. Touchpad motion tracking
                if self.active:
                    # Physical clicks always pass through or release touch
                    if wParam in (WM_LBUTTONUP, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                        if self._is_touching:
                            self._emit_touch_up()
                        self._last_physical_pt = None
                        return user32.CallNextHookEx(None, nCode, wParam, lParam)

                    if self._last_physical_pt is None:
                        self._last_physical_pt = (ms.pt.x, ms.pt.y)
                        dx = 0
                        dy = 0
                    else:
                        dx = ms.pt.x - self._last_physical_pt[0]
                        dy = ms.pt.y - self._last_physical_pt[1]
                        self._last_physical_pt = (ms.pt.x, ms.pt.y)

                    # Update proportional position within target window bounds
                    step_x = dx / max(1, self.screen_width)
                    step_y = dy / max(1, self.screen_height)
                    self._norm_x = min(1.0, max(0.0, self._norm_x + step_x))
                    self._norm_y = min(1.0, max(0.0, self._norm_y + step_y))

                    px, py = self._map_to_screen_px(self._norm_x, self._norm_y)

                    # Touch lifecycle: Start touch down or continue drag
                    success = False
                    if not self._is_touching:
                        success = self._emit_touch_down(px, py)
                        if success:
                            self._is_touching = True
                    else:
                        success = self._emit_touch_move(px, py)

                    self._schedule_release_timer()

                    # Throttle UI radar update to at most ~30 FPS
                    now = time.monotonic()
                    if self.on_touch_update and (now - self._last_radar_emit > 0.033):
                        self._last_radar_emit = now
                        self.on_touch_update(
                            self._norm_x, self._norm_y, self._is_touching, 1
                        )

                    # If injection succeeded, swallow the physical event;
                    # If injection was denied (e.g. no admin), DO NOT swallow to prevent freezing!
                    if success:
                        return 1
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                else:
                    self._last_physical_pt = (ms.pt.x, ms.pt.y)
                    self._norm_x = max(0.0, min(1.0, ms.pt.x / max(1, self.screen_width)))
                    self._norm_y = max(0.0, min(1.0, ms.pt.y / max(1, self.screen_height)))
                    now = time.monotonic()
                    if self.on_touch_update and (now - self._last_radar_emit > 0.05):
                        self._last_radar_emit = now
                        self.on_touch_update(self._norm_x, self._norm_y, False, 0)

            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        kbd_cb = HOOKPROC(low_level_kbd_proc)
        mouse_cb = HOOKPROC(low_level_mouse_proc)
        h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kbd_cb, None, 0)
        h_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_cb, None, 0)

        msg = wt.MSG()
        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0 or msg.message == WM_QUIT:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if h_kbd:
            user32.UnhookWindowsHookEx(h_kbd)
        if h_mouse:
            user32.UnhookWindowsHookEx(h_mouse)
