"""Pure-Python Windows Precision Touchpad reader, Touch Injection (InjectTouchInput), and Window Tracker via ctypes."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# Windows API constants
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEHID = 2

HID_USAGE_PAGE_DIGITIZER = 0x0D
HID_USAGE_DIGITIZER_TOUCH_PAD = 0x05
HID_USAGE_DIGITIZER_TIP_SWITCH = 0x42
HID_USAGE_DIGITIZER_CONTACT_ID = 0x51
HID_USAGE_DIGITIZER_CONTACT_COUNT = 0x54
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_X = 0x30
HID_USAGE_GENERIC_Y = 0x31

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13

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

# Windows 8/10/11 Native Touch Injection Constants
TOUCH_FEEDBACK_DEFAULT = 0x1
PT_TOUCH = 0x00000002
POINTER_FLAG_NONE = 0x00000000
POINTER_FLAG_NEW = 0x00000001
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_FIRSTBUTTON = 0x00000010
POINTER_FLAG_PRIMARY = 0x00002000
POINTER_FLAG_CONFIDENCE = 0x00004000
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000
TOUCH_MASK_CONTACTAREA = 0x00000001
TOUCH_MASK_ORIENTATION = 0x00000002
TOUCH_MASK_PRESSURE = 0x00000004


if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
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
            ("dwExtraInfo", ctypes.c_ulonglong),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", INPUT_UNION)]

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
            ("dwExtraInfo", ctypes.c_ulonglong),
        ]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wt.DWORD),
            ("scanCode", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_ulonglong),
        ]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wt.USHORT),
            ("usUsage", wt.USHORT),
            ("dwFlags", wt.DWORD),
            ("hwndTarget", wt.HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wt.DWORD),
            ("dwSize", wt.DWORD),
            ("hDevice", wt.HANDLE),
            ("wParam", wt.WPARAM),
        ]


def check_windows_permissions() -> Tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not running on Windows"
    return True, "Ready (Windows Precision Touchpad + Native Touch Injection API)"


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
    """Windows Precision Touchpad + Native Touch Injection (`InjectTouchInput`) & `SendInput` engine."""

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
        self._was_touching: bool = False
        self._norm_x: float = 0.5
        self._norm_y: float = 0.5
        self._touch_initialized: bool = False

    def start(self, preferred_device_path: Optional[str] = None) -> Tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows engine requires win32 platform."
        self.stop()
        self._stop_event.clear()

        try:
            if hasattr(user32, "InitializeTouchInjection"):
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
        if not active and self._was_touching:
            self._release_touch_or_click()
            self._was_touching = False
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

    def _emit_touch_or_drag(self, px: int, py: int, touching: bool) -> None:
        if not IS_WINDOWS:
            return
        if self.emulation_mode == "native_touch" and self._touch_initialized:
            ti = POINTER_TOUCH_INFO()
            ti.pointerInfo.pointerType = PT_TOUCH
            ti.pointerInfo.pointerId = 0
            ti.pointerInfo.ptPixelLocation.x = px
            ti.pointerInfo.ptPixelLocation.y = py
            if touching and not self._was_touching:
                ti.pointerInfo.pointerFlags = (
                    POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
                )
            elif touching and self._was_touching:
                ti.pointerInfo.pointerFlags = (
                    POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
                )
            else:
                ti.pointerInfo.pointerFlags = POINTER_FLAG_UP

            ti.touchMask = (
                TOUCH_MASK_CONTACTAREA | TOUCH_MASK_ORIENTATION | TOUCH_MASK_PRESSURE
            )
            ti.rcContact.left = px - 2
            ti.rcContact.top = py - 2
            ti.rcContact.right = px + 2
            ti.rcContact.bottom = py + 2
            ti.orientation = 90
            ti.pressure = 32000
            user32.InjectTouchInput(1, ctypes.byref(ti))
        else:
            # Universal absolute pointer drag (0..65535)
            abs_x = int((px / max(1, self.screen_width)) * 65535)
            abs_y = int((py / max(1, self.screen_height)) * 65535)
            flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            if touching and not self._was_touching:
                flags |= MOUSEEVENTF_LEFTDOWN
            elif not touching and self._was_touching:
                flags |= MOUSEEVENTF_LEFTUP

            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.u.mi.dx = abs_x
            inp.u.mi.dy = abs_y
            inp.u.mi.dwFlags = flags
            inp.u.mi.dwExtraInfo = 0x544D494D  # Tag synthetic events ("TMIM")
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _release_touch_or_click(self) -> None:
        px, py = self._map_to_screen_px(self._norm_x, self._norm_y)
        self._emit_touch_or_drag(px, py, False)

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
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                # Ignore our own synthetic events tagged with 0x544D494D
                if ms.dwExtraInfo == 0x544D494D:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                self._norm_x = max(0.0, min(1.0, ms.pt.x / max(1, self.screen_width)))
                self._norm_y = max(0.0, min(1.0, ms.pt.y / max(1, self.screen_height)))

                if self.active:
                    px, py = self._map_to_screen_px(self._norm_x, self._norm_y)
                    # When active on Windows, touchpad motion immediately touches & drags inside target window
                    self._emit_touch_or_drag(px, py, True)
                    self._was_touching = True
                    if self.on_touch_update:
                        self.on_touch_update(self._norm_x, self._norm_y, True, 1)
                    return 1
                else:
                    if self.on_touch_update:
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

