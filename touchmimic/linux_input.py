"""Pure-Python Linux evdev reader and uinput virtual touchscreen/pointer emitter."""

from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import select
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# Linux input event constants (<linux/input.h> & <linux/input-event-codes.h>)
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

SYN_REPORT = 0x00

# Keys & buttons
KEY_ESC = 1
KEY_T = 20
KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
KEY_F8 = 66
KEY_F9 = 67
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100


BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_TOOL_FINGER = 0x145
BTN_TOOL_QUINTTAP = 0x148
BTN_TOUCH = 0x14A
BTN_TOOL_DOUBLETAP = 0x14D
BTN_TOOL_TRIPLETAP = 0x14E
BTN_TOOL_QUADTAP = 0x14F

# Absolute axes
ABS_X = 0x00
ABS_Y = 0x01
ABS_PRESSURE = 0x18
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TOOL_TYPE = 0x37
ABS_MT_TRACKING_ID = 0x39

# Input properties
INPUT_PROP_POINTER = 0x00
INPUT_PROP_DIRECT = 0x01
INPUT_PROP_BUTTONPAD = 0x02

# ioctl helpers
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction: int, ioc_type: int, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (ioc_type << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IO(ioc_type: int, nr: int) -> int:
    return _IOC(_IOC_NONE, ioc_type, nr, 0)


def _IOR(ioc_type: int, nr: int, size: int) -> int:
    return _IOC(_IOC_READ, ioc_type, nr, size)


def _IOW(ioc_type: int, nr: int, size: int) -> int:
    return _IOC(_IOC_WRITE, ioc_type, nr, size)


# evdev ioctls
def EVIOCGABS(axis: int) -> int:
    # struct input_absinfo is 6 * int32 = 24 bytes
    return _IOR(ord("E"), 0x40 + axis, 24)


EVIOCGRAB = _IOW(ord("E"), 0x90, 4)

# uinput ioctls (<linux/uinput.h>)
UINPUT_MAX_NAME_SIZE = 80
UI_DEV_CREATE = _IO(ord("U"), 1)
UI_DEV_DESTROY = _IO(ord("U"), 2)
UI_SET_EVBIT = _IOW(ord("U"), 100, 4)
UI_SET_KEYBIT = _IOW(ord("U"), 101, 4)
UI_SET_RELBIT = _IOW(ord("U"), 102, 4)
UI_SET_ABSBIT = _IOW(ord("U"), 103, 4)
UI_SET_PROPBIT = _IOW(ord("U"), 110, 4)

# 64-bit Linux struct input_event: timeval (ll = 16 bytes) + type (H) + code (H) + value (i) = 24 bytes
EVENT_STRUCT = struct.Struct("llHHi")
ABSINFO_STRUCT = struct.Struct("iiiiii")

UINPUT_MAX_COORD = 32767
MAX_MT_SLOTS = 5


class InputId(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]


class UinputUserDev(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * UINPUT_MAX_NAME_SIZE),
        ("id", InputId),
        ("ff_effects_max", ctypes.c_uint32),
        ("absmax", ctypes.c_int32 * 64),
        ("absmin", ctypes.c_int32 * 64),
        ("absfuzz", ctypes.c_int32 * 64),
        ("absflat", ctypes.c_int32 * 64),
    ]


@dataclass
class AxisRange:
    value: int = 0
    minimum: int = 0
    maximum: int = 1
    fuzz: int = 0
    flat: int = 0
    resolution: int = 0

    @property
    def span(self) -> int:
        return max(1, self.maximum - self.minimum)

    def normalize(self, raw: int) -> float:
        norm = (raw - self.minimum) / float(self.span)
        return max(0.0, min(1.0, norm))


