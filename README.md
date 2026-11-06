# TouchMimic — Turn Your Touchpad into a 1:1 Touchscreen on Command (Linux, Windows & macOS)

**TouchMimic** transforms your laptop touchpad into an absolute 1:1 touchscreen mapped directly to your **currently focused window** (such as an Android Emulator or TikTok), a **pinned window**, or your **entire screen**.

Works natively across **Linux (Wayland/KWin & X11)**, **Windows 10/11**, and **macOS** using pure-Python OS bindings (`ctypes`) and `PyQt6`.

---

## ✨ Key Features

- **1:1 Absolute Touch Mapping**: Touching any point on your touchpad immediately touches and drags at the corresponding proportional point inside your active window (or screen), and lifting your finger immediately releases.
- **Cross-Platform Native Engines**:
  - **Linux (`touchmimic/linux_input.py`)**: Reads raw touchpad multi-touch slots via `evdev` (`/dev/input/event*`), grabs relative motion (`EVIOCGRAB`), and emits virtual hardware touches via `/dev/uinput` (`TouchMimic Absolute Pointer` & `TouchMimic Virtual Touchscreen`).
  - **Windows 10/11 (`touchmimic/windows_input.py`)**: Uses Win32 low-level input hooks (`WH_MOUSE_LL`, `WH_KEYBOARD_LL`), `SendInput` absolute virtual-desktop mapping, and Windows Native Touch Injection (`InitializeTouchInjection` / `InjectTouchInput` in `user32.dll`).
  - **macOS (`touchmimic/macos_input.py`)**: Reads raw normalized `[0.0 .. 1.0]` Apple Trackpad contacts directly from `MultitouchSupport.framework` (`MTRegisterContactFrameCallback`) and emits 1:1 Quartz `CGEvent` touch-drags.
- **Smart Active Window Tracking**:
  - **Auto-Follow Active Window**: Automatically tracks the exact position and size of the focused window (via KWin DBus Scripting on KDE Wayland, `GetForegroundWindow` on Windows, and `System Events` on macOS).
  - **Pin / Lock Window**: Lock the 1:1 touch mapping onto your Android Emulator or TikTok window.
  - **Titlebar Skip**: Automatically skips the top window titlebar so touching the top edge of your touchpad never accidentally drags or closes the window.
- **Instant On-Command Triggers**:
  - **3-Finger Tap** directly on the touchpad toggles Touch Mode ON/OFF.
  - **Global Hotkeys**: Press **`F8`** or **`Ctrl + Alt + T`** from anywhere.
  - **CLI Commands**: Run `touchmimic toggle`, `touchmimic on`, `touchmimic off`, `touchmimic pin`, or `touchmimic status` on any OS.

---

## 🚀 Quick Start

### 1. Install
```bash
pip install -e .
```
*(Or run directly via `./bin/touchmimic` on Linux/macOS or `bin\touchmimic.bat` on Windows as long as `PyQt6` is installed.)*

### 2. Platform Permissions (One-Time Setup)
- **Linux**: Click **"🔑 Grant Input Permissions"** inside the GUI, or run `./setup-permissions.sh` / `touchmimic permissions` (uses `setfacl` and a udev rule so it takes effect immediately without rebooting).
- **Windows**: No special permissions or drivers required — `InjectTouchInput` and `SendInput` work out of the box.
- **macOS**: Grant **Accessibility** permission when prompted (`System Settings -> Privacy & Security -> Accessibility`), or run `touchmimic permissions`.

### 3. Usage
```bash
# Launch the Control Panel + Floating HUD
touchmimic

# CLI Controls (works across Linux, Windows, and macOS):
touchmimic toggle
touchmimic on
touchmimic off
touchmimic pin
touchmimic status
```
