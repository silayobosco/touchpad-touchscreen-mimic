# TouchMimic — Turn Your Touchpad into a 1:1 Touchscreen on Command

**TouchMimic** transforms your laptop touchpad into an absolute 1:1 touchscreen mapped directly to your **currently focused window** (like an Android Emulator or TikTok), a **pinned window**, or your **entire screen**.

Built specifically for Linux (KDE Plasma 6 Wayland & X11) with zero external Python package dependencies beyond `PyQt6`.

---

## ✨ Key Features

- **1:1 Absolute Touch Mapping**: Touching any point on your touchpad immediately touches and drags at the corresponding point inside your active window (or screen), and lifting your finger immediately releases the touch/drag.
- **Smart Active Window Tracking (Wayland & X11)**:
  - **Auto-Follow Active Window**: Automatically tracks the exact position and size of the focused window via KWin DBus Scripting.
  - **Pin / Lock Window**: Lock the 1:1 touch mapping onto your Android Emulator or TikTok window (including an interactive **Crosshair Window Picker**).
  - **Titlebar Skip**: Automatically skips the top 32px window titlebar so touching the top edge of your touchpad never accidentally drags or closes the window.
- **Instant On-Command Triggers**:
  - **3-Finger Tap** directly on the touchpad toggles Touch Mode ON/OFF at any time.
  - **Global Hotkeys**: Press **`F8`** or **`Ctrl + Alt + T`** from anywhere on Wayland.
  - **CLI Commands**: Run `touchmimic toggle`, `touchmimic on`, `touchmimic off`, or `touchmimic pin` from terminal or bind them to any custom shortcut.
- **Two Kernel-Level Emulation Modes**:
  1. **Universal 1:1 Touch-Drag Click (`pointer_drag`)**: Works in 100% of apps (TikTok Web/Desktop, Android Studio Emulator, Waydroid, Genymotion, browsers, games).
  2. **Native Kernel Multi-Touch Screen (`native_touch`)**: Emits Linux Multi-Touch Protocol B events (`BTN_TOUCH`, `ABS_MT_SLOT`) for native multi-finger gestures like pinch-to-zoom.
- **Floating HUD Pill & Live Touchpad Radar**: Always-on-top compact status pill and real-time 2D finger visualizer.

---

## 🚀 Quick Start

### 1. Grant Input Device Permissions (One-Time Setup)
TouchMimic reads raw touchpad coordinates from `/dev/input/event*` and creates a virtual touchscreen via `/dev/uinput`.
Either click the **"🔑 Grant Input Permissions"** button inside the GUI, or run:

```bash
./setup-permissions.sh
# or:
touchmimic permissions
```
*(Uses `setfacl` so it takes effect immediately without needing a reboot or logout!)*

### 2. Launch TouchMimic

```bash
# Launch the Control Panel + Floating HUD
touchmimic

# Or use CLI commands while running:
touchmimic toggle
touchmimic on
touchmimic off
touchmimic pin
touchmimic status
```

