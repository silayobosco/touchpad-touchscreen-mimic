#!/usr/bin/env python3
"""Cross-platform standalone binary builder for TouchMimic.

Supports building standalone executables for:
  - Windows: TouchMimic.exe (single-file windowed binary)
  - Linux:   TouchMimic (single-file ELF executable binary)
  - macOS:   TouchMimic.app (Application Bundle) & TouchMimic binary
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.realpath(__file__))
ENTRY_POINT = os.path.join(PROJECT_ROOT, "touchmimic", "__main__.py")
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")


def check_pyinstaller() -> bool:
    """Ensure pyinstaller is available in the current environment."""
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return False


def install_pyinstaller() -> bool:
    """Attempt to install PyInstaller via pip."""
    print("📦 PyInstaller not found. Attempting to install via pip...")
    res = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=False)
    return res.returncode == 0


def build_binary(
    windowed: bool = True,
    onefile: bool = True,
    clean: bool = True,
    target_name: str = "TouchMimic",
) -> int:
    system = platform.system().lower()
    print(f"\n========================================================")
    print(f"🚀 Building TouchMimic Standalone Binary for: {platform.system()} ({platform.machine()})")
    print(f"========================================================\n")

    if not check_pyinstaller():
        if not install_pyinstaller():
            print("❌ Failed to install PyInstaller. Please run: pip install pyinstaller")
            return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        target_name,
        "--noconfirm",
        "--paths",
        PROJECT_ROOT,
    ]

    if clean:
        cmd.append("--clean")

    if onefile and system != "darwin":
        # On Linux and Windows, onefile produces a convenient single executable
        cmd.append("--onefile")
    elif system == "darwin":
        # On macOS, creating a proper .app bundle is standard
        cmd.append("--onedir")
        cmd.append("--windowed")
        cmd.append(
            "--osx-bundle-identifier=com.silayobosco.touchmimic"
        )

    if windowed:
        cmd.append("--windowed")
    else:
        cmd.append("--console")

    # Hidden imports to ensure all OS dynamic backends & PyQt6 components are bundled
    hidden_imports = [
        "touchmimic",
        "touchmimic.cli",
        "touchmimic.gui",
        "touchmimic.platform_backend",
        "touchmimic.window_tracker",
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
    ]

    if system == "windows":
        hidden_imports.extend([
            "touchmimic.windows_input",
            "ctypes",
            "ctypes.wintypes",
        ])
    elif system == "linux":
        hidden_imports.extend([
            "touchmimic.linux_input",
            "fcntl",
            "select",
            "struct",
        ])
        try:
            import PyQt6.QtDBus  # noqa: F401
            hidden_imports.append("PyQt6.QtDBus")
        except ImportError:
            pass
    elif system == "darwin":
        hidden_imports.extend([
            "touchmimic.macos_input",
            "ctypes",
        ])

    for hi in hidden_imports:
        cmd.extend(["--hidden-import", hi])

    # Include additional documentation/scripts in dist
    cmd.append(ENTRY_POINT)

    print("Running command:")
    print(" ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print("\n❌ PyInstaller build failed.")
        return result.returncode

    print("\n✅ Build succeeded!")
    if system == "windows":
        exe_path = os.path.join(DIST_DIR, f"{target_name}.exe")
        print(f"📦 Executable located at: {exe_path}")
    elif system == "darwin":
        app_path = os.path.join(DIST_DIR, f"{target_name}.app")
        print(f"📦 macOS Application located at: {app_path}")
    else:
        bin_path = os.path.join(DIST_DIR, target_name)
        print(f"📦 Linux binary located at: {bin_path}")
        # Copy setup-permissions.sh into dist for convenience
        shutil.copy(os.path.join(PROJECT_ROOT, "setup-permissions.sh"), DIST_DIR)
        print(f"📄 Copied setup-permissions.sh to: {DIST_DIR}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build standalone binary of TouchMimic")
    parser.add_argument(
        "--console",
        action="store_true",
        help="Build with terminal console enabled (useful for debugging CLI commands)",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build as a directory instead of a single executable file",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not clean build cache before building",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="TouchMimic",
        help="Name of output binary (default: TouchMimic)",
    )

    args = parser.parse_args()

    return build_binary(
        windowed=not args.console,
        onefile=not args.onedir,
        clean=not args.no_clean,
        target_name=args.name,
    )


if __name__ == "__main__":
    sys.exit(main())