@dataclass
class DeviceInfo:
    path: str
    name: str
    phys: str
    bus: int
    is_touchpad: bool = False
    is_keyboard: bool = False
    readable: bool = False

    @property
    def priority_score(self) -> int:
        """Prefer I2C / USB precision touchpads over fallback PS/2 devices."""
        score = 0
        lname = self.name.lower()
        if "touchpad" in lname:
            score += 50
        if self.bus == 0x18 or "i2c" in self.phys.lower():
            score += 40
        if "syna" in lname or "elan" in lname or "alps" in lname:
            score += 20
        if "ps/2" in lname:
            score -= 10
        return score


@dataclass
class TouchSlotState:
    active: bool = False
    tracking_id: int = -1
    raw_x: int = 0
    raw_y: int = 0
    norm_x: float = 0.5
    norm_y: float = 0.5


def discover_input_devices() -> Tuple[List[DeviceInfo], List[DeviceInfo]]:
    """Return (touchpads, keyboards) sorted by priority."""
    touchpads: List[DeviceInfo] = []
    keyboards: List[DeviceInfo] = []

    for sys_ev in sorted(glob.glob("/sys/class/input/event*")):
        ev_name = os.path.basename(sys_ev)
        dev_path = f"/dev/input/{ev_name}"
        dev_dir = os.path.join(sys_ev, "device")
        name_file = os.path.join(dev_dir, "name")
        if not os.path.exists(name_file):
            continue
        try:
            with open(name_file, "r", encoding="utf-8", errors="ignore") as f:
                name = f.read().strip()
        except OSError:
            continue

        # Ignore our own virtual devices
        if "TouchMimic" in name:
            continue

        phys = ""
        phys_file = os.path.join(dev_dir, "phys")
        if os.path.exists(phys_file):
            try:
                with open(phys_file, "r", encoding="utf-8", errors="ignore") as f:
                    phys = f.read().strip()
            except OSError:
                pass

        bus = 0
        id_bus_file = os.path.join(dev_dir, "id", "bustype")
        if os.path.exists(id_bus_file):
            try:
                with open(id_bus_file, "r", encoding="utf-8") as f:
                    bus = int(f.read().strip(), 16)
            except (OSError, ValueError):
                pass

        abs_cap = ""
        abs_file = os.path.join(dev_dir, "capabilities", "abs")
        if os.path.exists(abs_file):
            try:
                with open(abs_file, "r", encoding="utf-8") as f:
                    abs_cap = f.read().strip()
            except OSError:
                pass

        key_cap = ""
        key_file = os.path.join(dev_dir, "capabilities", "key")
        if os.path.exists(key_file):
            try:
                with open(key_file, "r", encoding="utf-8") as f:
                    key_cap = f.read().strip()
            except OSError:
                pass

        lname = name.lower()
        is_tp = ("touchpad" in lname or "synaptics" in lname or "elan" in lname) and (
            abs_cap not in ("", "0")
        )
        is_kb = ("keyboard" in lname or "kbd" in lname) and len(key_cap) > 20

        readable = os.access(dev_path, os.R_OK)
        info = DeviceInfo(
            path=dev_path,
            name=name,
            phys=phys,
            bus=bus,
            is_touchpad=is_tp,
            is_keyboard=is_kb,
            readable=readable,
        )
        if is_tp:
            touchpads.append(info)
        elif is_kb:
            keyboards.append(info)

    touchpads.sort(key=lambda d: d.priority_score, reverse=True)
    return touchpads, keyboards


def check_permissions() -> Tuple[bool, str]:
    """Check if current user has read access to touchpad and write access to /dev/uinput."""
    touchpads, _ = discover_input_devices()
    if not touchpads:
        return False, "No touchpad device found in /sys/class/input."
    tp = touchpads[0]
    tp_ok = os.access(tp.path, os.R_OK)
    uinput_ok = os.access("/dev/uinput", os.W_OK)
    if tp_ok and uinput_ok:
        return True, f"Ready ({tp.name} at {tp.path})"
    missing = []
    if not tp_ok:
        missing.append(f"{tp.path} (read)")
    if not uinput_ok:
        missing.append("/dev/uinput (write)")
    return False, f"Permission needed for: {', '.join(missing)}"


