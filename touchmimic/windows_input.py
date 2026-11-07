"""Pure-Python Windows Precision Touchpad reader, Touch Injection, and Window Tracker via ctypes."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
from collections import deque
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

# Windows touch-injection constants.  These are deliberately kept here instead
# of relying on a third-party Win32 package so the standalone build stays
# self-contained.
PT_TOUCH = 0x00000002
TOUCH_FEEDBACK_DEFAULT = 0x00000001
POINTER_FLAG_NEW = 0x00000001
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_FIRSTBUTTON = 0x00000010
POINTER_FLAG_PRIMARY = 0x00002000
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000
TOUCH_MASK_CONTACTAREA = 0x00000001

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
    try:
        hid = ctypes.WinDLL("hid.dll")
    except Exception:
        hid = None

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wt.POINT),
            ("mouseData", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class POINTER_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerType", wt.DWORD),
            ("pointerId", wt.UINT),
            ("frameId", wt.UINT),
            ("pointerFlags", wt.DWORD),
            ("sourceDevice", wt.HANDLE),
            ("hwndTarget", wt.HWND),
            ("ptPixelLocation", wt.POINT),
            ("ptHimetricLocation", wt.POINT),
            ("ptPixelLocationRaw", wt.POINT),
            ("ptHimetricLocationRaw", wt.POINT),
            ("dwTime", wt.DWORD),
            ("historyCount", wt.UINT),
            ("InputData", ctypes.c_int32),
            ("dwKeyStates", wt.DWORD),
            ("PerformanceCount", ctypes.c_uint64),
            ("ButtonChangeType", wt.DWORD),
        ]

    class POINTER_TOUCH_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerInfo", POINTER_INFO),
            ("touchFlags", wt.DWORD),
            ("touchMask", wt.DWORD),
            ("rcContact", wt.RECT),
            ("rcContactRaw", wt.RECT),
            ("orientation", wt.DWORD),
            ("pressure", wt.DWORD),
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
    """Robust On-Demand Windows Touchpad Mimic Engine.

    Directly converts physical touchpad finger movements into 1:1 touch-drags
    inside the target application window with zero feedback runaway, correct
    directionality, and automatic finger-lift release.
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
        # Native contacts are what browsers and Android emulators recognise as
        # touch.  Mouse dragging remains a compatibility fallback only.
        self.emulation_mode: str = "native_touch"
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

        self._is_injecting: bool = False
        self._is_touching: bool = False
        self._norm_x: float = 0.5
        self._norm_y: float = 0.5
        self._last_physical_pt: Optional[Tuple[int, int]] = None
        self._synthetic_cursor_points = deque(maxlen=8)
        self._touch_injection_ready = False
        self._using_native_touch = False

        self._release_timer: Optional[threading.Timer] = None
        self._release_timer_lock = threading.Lock()
        self._last_radar_emit: float = 0.0

    def start(self, preferred_device_path: Optional[str] = None) -> Tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows engine requires win32 platform."
        self.stop()
        self._stop_event.clear()
        self._touch_injection_ready = self._initialize_touch_injection()

        self._thread = threading.Thread(
            target=self._win32_loop, daemon=True, name="Win32TouchMimicLoop"
        )
        self._thread.start()
        mode = "native touch injection" if self._touch_injection_ready else "mouse-drag fallback"
        return True, f"Connected to {self.device_name} ({mode})"

    def _initialize_touch_injection(self) -> bool:
        """Enable the built-in Windows 8+ touch injector once per engine."""
        if not IS_WINDOWS:
            return False
        try:
            user32.InitializeTouchInjection.argtypes = (wt.UINT, wt.DWORD)
            user32.InitializeTouchInjection.restype = wt.BOOL
            user32.InjectTouchInput.argtypes = (wt.UINT, ctypes.POINTER(POINTER_TOUCH_INFO))
            user32.InjectTouchInput.restype = wt.BOOL
            return bool(user32.InitializeTouchInjection(1, TOUCH_FEEDBACK_DEFAULT))
        except (AttributeError, OSError):
            return False

    def _make_touch_info(self, px: int, py: int, pointer_flags: int):
        info = POINTER_TOUCH_INFO()
        info.pointerInfo.pointerType = PT_TOUCH
        info.pointerInfo.pointerId = 1
        info.pointerInfo.pointerFlags = pointer_flags
        info.pointerInfo.ptPixelLocation = wt.POINT(px, py)
        # A non-zero contact rectangle is required by InjectTouchInput.
        info.touchMask = TOUCH_MASK_CONTACTAREA
        info.rcContact = wt.RECT(px - 2, py - 2, px + 2, py + 2)
        return info

    def _inject_native_touch(self, px: int, py: int, pointer_flags: int) -> bool:
        if not self._touch_injection_ready:
            return False
        try:
            info = self._make_touch_info(px, py, pointer_flags)
            return bool(user32.InjectTouchInput(1, ctypes.byref(info)))
        except (AttributeError, OSError):
            self._touch_injection_ready = False
            return False

    def _move_fallback_cursor(self, px: int, py: int) -> None:
        """Move the compatibility cursor without re-consuming that move as input."""
        self._synthetic_cursor_points.append((px, py))
        user32.SetCursorPos(px, py)

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
            rx, ry, rw, rh = self.target_rect
            inset = min(self.top_inset_px, max(0, rh - 50))
            eff_w = max(1, rw)
            eff_h = max(1, rh - inset)

            self._norm_x = 0.5
            self._norm_y = 0.5
            center_x = rx + eff_w // 2
            center_y = ry + inset + eff_h // 2

            self._is_touching = False
            self._last_physical_pt = None
            # Native injection does not move the cursor.  Moving it here while
            # a low-level mouse hook is active creates a feedback loop where
            # our synthetic move is interpreted as fresh touchpad movement.
            if self.emulation_mode == "pointer_drag" or not self._touch_injection_ready:
                self._move_fallback_cursor(center_x, center_y)

            # Install hook on demand on worker thread
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_APP_ENABLE_HOOK, 0, 0)
        else:
            self._cancel_release_timer()
            if self._is_touching:
                self._emit_touch_up()
            self._last_physical_pt = None

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

    def _emit_touch_down(self, px: int, py: int) -> bool:
        if not IS_WINDOWS:
            return False

        self._is_injecting = True
        try:
            if self.emulation_mode == "native_touch":
                self._using_native_touch = self._inject_native_touch(
                    px,
                    py,
                    POINTER_FLAG_NEW
                    | POINTER_FLAG_INRANGE
                    | POINTER_FLAG_INCONTACT
                    | POINTER_FLAG_FIRSTBUTTON
                    | POINTER_FLAG_PRIMARY
                    | POINTER_FLAG_DOWN,
                )
                if self._using_native_touch:
                    return True
            self._move_fallback_cursor(px, py)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, SYNTHETIC_MARKER)
            return True
        except Exception:
            return False
        finally:
            self._is_injecting = False

    def _emit_touch_move(self, px: int, py: int) -> bool:
        if not IS_WINDOWS:
            return False

        self._is_injecting = True
        try:
            if self._using_native_touch:
                return self._inject_native_touch(
                    px,
                    py,
                    POINTER_FLAG_INRANGE
                    | POINTER_FLAG_INCONTACT
                    | POINTER_FLAG_FIRSTBUTTON
                    | POINTER_FLAG_PRIMARY
                    | POINTER_FLAG_UPDATE,
                )
            self._move_fallback_cursor(px, py)
            return True
        except Exception:
            return False
        finally:
            self._is_injecting = False

    def _emit_touch_up(self) -> None:
        if not IS_WINDOWS:
            return

        self._is_injecting = True
        try:
            if self._using_native_touch:
                rx, ry, rw, rh = self.target_rect
                inset = min(self.top_inset_px, max(0, rh - 50))
                px = int(rx + self._norm_x * max(1, rw))
                py = int(ry + inset + self._norm_y * max(1, rh - inset))
                self._inject_native_touch(px, py, POINTER_FLAG_PRIMARY | POINTER_FLAG_UP)
                self._using_native_touch = False
                return
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, SYNTHETIC_MARKER)
        except Exception:
            pass
        finally:
            self._is_injecting = False
            self._is_touching = False

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

        # If swipe ended near an edge, reposition back to window center so next swipe has full travel distance
        if self._norm_x < 0.25 or self._norm_x > 0.75 or self._norm_y < 0.25 or self._norm_y > 0.75:
            self._norm_x = 0.5
            self._norm_y = 0.5
            rx, ry, rw, rh = self.target_rect
            inset = min(self.top_inset_px, max(0, rh - 50))
            eff_w = max(1, rw)
            eff_h = max(1, rh - inset)
            center_x = rx + eff_w // 2
            center_y = ry + inset + eff_h // 2
            if not self._using_native_touch:
                self._move_fallback_cursor(center_x, center_y)

        self._last_physical_pt = None
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

            # Ignore our own synthetic events to prevent re-entrancy
            if self._is_injecting:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if ms.dwExtraInfo == SYNTHETIC_MARKER:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # SetCursorPos has no dwExtraInfo parameter.  Its WM_MOUSEMOVE can
            # therefore arrive after _is_injecting is cleared; recognize the
            # exact coordinates we just emitted and never feed them back into
            # the relative touchpad mapper.
            if wParam == WM_MOUSEMOVE and (ms.pt.x, ms.pt.y) in self._synthetic_cursor_points:
                try:
                    self._synthetic_cursor_points.remove((ms.pt.x, ms.pt.y))
                except ValueError:
                    pass
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Allow physical clicks on TouchMimic, Taskbar, or Task Manager to pass through
            if wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                if self._is_safe_window_under_cursor(ms.pt):
                    if self._is_touching:
                        self._emit_touch_up()
                    self._last_physical_pt = None
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Physical right / middle clicks always pass through
            if wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                if self._is_touching:
                    self._emit_touch_up()
                self._last_physical_pt = None
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Physical button up releases touch
            if wParam == WM_LBUTTONUP:
                if self._is_touching:
                    self._emit_touch_up()
                self._last_physical_pt = None
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Track physical touchpad displacement via consecutive MSLLHOOKSTRUCT points
            if self._last_physical_pt is None:
                self._last_physical_pt = (ms.pt.x, ms.pt.y)
                return 1

            dx = ms.pt.x - self._last_physical_pt[0]
            dy = ms.pt.y - self._last_physical_pt[1]
            self._last_physical_pt = (ms.pt.x, ms.pt.y)

            if dx != 0 or dy != 0:
                rx, ry, rw, rh = self.target_rect
                inset = min(self.top_inset_px, max(0, rh - 50))
                eff_w = max(1, rw)
                eff_h = max(1, rh - inset)

                sens = 1.35
                self._norm_x = max(0.0, min(1.0, self._norm_x + (dx / eff_w) * sens))
                self._norm_y = max(0.0, min(1.0, self._norm_y + (dy / eff_h) * sens))

                target_px = int(rx + self._norm_x * eff_w)
                target_py = int(ry + inset + self._norm_y * eff_h)

                if not self._is_touching:
                    self._emit_touch_down(target_px, target_py)
                    self._is_touching = True
                else:
                    self._emit_touch_move(target_px, target_py)

                self._schedule_release_timer()

                now = time.monotonic()
                if self.on_touch_update and (now - self._last_radar_emit > 0.033):
                    self._last_radar_emit = now
                    self.on_touch_update(self._norm_x, self._norm_y, True, 1)

            # Lock cursor inside target window during touch mode
            return 1

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
