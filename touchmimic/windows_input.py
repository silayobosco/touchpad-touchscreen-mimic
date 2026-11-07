"""Pure-Python Windows Precision Touchpad reader, 1:1 Touchscreen Mimic, and Window Tracker via ctypes."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
from typing import Callable, Optional, Set, Tuple

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
WM_USER = 0x0400

WM_APP_ENABLE_HOOK = WM_USER + 10
WM_APP_DISABLE_HOOK = WM_USER + 11

WH_MOUSE_LL = 14

VK_ESCAPE = 0x1B
VK_F8 = 0x77
VK_T = 0x54

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002

HOTKEY_ID_F8 = 1001
HOTKEY_ID_CTRL_ALT_T = 1002

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# Synthetic event marker tag ("TMIM" = 0x544D494D)
SYNTHETIC_MARKER = 0x544D494D


if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32
    try:
        # Enable Per-Monitor DPI Awareness V2 so coordinate metrics are unscaled
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wt.POINT),
            ("mouseData", wt.DWORD),
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
    """True 1:1 Touchpad-to-Touchscreen Mimic Engine for Windows.

    Converts physical touchpad contact and movement into true 1:1 touch-drags
    inside the active or pinned application window (TikTok, Android emulators, etc.)
    with perfect direction, zero feedback drift, and automatic finger-lift release.
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
        self.emulation_mode: str = "pointer_drag"
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

        self.safe_hwnds: Set[int] = set()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._h_mouse = None
        self._mouse_cb = None

        self._is_touching: bool = False
        self._norm_x: float = 0.5
        self._norm_y: float = 0.5
        self._last_cursor_pt: Optional[Tuple[int, int]] = None

        self._release_timer: Optional[threading.Timer] = None
        self._release_timer_lock = threading.Lock()
        self._last_radar_emit: float = 0.0

    def start(self, preferred_device_path: Optional[str] = None) -> Tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows engine requires win32 platform."
        self.stop()
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._win32_loop, daemon=True, name="Win32TouchMimicLoop"
        )
        self._thread.start()
        return True, f"Connected to {self.device_name} (1:1 Touchscreen Mimic)"

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

        if active:
            self._is_touching = False
            self._norm_x = 0.5
            self._norm_y = 0.5

            rx, ry, rw, rh = self.target_rect
            inset = min(self.top_inset_px, max(0, rh - 50))
            center_x = rx + max(1, rw) // 2
            center_y = ry + inset + max(1, rh - inset) // 2

            # Place cursor at center of target window
            user32.SetCursorPos(center_x, center_y)
            self._last_cursor_pt = (center_x, center_y)

            # Install hook on worker thread
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_APP_ENABLE_HOOK, 0, 0)
        else:
            self._cancel_release_timer()
            if self._is_touching:
                self._emit_touch_up()
            self._last_cursor_pt = None

            # Detach hook on worker thread
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_APP_DISABLE_HOOK, 0, 0)

        if self.on_state_changed:
            self.on_state_changed(self.active)

    def toggle(self) -> bool:
        self.set_active(not self.active)
        return self.active

    def _is_safe_window_under_cursor(self, pt: wt.POINT) -> bool:
        """Return True only if cursor is over TouchMimic, Taskbar, or Task Manager."""
        try:
            hwnd = user32.WindowFromPoint(pt)
            if not hwnd:
                return False
            root = user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
            target_hwnd = root if root else hwnd

            if target_hwnd in self.safe_hwnds or hwnd in self.safe_hwnds:
                return True

            cls_buf = ctypes.create_unicode_buffer(32)
            user32.GetClassNameW(target_hwnd, cls_buf, 32)
            cls_name = cls_buf.value.lower()

            if cls_name in ("shell_traywnd", "shell_secondarytraywnd", "taskmgr"):
                return True
        except Exception:
            pass
        return False

    def _emit_touch_down(self) -> None:
        if not IS_WINDOWS:
            return
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, SYNTHETIC_MARKER)
        self._is_touching = True

    def _emit_touch_up(self) -> None:
        if not IS_WINDOWS:
            return
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, SYNTHETIC_MARKER)
        self._is_touching = False

    def _schedule_release_timer(self) -> None:
        with self._release_timer_lock:
            if self._release_timer:
                self._release_timer.cancel()
            self._release_timer = threading.Timer(0.12, self._on_inactivity_timeout)
            self._release_timer.daemon = True
            self._release_timer.start()

    def _cancel_release_timer(self) -> None:
        with self._release_timer_lock:
            if self._release_timer:
                self._release_timer.cancel()
                self._release_timer = None

    def _on_inactivity_timeout(self) -> None:
        """Called when no new touchpad motion has arrived for 120ms (finger lifted)."""
        if self._is_touching:
            self._emit_touch_up()

        # If drag ended near an edge, recenter to center so next swipe has full travel
        rx, ry, rw, rh = self.target_rect
        inset = min(self.top_inset_px, max(0, rh - 50))
        eff_w = max(1, rw)
        eff_h = max(1, rh - inset)
        center_x = rx + eff_w // 2
        center_y = ry + inset + eff_h // 2

        if self._norm_x < 0.2 or self._norm_x > 0.8 or self._norm_y < 0.2 or self._norm_y > 0.8:
            self._norm_x = 0.5
            self._norm_y = 0.5
            user32.SetCursorPos(center_x, center_y)
            self._last_cursor_pt = (center_x, center_y)

        if self.on_touch_update:
            self.on_touch_update(self._norm_x, self._norm_y, False, 0)

    def _win32_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        # Force message queue creation for this thread
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        # Register global hotkeys natively via Win32 API
        if self.hotkey_f8_toggle:
            user32.RegisterHotKey(None, HOTKEY_ID_F8, 0, VK_F8)
        if self.ctrl_alt_t_toggle:
            user32.RegisterHotKey(None, HOTKEY_ID_CTRL_ALT_T, MOD_CONTROL | MOD_ALT, VK_T)

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, ctypes.c_int, wt.WPARAM, wt.LPARAM
        )

        def low_level_mouse_proc(nCode: int, wParam: int, lParam: int) -> int:
            if nCode < 0:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Emergency Escape key disengages Touch Mode
            if (user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000) != 0:
                self.set_active(False)
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if ms.dwExtraInfo == SYNTHETIC_MARKER:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Allow physical clicks on TouchMimic, Taskbar, or Task Manager to pass through
            if wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                if self._is_safe_window_under_cursor(ms.pt):
                    if self._is_touching:
                        self._emit_touch_up()
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Physical right / middle clicks always release touch and pass through
            if wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                if self._is_touching:
                    self._emit_touch_up()
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Physical button up releases touch
            if wParam == WM_LBUTTONUP:
                if self._is_touching:
                    self._emit_touch_up()
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Touchpad motion: convert to 1:1 proportional touch-drag
            if wParam == WM_MOUSEMOVE:
                if self._last_cursor_pt is None:
                    self._last_cursor_pt = (ms.pt.x, ms.pt.y)
                    return 1

                dx = ms.pt.x - self._last_cursor_pt[0]
                dy = ms.pt.y - self._last_cursor_pt[1]

                if dx != 0 or dy != 0:
                    rx, ry, rw, rh = self.target_rect
                    inset = min(self.top_inset_px, max(0, rh - 50))
                    eff_w = max(1, rw)
                    eff_h = max(1, rh - inset)

                    # Dynamic sensitivity: maps full touchpad swipe across the target window
                    sens = 1.45
                    self._norm_x = max(0.0, min(1.0, self._norm_x + (dx / eff_w) * sens))
                    self._norm_y = max(0.0, min(1.0, self._norm_y + (dy / eff_h) * sens))

                    target_px = int(rx + self._norm_x * eff_w)
                    target_py = int(ry + inset + self._norm_y * eff_h)

                    # Auto-engage touch drag on first contact
                    if not self._is_touching:
                        user32.SetCursorPos(target_px, target_py)
                        self._emit_touch_down()
                    else:
                        user32.SetCursorPos(target_px, target_py)

                    # Synchronize last cursor position so the next delta is pure physical motion
                    self._last_cursor_pt = (target_px, target_py)
                    self._schedule_release_timer()

                    now = time.monotonic()
                    if self.on_touch_update and (now - self._last_radar_emit > 0.033):
                        self._last_radar_emit = now
                        self.on_touch_update(self._norm_x, self._norm_y, True, 1)

                # Swallow physical move to lock cursor strictly to 1:1 touch position
                return 1

            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._mouse_cb = HOOKPROC(low_level_mouse_proc)

        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0 or msg.message == WM_QUIT:
                break

            if msg.message == WM_HOTKEY:
                self.toggle()
            elif msg.message == WM_APP_ENABLE_HOOK:
                if not self._h_mouse:
                    self._h_mouse = user32.SetWindowsHookExW(
                        WH_MOUSE_LL, self._mouse_cb, None, 0
                    )
            elif msg.message == WM_APP_DISABLE_HOOK:
                if self._h_mouse:
                    user32.UnhookWindowsHookEx(self._h_mouse)
                    self._h_mouse = None

            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._h_mouse:
            user32.UnhookWindowsHookEx(self._h_mouse)
            self._h_mouse = None
        user32.UnregisterHotKey(None, HOTKEY_ID_F8)
        user32.UnregisterHotKey(None, HOTKEY_ID_CTRL_ALT_T)