def grant_permissions_via_pkexec(install_udev: bool = True) -> Tuple[bool, str]:
    """Use pkexec to grant instant ACL access to /dev/input/event* and /dev/uinput and install udev rule."""
    user = os.environ.get("USER", "boscoovic")
    udev_rule = (
        'KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput", TAG+="uaccess"\n'
        'KERNEL=="event*", SUBSYSTEM=="input", MODE="0660", GROUP="input", TAG+="uaccess"\n'
    )
    cmds = [
        f"setfacl -m u:{user}:rw /dev/uinput /dev/input/event*",
        f"usermod -aG input {user} || true",
    ]
    if install_udev:
        cmds.append(
            f"printf '%s' '{udev_rule}' > /etc/udev/rules.d/99-touchmimic.rules && "
            "udevadm control --reload-rules || true"
        )
    shell_cmd = " && ".join(cmds)
    try:
        res = subprocess.run(
            ["pkexec", "sh", "-c", shell_cmd],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if res.returncode == 0:
            return True, "Permissions granted successfully!"
        return False, f"pkexec exited with {res.returncode}: {res.stderr.strip()}"
    except Exception as exc:
        return False, f"Failed to run pkexec: {exc}"


class VirtualOutputDevices:
    """Manages uinput virtual devices for Pointer Touch-Drag and Native Multi-Touch Screen."""

    def __init__(self) -> None:
        self.pointer_fd: Optional[int] = None
        self.touch_fd: Optional[int] = None
        self._lock = threading.Lock()

    def open(self) -> None:
        with self._lock:
            if self.pointer_fd is None:
                self.pointer_fd = self._create_pointer_device()
            if self.touch_fd is None:
                self.touch_fd = self._create_touchscreen_device()

    def close(self) -> None:
        with self._lock:
            for fd in (self.pointer_fd, self.touch_fd):
                if fd is not None:
                    try:
                        fcntl.ioctl(fd, UI_DEV_DESTROY)
                    except OSError:
                        pass
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self.pointer_fd = None
            self.touch_fd = None

    def _create_pointer_device(self) -> int:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_LEFT)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_RIGHT)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_Y)
        fcntl.ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)

        udev = UinputUserDev()
        udev.name = b"TouchMimic Absolute Pointer"
        udev.id.bustype = 0x03  # BUS_USB
        udev.id.vendor = 0x1209
        udev.id.product = 0x544D
        udev.id.version = 1
        udev.absmin[ABS_X] = 0
        udev.absmax[ABS_X] = UINPUT_MAX_COORD
        udev.absmin[ABS_Y] = 0
        udev.absmax[ABS_Y] = UINPUT_MAX_COORD

        os.write(fd, bytes(udev))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        return fd

    def _create_touchscreen_device(self) -> int:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for axis in (
            ABS_X,
            ABS_Y,
            ABS_MT_SLOT,
            ABS_MT_POSITION_X,
            ABS_MT_POSITION_Y,
            ABS_MT_TRACKING_ID,
        ):
            fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
        fcntl.ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)

        udev = UinputUserDev()
        udev.name = b"TouchMimic Virtual Touchscreen"
        udev.id.bustype = 0x03
        udev.id.vendor = 0x1209
        udev.id.product = 0x544E
        udev.id.version = 1
        udev.absmin[ABS_X] = 0
        udev.absmax[ABS_X] = UINPUT_MAX_COORD
        udev.absmin[ABS_Y] = 0
        udev.absmax[ABS_Y] = UINPUT_MAX_COORD
        udev.absmin[ABS_MT_SLOT] = 0
        udev.absmax[ABS_MT_SLOT] = MAX_MT_SLOTS - 1
        udev.absmin[ABS_MT_POSITION_X] = 0
        udev.absmax[ABS_MT_POSITION_X] = UINPUT_MAX_COORD
        udev.absmin[ABS_MT_POSITION_Y] = 0
        udev.absmax[ABS_MT_POSITION_Y] = UINPUT_MAX_COORD
        udev.absmin[ABS_MT_TRACKING_ID] = 0
        udev.absmax[ABS_MT_TRACKING_ID] = 65535

        os.write(fd, bytes(udev))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        return fd

    @staticmethod
    def _write_events(fd: int, events: List[Tuple[int, int, int]]) -> None:
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        buf = bytearray()
        for ev_type, ev_code, ev_val in events:
            buf.extend(EVENT_STRUCT.pack(sec, usec, ev_type, ev_code, int(ev_val)))
        buf.extend(EVENT_STRUCT.pack(sec, usec, EV_SYN, SYN_REPORT, 0))
        os.write(fd, buf)

    def emit_pointer_touch(self, u_x: int, u_y: int, touching: bool, was_touching: bool) -> None:
        with self._lock:
            if self.pointer_fd is None:
                return
            u_x = max(0, min(UINPUT_MAX_COORD, u_x))
            u_y = max(0, min(UINPUT_MAX_COORD, u_y))
            if touching and not was_touching:
                # Move cursor to touch start location first, then press BTN_LEFT
                self._write_events(
                    self.pointer_fd,
                    [(EV_ABS, ABS_X, u_x), (EV_ABS, ABS_Y, u_y)],
                )
                self._write_events(
                    self.pointer_fd,
                    [(EV_KEY, BTN_LEFT, 1)],
                )
            elif touching and was_touching:
                self._write_events(
                    self.pointer_fd,
                    [(EV_ABS, ABS_X, u_x), (EV_ABS, ABS_Y, u_y)],
                )
            elif not touching and was_touching:
                self._write_events(
                    self.pointer_fd,
                    [(EV_KEY, BTN_LEFT, 0)],
                )

    def emit_pointer_hover(self, u_x: int, u_y: int) -> None:
        with self._lock:
            if self.pointer_fd is None:
                return
            u_x = max(0, min(UINPUT_MAX_COORD, u_x))
            u_y = max(0, min(UINPUT_MAX_COORD, u_y))
            self._write_events(
                self.pointer_fd,
                [(EV_ABS, ABS_X, u_x), (EV_ABS, ABS_Y, u_y)],
            )

    def emit_native_touch_frame(
        self,
        slots: Dict[int, Tuple[bool, int, int, int]],
        primary_xy: Optional[Tuple[int, int]],
        any_active: bool,
    ) -> None:
        """Emit Multi-Touch Protocol B frame on virtual touchscreen."""
        with self._lock:
            if self.touch_fd is None:
                return
            events: List[Tuple[int, int, int]] = []
            for slot_idx, (active, tracking_id, u_x, u_y) in slots.items():
                if slot_idx >= MAX_MT_SLOTS:
                    continue
                events.append((EV_ABS, ABS_MT_SLOT, slot_idx))
                if active:
                    u_x = max(0, min(UINPUT_MAX_COORD, u_x))
                    u_y = max(0, min(UINPUT_MAX_COORD, u_y))
                    events.append((EV_ABS, ABS_MT_TRACKING_ID, max(1, tracking_id)))
                    events.append((EV_ABS, ABS_MT_POSITION_X, u_x))
                    events.append((EV_ABS, ABS_MT_POSITION_Y, u_y))
                else:
                    events.append((EV_ABS, ABS_MT_TRACKING_ID, -1))
            events.append((EV_KEY, BTN_TOUCH, 1 if any_active else 0))
            if any_active and primary_xy is not None:
                events.append((EV_ABS, ABS_X, max(0, min(UINPUT_MAX_COORD, primary_xy[0]))))
                events.append((EV_ABS, ABS_Y, max(0, min(UINPUT_MAX_COORD, primary_xy[1]))))
            self._write_events(self.touch_fd, events)


class TouchpadMimicEngine:
    """Reads touchpad evdev events, grabs device when active, and maps 1:1 to target region."""

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

        # Screen and Target Window Geometry (in pixels)
        self.screen_width: int = 1920
        self.screen_height: int = 1080
        self.target_rect: Tuple[int, int, int, int] = (0, 0, 1920, 1080)  # x, y, w, h
        self.top_inset_px: int = 0
        self.margin_pct: float = 0.03  # 3% edge deadzone on touchpad for comfortable corner reach

        self.virtual_out = VirtualOutputDevices()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tp_fd: Optional[int] = None
        self._kb_fds: List[int] = []
        self._grabbed: bool = False
        self._pending_grab: bool = False

        self.axis_x = AxisRange(0, 0, 1200)
        self.axis_y = AxisRange(0, 0, 800)
        self.device_name: str = "Not connected"
        self.device_path: str = ""

        # Live touch tracking state
        self._current_slot: int = 0
        self._slots: Dict[int, TouchSlotState] = {
            i: TouchSlotState() for i in range(MAX_MT_SLOTS)
        }
        self._dirty_slots: Dict[int, bool] = {}
        self._btn_touch: bool = False
        self._was_touching: bool = False
        self._active_fingers: int = 0
        self._norm_x: float = 0.5
        self._norm_y: float = 0.5

    def query_axis_range(self, fd: int, axis: int) -> Optional[AxisRange]:
        try:
            buf = fcntl.ioctl(fd, EVIOCGABS(axis), bytes(24))
            val, minimum, maximum, fuzz, flat, res = ABSINFO_STRUCT.unpack(buf)
            if maximum > minimum:
                return AxisRange(val, minimum, maximum, fuzz, flat, res)
        except OSError:
            pass
        return None

    def start(self, preferred_device_path: Optional[str] = None) -> Tuple[bool, str]:
        """Open touchpad, keyboard hotkeys, and virtual uinput devices, and start event loop."""
        self.stop()
        self._stop_event.clear()

        touchpads, keyboards = discover_input_devices()
        if not touchpads:
            msg = "No touchpad detected on this system."
            if self.on_error:
                self.on_error(msg)
            return False, msg

        chosen = touchpads[0]
        if preferred_device_path:
            for tp in touchpads:
                if tp.path == preferred_device_path:
                    chosen = tp
                    break

        try:
            tp_fd = os.open(chosen.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            msg = f"Cannot open touchpad {chosen.path}: {exc}. Click 'Grant Permissions' first."
            if self.on_error:
                self.on_error(msg)
            return False, msg

        try:
            self.virtual_out.open()
        except OSError as exc:
            os.close(tp_fd)
            msg = f"Cannot open /dev/uinput: {exc}. Click 'Grant Permissions' first."
            if self.on_error:
                self.on_error(msg)
            return False, msg

        # Read axis ranges (prefer MT_POSITION, fallback to ABS_X/Y)
        ax_x = self.query_axis_range(tp_fd, ABS_MT_POSITION_X) or self.query_axis_range(tp_fd, ABS_X)
        ax_y = self.query_axis_range(tp_fd, ABS_MT_POSITION_Y) or self.query_axis_range(tp_fd, ABS_Y)
        if ax_x:
            self.axis_x = ax_x
        if ax_y:
            self.axis_y = ax_y

        self._tp_fd = tp_fd
        self.device_name = chosen.name
        self.device_path = chosen.path

        # Open keyboards in non-blocking read-only mode (not grabbed) for global hotkey support on Wayland
        self._kb_fds = []
        for kb in keyboards:
            try:
                kfd = os.open(kb.path, os.O_RDONLY | os.O_NONBLOCK)
                self._kb_fds.append(kfd)
            except OSError:
                pass

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TouchMimicLoop")
        self._thread.start()
        return True, f"Connected to {chosen.name} ({chosen.path})"

    def stop(self) -> None:
        self.set_active(False)
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

        if self._tp_fd is not None:
            self._set_grab(False)
            try:
                os.close(self._tp_fd)
            except OSError:
                pass
            self._tp_fd = None

        for kfd in self._kb_fds:
            try:
                os.close(kfd)
            except OSError:
                pass
        self._kb_fds.clear()
        self.virtual_out.close()

    def _set_grab(self, grab: bool) -> None:
        if self._tp_fd is None or self._grabbed == grab:
            return
        try:
            fcntl.ioctl(self._tp_fd, EVIOCGRAB, 1 if grab else 0)
            self._grabbed = grab
        except OSError:
            pass

    def set_active(self, active: bool) -> None:
        if self.active == active:
            return
        self.active = active
        if not active:
            # Release any active virtual touch/click immediately
            if self._was_touching:
                if self.emulation_mode == "pointer_drag":
                    self.virtual_out.emit_pointer_touch(0, 0, False, True)
                else:
                    self.virtual_out.emit_native_touch_frame(
                        {i: (False, -1, 0, 0) for i in range(MAX_MT_SLOTS)},
                        None,
                        False,
                    )
                self._was_touching = False
            self._pending_grab = False
            self._set_grab(False)
        else:
            # If user's finger is currently down on the touchpad (e.g. they clicked a UI button),
            # wait until finger lifts before grabbing so KWin doesn't get a stuck button state.
            if self._btn_touch:
                self._pending_grab = True
            else:
                self._pending_grab = False
                self._set_grab(True)

        if self.on_state_changed:
            self.on_state_changed(self.active)

    def toggle(self) -> bool:
        self.set_active(not self.active)
        return self.active

    def _apply_deadzone(self, norm: float) -> float:
        m = max(0.0, min(0.25, self.margin_pct))
        if m <= 0.0:
            return norm
        scaled = (norm - m) / (1.0 - 2.0 * m)
        return max(0.0, min(1.0, scaled))

    def _map_to_uinput(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        nx = self._apply_deadzone(norm_x)
        ny = self._apply_deadzone(norm_y)

        rx, ry, rw, rh = self.target_rect
        inset = min(self.top_inset_px, max(0, rh - 50))
        eff_y = ry + inset
        eff_h = max(1, rh - inset)
        eff_w = max(1, rw)

        px = rx + nx * eff_w
        py = eff_y + ny * eff_h

        sw = max(1, self.screen_width)
        sh = max(1, self.screen_height)

        u_x = int((px / sw) * UINPUT_MAX_COORD)
        u_y = int((py / sh) * UINPUT_MAX_COORD)
        return (
            max(0, min(UINPUT_MAX_COORD, u_x)),
            max(0, min(UINPUT_MAX_COORD, u_y)),
        )

    def _run_loop(self) -> None:
        ctrl_down = False
        alt_down = False
        last_toggle_time = 0.0

        while not self._stop_event.is_set():
            if self._tp_fd is None:
                break
            watch_fds = [self._tp_fd] + self._kb_fds
            try:
                rlist, _, _ = select.select(watch_fds, [], [], 0.05)
            except (OSError, ValueError):
                break

            for fd in rlist:
                try:
                    raw_data = os.read(fd, EVENT_STRUCT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    continue

                if fd == self._tp_fd:
                    self._process_touchpad_bytes(raw_data)
                else:
                    # Keyboard hotkey check
                    offset = 0
                    while offset + EVENT_STRUCT.size <= len(raw_data):
                        _, _, ev_type, ev_code, ev_val = EVENT_STRUCT.unpack_from(raw_data, offset)
                        offset += EVENT_STRUCT.size
                        if ev_type != EV_KEY:
                            continue
                        if ev_code in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
                            ctrl_down = ev_val > 0
                        elif ev_code in (KEY_LEFTALT, KEY_RIGHTALT):
                            alt_down = ev_val > 0
                        elif ev_val == 1:  # Key press
                            now = time.monotonic()
                            if now - last_toggle_time > 0.35:
                                if self.hotkey_f8_toggle and ev_code == KEY_F8:
                                    last_toggle_time = now
                                    self.toggle()
                                elif self.ctrl_alt_t_toggle and ctrl_down and alt_down and ev_code == KEY_T:
                                    last_toggle_time = now
                                    self.toggle()

    def _process_touchpad_bytes(self, raw_data: bytes) -> None:
        offset = 0
        while offset + EVENT_STRUCT.size <= len(raw_data):
            _, _, ev_type, ev_code, ev_val = EVENT_STRUCT.unpack_from(raw_data, offset)
            offset += EVENT_STRUCT.size

            if ev_type == EV_ABS:
                if ev_code == ABS_MT_SLOT:
                    self._current_slot = max(0, min(MAX_MT_SLOTS - 1, ev_val))
                elif ev_code == ABS_MT_TRACKING_ID:
                    slot = self._slots[self._current_slot]
                    slot.tracking_id = ev_val
                    slot.active = ev_val >= 0
                    self._dirty_slots[self._current_slot] = True
                elif ev_code == ABS_MT_POSITION_X:
                    slot = self._slots[self._current_slot]
                    slot.raw_x = ev_val
                    slot.norm_x = self.axis_x.normalize(ev_val)
                    self._dirty_slots[self._current_slot] = True
                    if self._current_slot == 0:
                        self._norm_x = slot.norm_x
                elif ev_code == ABS_MT_POSITION_Y:
                    slot = self._slots[self._current_slot]
                    slot.raw_y = ev_val
                    slot.norm_y = self.axis_y.normalize(ev_val)
                    self._dirty_slots[self._current_slot] = True
                    if self._current_slot == 0:
                        self._norm_y = slot.norm_y
                elif ev_code == ABS_X:
                    self._norm_x = self.axis_x.normalize(ev_val)
                elif ev_code == ABS_Y:
                    self._norm_y = self.axis_y.normalize(ev_val)

            elif ev_type == EV_KEY:
                if ev_code == BTN_TOUCH:
                    self._btn_touch = ev_val > 0
                    if not self._btn_touch and self._pending_grab and self.active:
                        self._pending_grab = False
                        self._set_grab(True)
                elif ev_code == BTN_TOOL_TRIPLETAP and ev_val == 1 and self.triple_tap_toggle:
                    # 3-finger tap on touchpad toggles Touch Mode!
                    self.toggle()
                    return
                elif ev_code == BTN_TOOL_FINGER and ev_val == 1:
                    self._active_fingers = max(1, self._active_fingers)
                elif ev_code == BTN_TOOL_DOUBLETAP and ev_val == 1:
                    self._active_fingers = 2

            elif ev_type == EV_SYN and ev_code == SYN_REPORT:
                self._handle_syn_report()

    def _handle_syn_report(self) -> None:
        active_slot_count = sum(1 for s in self._slots.values() if s.active)
        is_touching = self._btn_touch or active_slot_count > 0

        if self.on_touch_update:
            self.on_touch_update(
                self._norm_x,
                self._norm_y,
                is_touching,
                max(1 if is_touching else 0, active_slot_count),
            )

        if not self.active or self._pending_grab:
            self._was_touching = False
            self._dirty_slots.clear()
            return

        if self.emulation_mode == "pointer_drag":
            u_x, u_y = self._map_to_uinput(self._norm_x, self._norm_y)
            self.virtual_out.emit_pointer_touch(u_x, u_y, is_touching, self._was_touching)
        else:
            # Native multi-touch touchscreen protocol B
            slot_payload: Dict[int, Tuple[bool, int, int, int]] = {}
            for idx in list(self._dirty_slots.keys()):
                st = self._slots[idx]
                ux, uy = self._map_to_uinput(st.norm_x, st.norm_y)
                slot_payload[idx] = (st.active, st.tracking_id, ux, uy)
            self._dirty_slots.clear()
            primary_xy = self._map_to_uinput(self._norm_x, self._norm_y)
            self.virtual_out.emit_native_touch_frame(slot_payload, primary_xy, is_touching)

        self._was_touching = is_touching

