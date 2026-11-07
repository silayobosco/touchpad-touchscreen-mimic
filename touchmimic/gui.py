"""PyQt6 Control Panel, Live Touchpad Visualizer, Floating HUD Pill, and System Tray."""

from __future__ import annotations

import subprocess
from typing import Optional

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .platform_backend import (
    check_platform_permissions,
    create_platform_engine,
    get_os_name,
    grant_platform_permissions,
)
from .window_tracker import WindowGeometry, WindowTracker


def make_status_icon(active: bool) -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QColor("#10b981") if active else QColor("#475569")
    p.setBrush(bg)
    p.setPen(QPen(QColor("#ffffff"), 3))
    p.drawRoundedRect(6, 12, 52, 40, 10, 10)
    # Inner touch ring
    p.setBrush(QColor("#ffffff") if active else QColor("#94a3b8"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPoint(32, 32), 8, 8)
    p.end()
    return QIcon(pix)


class TouchRadarWidget(QWidget):
    """Live 2D visual preview of finger position on the touchpad and active mapping bounds."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(280, 155)
        self.norm_x: float = 0.5
        self.norm_y: float = 0.5
        self.touching: bool = False
        self.fingers: int = 0
        self.mimic_active: bool = False
        self.margin_pct: float = 0.03
        self.target_label: str = "Active Window"

    def update_state(
        self,
        norm_x: float,
        norm_y: float,
        touching: bool,
        fingers: int,
        mimic_active: bool,
        margin_pct: float,
    ) -> None:
        self.norm_x = norm_x
        self.norm_y = norm_y
        self.touching = touching
        self.fingers = fingers
        self.mimic_active = mimic_active
        self.margin_pct = margin_pct
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        pad_rect = QRectF(8.0, 8.0, w - 16.0, h - 16.0)

        # Pad background
        p.setBrush(QColor("#0f172a"))
        border_col = QColor("#10b981") if self.mimic_active else QColor("#334155")
        p.setPen(QPen(border_col, 2))
        p.drawRoundedRect(pad_rect, 12.0, 12.0)

        # Active mapped inner area (accounting for deadzone margin)
        mx = pad_rect.width() * self.margin_pct
        my = pad_rect.height() * self.margin_pct
        inner_rect = QRectF(
            pad_rect.left() + mx,
            pad_rect.top() + my,
            pad_rect.width() - 2 * mx,
            pad_rect.height() - 2 * my,
        )
        p.setBrush(QColor(16, 185, 129, 18) if self.mimic_active else QColor(51, 65, 85, 25))
        pen_inner = QPen(QColor("#10b981" if self.mimic_active else "#475569"), 1, Qt.PenStyle.DashLine)
        p.setPen(pen_inner)
        p.drawRoundedRect(inner_rect, 8.0, 8.0)

        # Grid crosshairs
        p.setPen(QPen(QColor(148, 163, 184, 35), 1))
        p.drawLine(
            int(inner_rect.center().x()),
            int(inner_rect.top()),
            int(inner_rect.center().x()),
            int(inner_rect.bottom()),
        )
        p.drawLine(
            int(inner_rect.left()),
            int(inner_rect.center().y()),
            int(inner_rect.right()),
            int(inner_rect.center().y()),
        )

        # Finger contact position
        fx = pad_rect.left() + self.norm_x * pad_rect.width()
        fy = pad_rect.top() + self.norm_y * pad_rect.height()

        if self.touching:
            glow_color = QColor(16, 185, 129, 90) if self.mimic_active else QColor(56, 189, 248, 90)
            dot_color = QColor("#10b981") if self.mimic_active else QColor("#38bdf8")
            p.setBrush(glow_color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(fx - 18, fy - 18, 36, 36))
            p.setBrush(dot_color)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawEllipse(QRectF(fx - 8, fy - 8, 16, 16))
        else:
            p.setBrush(QColor("#64748b"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(fx - 5, fy - 5, 10, 10))

        # Overlay label
        p.setPen(QColor("#94a3b8"))
        font = p.font()
        font.setPointSize(9)
        p.setFont(font)
        state_txt = (
            f"TOUCHING ({self.fingers}F)  [{int(self.norm_x*100)}%, {int(self.norm_y*100)}%]"
            if self.touching
            else f"IDLE  [{int(self.norm_x*100)}%, {int(self.norm_y*100)}%]"
        )
        p.drawText(
            pad_rect.adjusted(10, 6, -10, -6),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
            state_txt,
        )
        p.end()


class FloatingHudPill(QWidget):
    """Compact always-on-top status pill for quick visual feedback and toggling."""

    toggleRequested = pyqtSignal()
    pinRequested = pyqtSignal()
    openPanelRequested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool,
        )
        self.setObjectName("TouchMimicHud")
        self.setWindowTitle("TouchMimic HUD")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_pos: Optional[QPoint] = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        self.container = QFrame(self)
        self.container.setObjectName("HudContainer")
        layout = QHBoxLayout(self.container)
        layout.setContentsMargins(12, 6, 10, 6)
        layout.setSpacing(8)

        self.dot_label = QLabel("●")
        self.status_label = QLabel("TOUCH: OFF")
        font = QFont()
        font.setBold(True)
        font.setPointSize(9)
        self.status_label.setFont(font)

        self.target_label = QLabel("Active Window")
        self.target_label.setStyleSheet("color: #cbd5e1; font-size: 11px;")

        self.toggle_btn = QPushButton("Toggle (F8)")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggleRequested.emit)

        self.pin_btn = QPushButton("Pin")
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setToolTip("Lock/Pin current target window")
        self.pin_btn.clicked.connect(self.pinRequested.emit)

        self.panel_btn = QPushButton("⚙")
        self.panel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.panel_btn.setToolTip("Open TouchMimic Control Panel")
        self.panel_btn.setFixedWidth(28)
        self.panel_btn.clicked.connect(self.openPanelRequested.emit)

        layout.addWidget(self.dot_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.target_label)
        layout.addWidget(self.toggle_btn)
        layout.addWidget(self.pin_btn)
        layout.addWidget(self.panel_btn)
        outer.addWidget(self.container)

        self.update_appearance(False, "Active Window", False)

        # Place near top-right of primary screen
        screen = QGuiApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move(sg.right() - 430, sg.top() + 12)

    def update_appearance(self, active: bool, target_name: str, pinned: bool) -> None:
        short_target = target_name if len(target_name) <= 22 else target_name[:19] + "..."
        prefix = "📌 " if pinned else "🎯 "
        self.target_label.setText(f"{prefix}{short_target}")
        self.pin_btn.setText("Unpin" if pinned else "Pin")

        if active:
            self.status_label.setText("TOUCH: ON")
            self.status_label.setStyleSheet("color: #ecfdf5; font-weight: bold;")
            self.dot_label.setStyleSheet("color: #34d399; font-size: 14px;")
            self.container.setStyleSheet(
                """
                QFrame#HudContainer {
                    background-color: rgba(6, 78, 59, 235);
                    border: 2px solid #10b981;
                    border-radius: 16px;
                }
                QPushButton {
                    background-color: #059669;
                    color: white;
                    border: 1px solid #34d399;
                    border-radius: 8px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #10b981;
                }
                """
            )
        else:
            self.status_label.setText("TOUCH: OFF")
            self.status_label.setStyleSheet("color: #f1f5f9; font-weight: bold;")
            self.dot_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
            self.container.setStyleSheet(
                """
                QFrame#HudContainer {
                    background-color: rgba(15, 23, 42, 230);
                    border: 1px solid #475569;
                    border-radius: 16px;
                }
                QPushButton {
                    background-color: #1e293b;
                    color: #e2e8f0;
                    border: 1px solid #475569;
                    border-radius: 8px;
                    padding: 3px 8px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #334155;
                }
                """
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None


class TouchMimicController(QMainWindow):
    """Main application window and coordinator for TouchpadMimicEngine and WindowTracker."""

    engineStateSignal = pyqtSignal(bool)
    engineTouchSignal = pyqtSignal(float, float, bool, int)
    engineErrorSignal = pyqtSignal(str)

    def __init__(self, start_enabled: bool = False, minimize_to_hud: bool = False) -> None:
        super().__init__()
        self.setObjectName("TouchMimicMainWindow")
        self.setWindowTitle("TouchMimic — Touchpad to Touchscreen")
        self.setMinimumSize(620, 650)

        screen = QGuiApplication.primaryScreen()
        sg = screen.geometry() if screen else QRectF(0, 0, 1920, 1080).toRect()
        self.screen_w = sg.width()
        self.screen_h = sg.height()

        self.engine = create_platform_engine(
            on_state_changed=lambda act: self.engineStateSignal.emit(act),
            on_touch_update=lambda nx, ny, t, f: self.engineTouchSignal.emit(nx, ny, t, f),
            on_error=lambda err: self.engineErrorSignal.emit(err),
        )
        self.engine.screen_width = self.screen_w
        self.engine.screen_height = self.screen_h
        self.engine.top_inset_px = 32  # Default skip window titlebar for active windows

        self.tracker = WindowTracker(
            screen_width=self.screen_w,
            screen_height=self.screen_h,
            status_provider=self.get_status_dict,
            parent=self,
        )

        self.hud = FloatingHudPill()
        self.hud.toggleRequested.connect(self.engine.toggle)
        self.hud.pinRequested.connect(self._on_toggle_pin)
        self.hud.openPanelRequested.connect(self._show_and_raise)

        if hasattr(self.engine, "safe_hwnds"):
            try:
                self.engine.safe_hwnds.add(int(self.winId()))
                self.engine.safe_hwnds.add(int(self.hud.winId()))
            except Exception:
                pass

        self._build_ui()
        self._setup_tray()

        # Connect signals
        self.engineStateSignal.connect(self._on_engine_state_changed)
        self.engineTouchSignal.connect(self._on_engine_touch_update)
        self.engineErrorSignal.connect(self._on_engine_error)

        self.tracker.targetChanged.connect(self._on_target_window_changed)
        self.tracker.bridge.commandReceived.connect(self._on_dbus_command)

        # Start services
        self.tracker.start()
        self._try_start_engine()

        # Standby 30 FPS radar tracking timer (renders live touchpad/mouse cursor when standby)
        self.standby_radar_timer = QTimer(self)
        self.standby_radar_timer.setInterval(33)
        self.standby_radar_timer.timeout.connect(self._update_live_radar)
        self.standby_radar_timer.start()

        self.hud.show()
        if start_enabled:
            QTimer.singleShot(250, lambda: self.engine.set_active(True))
        if minimize_to_hud:
            self.hide()
        else:
            self.show()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#CentralRoot {
                background-color: #0b1120;
                color: #f1f5f9;
                font-family: 'Inter', 'Noto Sans', sans-serif;
            }
            QGroupBox {
                background-color: #111827;
                border: 1px solid #1e293b;
                border-radius: 12px;
                margin-top: 12px;
                padding: 14px;
                font-weight: bold;
                color: #94a3b8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
            }
            QComboBox, QSpinBox {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 5px 10px;
            }
            QCheckBox {
                color: #cbd5e1;
                spacing: 8px;
            }
            """
        )

        central = QWidget(self)
        central.setObjectName("CentralRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        # 1. Permission Warning Banner (hidden when permissions are OK)
        self.perm_frame = QFrame()
        self.perm_frame.setStyleSheet(
            """
            QFrame {
                background-color: #451a03;
                border: 1px solid #f59e0b;
                border-radius: 10px;
                padding: 8px;
            }
            """
        )
        perm_layout = QHBoxLayout(self.perm_frame)
        self.perm_label = QLabel(
            "⚠️ Input device access is required to read your touchpad and create the virtual touchscreen."
        )
        self.perm_label.setWordWrap(True)
        self.perm_label.setStyleSheet("color: #fef3c7; border: none;")
        self.grant_btn = QPushButton("🔑 Grant Input Permissions")
        self.grant_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.grant_btn.setStyleSheet(
            "background-color: #d97706; color: white; border: none; padding: 8px 14px; font-weight: bold;"
        )
        self.grant_btn.clicked.connect(self._on_grant_permissions)
        perm_layout.addWidget(self.perm_label, 1)
        perm_layout.addWidget(self.grant_btn)
        root.addWidget(self.perm_frame)

        # 2. Hero Status & Toggle Card
        self.hero_frame = QFrame()
        hero_layout = QHBoxLayout(self.hero_frame)
        hero_layout.setContentsMargins(16, 14, 16, 14)

        hero_text_col = QVBoxLayout()
        self.hero_title = QLabel("TOUCHSCREEN MIMIC: STANDBY")
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        self.hero_title.setFont(title_font)

        self.hero_sub = QLabel(
            "Triggers:  3-Finger Tap on Touchpad   •   F8 Key   •   Ctrl+Alt+T   •   `touchmimic toggle`"
        )
        self.hero_sub.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.device_label = QLabel("Touchpad: Detecting...")
        self.device_label.setStyleSheet("color: #64748b; font-size: 11px;")

        hero_text_col.addWidget(self.hero_title)
        hero_text_col.addWidget(self.hero_sub)
        hero_text_col.addWidget(self.device_label)

        self.main_toggle_btn = QPushButton("ENABLE TOUCH MODE")
        self.main_toggle_btn.setMinimumHeight(46)
        self.main_toggle_btn.setMinimumWidth(190)
        self.main_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main_toggle_btn.clicked.connect(self.engine.toggle)

        hero_layout.addLayout(hero_text_col, 1)
        hero_layout.addWidget(self.main_toggle_btn)
        root.addWidget(self.hero_frame)

        # 3. Live Touchpad Radar Preview
        radar_group = QGroupBox("Live 1:1 Touchpad Surface Preview")
        radar_layout = QVBoxLayout(radar_group)
        self.radar = TouchRadarWidget()
        radar_layout.addWidget(self.radar)
        root.addWidget(radar_group, 1)

        # 4. Target Area & Window Mapping Group
        target_group = QGroupBox("Target Window & Mapping Area")
        t_grid = QGridLayout(target_group)
        t_grid.setSpacing(10)

        t_grid.addWidget(QLabel("Map Touchpad To:"), 0, 0)
        self.target_mode_combo = QComboBox()
        self.target_mode_combo.addItem("🎯 Active Focused Window (Auto-Follow)", "active_window")
        self.target_mode_combo.addItem("📌 Pinned / Locked Window (TikTok / Emulator)", "pinned_window")
        self.target_mode_combo.addItem("🖥️ Entire Screen (1920×1080)", "fullscreen")
        self.target_mode_combo.currentIndexChanged.connect(self._on_target_mode_combo_changed)
        t_grid.addWidget(self.target_mode_combo, 0, 1, 1, 2)

        t_grid.addWidget(QLabel("Current Target:"), 1, 0)
        self.current_target_lbl = QLabel("Detecting active window...")
        self.current_target_lbl.setStyleSheet("color: #38bdf8; font-weight: 600;")
        t_grid.addWidget(self.current_target_lbl, 1, 1)

        btn_row = QHBoxLayout()
        self.pin_window_btn = QPushButton("📌 Pin Active Window")
        self.pin_window_btn.clicked.connect(self._on_toggle_pin)
        self.pick_window_btn = QPushButton("🎯 Crosshair Pick Window")
        self.pick_window_btn.setToolTip("Click to select any visible window (e.g. Android Emulator or TikTok) with a crosshair")
        self.pick_window_btn.clicked.connect(self._on_pick_window)
        btn_row.addWidget(self.pin_window_btn)
        btn_row.addWidget(self.pick_window_btn)
        t_grid.addLayout(btn_row, 1, 2)

        t_grid.addWidget(QLabel("Skip Window Titlebar (px):"), 2, 0)
        self.inset_spin = QSpinBox()
        self.inset_spin.setRange(0, 150)
        self.inset_spin.setValue(32)
        self.inset_spin.setToolTip(
            "Skips the top N pixels of the target window so touching the top edge of the touchpad won't click the window close/minimize bar."
        )
        self.inset_spin.valueChanged.connect(self._on_inset_changed)
        t_grid.addWidget(self.inset_spin, 2, 1)

        t_grid.addWidget(QLabel("Touchpad Edge Margin:"), 2, 2)
        margin_row = QHBoxLayout()
        self.margin_slider = QSlider(Qt.Orientation.Horizontal)
        self.margin_slider.setRange(0, 15)
        self.margin_slider.setValue(3)
        self.margin_lbl = QLabel("3%")
        self.margin_slider.valueChanged.connect(self._on_margin_changed)
        margin_row.addWidget(self.margin_slider)
        margin_row.addWidget(self.margin_lbl)
        t_grid.addLayout(margin_row, 2, 3)

        root.addWidget(target_group)

        # 5. Emulation Mode & Triggers Group
        mode_group = QGroupBox("Touch Emulation Engine & Triggers")
        m_grid = QGridLayout(mode_group)
        m_grid.setSpacing(10)

        m_grid.addWidget(QLabel("Touch Behavior:"), 0, 0)
        self.emul_combo = QComboBox()
        self.emul_combo.addItem(
            "👆 Universal 1:1 Touch-Drag Click (Best for TikTok, Browsers & Emulators)",
            "pointer_drag",
        )
        self.emul_combo.addItem(
            "📱 Native Kernel Multi-Touch Screen (Multi-finger / Pinch-to-Zoom)",
            "native_touch",
        )
        self.emul_combo.currentIndexChanged.connect(self._on_emul_mode_changed)
        self.emul_combo.setCurrentIndex(0)
        m_grid.addWidget(self.emul_combo, 0, 1, 1, 3)

        self.cb_triple_tap = QCheckBox("3-Finger Tap on Touchpad toggles mode")
        self.cb_triple_tap.setChecked(True)
        self.cb_triple_tap.toggled.connect(self._on_triggers_changed)

        self.cb_f8 = QCheckBox("F8 global key toggles mode")
        self.cb_f8.setChecked(True)
        self.cb_f8.toggled.connect(self._on_triggers_changed)

        self.cb_ctrl_alt_t = QCheckBox("Ctrl+Alt+T global shortcut")
        self.cb_ctrl_alt_t.setChecked(True)
        self.cb_ctrl_alt_t.toggled.connect(self._on_triggers_changed)

        self.cb_show_hud = QCheckBox("Show Floating Always-On-Top HUD Pill")
        self.cb_show_hud.setChecked(True)
        self.cb_show_hud.toggled.connect(lambda checked: self.hud.setVisible(checked))

        m_grid.addWidget(self.cb_triple_tap, 1, 0, 1, 2)
        m_grid.addWidget(self.cb_f8, 1, 2, 1, 2)
        m_grid.addWidget(self.cb_ctrl_alt_t, 2, 0, 1, 2)
        m_grid.addWidget(self.cb_show_hud, 2, 2, 1, 2)

        root.addWidget(mode_group)

        self._update_hero_style(False)

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_status_icon(False), self)
        self.tray.setToolTip("TouchMimic — Touchpad to Touchscreen")
        menu = self.tray.contextMenu() or QWidget()
        from PyQt6.QtWidgets import QMenu

        tray_menu = QMenu()
        self.act_toggle = QAction("Toggle Touch Mode (F8)", self)
        self.act_toggle.triggered.connect(self.engine.toggle)
        tray_menu.addAction(self.act_toggle)

        act_pin = QAction("Pin / Unpin Current Window", self)
        act_pin.triggered.connect(self._on_toggle_pin)
        tray_menu.addAction(act_pin)

        act_show = QAction("Open Control Panel", self)
        act_show.triggered.connect(self._show_and_raise)
        tray_menu.addAction(act_show)

        tray_menu.addSeparator()
        act_quit = QAction("Quit TouchMimic", self)
        act_quit.triggered.connect(self.quit_app)
        tray_menu.addAction(act_quit)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _try_start_engine(self) -> None:
        ok, msg = check_platform_permissions()
        if not ok:
            self.perm_frame.setVisible(True)
            self.perm_label.setText(f"⚠️ {msg}")
            self.device_label.setText(f"Status: {msg}")
            if get_os_name() == "windows":
                self.engine.start()
                self._sync_target_to_engine(self.tracker.current_target())
            return
        self.perm_frame.setVisible(False)
        started, status_msg = self.engine.start()
        self.device_label.setText(status_msg)
        self.perm_frame.setVisible(not started)
        self._sync_target_to_engine(self.tracker.current_target())

    def _on_grant_permissions(self) -> None:
        self.grant_btn.setEnabled(False)
        self.grant_btn.setText("Waiting for authentication...")
        QApplication.processEvents()
        ok, msg = grant_platform_permissions()
        self.grant_btn.setEnabled(True)
        self.grant_btn.setText("🔑 Grant Input Permissions")
        if ok:
            self._try_start_engine()
        else:
            self.perm_label.setText(f"⚠️ {msg}")

    def _update_hero_style(self, active: bool) -> None:
        self.setWindowIcon(make_status_icon(active))
        if hasattr(self, "tray"):
            self.tray.setIcon(make_status_icon(active))
        if active:
            self.hero_title.setText("🟢 TOUCHSCREEN MIMIC: ACTIVE")
            self.hero_title.setStyleSheet("color: #34d399;")
            self.main_toggle_btn.setText("DISABLE TOUCH MODE (F8)")
            self.main_toggle_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #059669;
                    color: white;
                    border: 2px solid #34d399;
                    border-radius: 10px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #10b981;
                }
                """
            )
            self.hero_frame.setStyleSheet(
                """
                QFrame {
                    background-color: #064e3b;
                    border: 2px solid #10b981;
                    border-radius: 14px;
                }
                """
            )
        else:
            self.hero_title.setText("⚪ TOUCHSCREEN MIMIC: STANDBY")
            self.hero_title.setStyleSheet("color: #f8fafc;")
            self.main_toggle_btn.setText("ENABLE TOUCH MODE (F8)")
            self.main_toggle_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #2563eb;
                    color: white;
                    border: 1px solid #60a5fa;
                    border-radius: 10px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #3b82f6;
                }
                """
            )
            self.hero_frame.setStyleSheet(
                """
                QFrame {
                    background-color: #111827;
                    border: 1px solid #334155;
                    border-radius: 14px;
                }
                """
            )

    def _on_engine_state_changed(self, active: bool) -> None:
        self._update_hero_style(active)
        target = self.tracker.current_target()
        pinned = self.tracker.target_mode == "pinned_window"
        self.hud.update_appearance(active, target.caption, pinned)
        self.radar.update_state(
            self.radar.norm_x,
            self.radar.norm_y,
            self.radar.touching,
            self.radar.fingers,
            active,
            self.engine.margin_pct,
        )
        state_str = "ENABLED" if active else "DISABLED (Normal Touchpad)"
        if get_os_name() == "linux":
            try:
                subprocess.Popen(
                    [
                        "notify-send",
                        "-a",
                        "TouchMimic",
                        "-t",
                        "1500",
                        f"TouchMimic: {state_str}",
                        f"Target: {target.summary()}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass
        if hasattr(self, "tray") and self.tray.isVisible():
            self.tray.showMessage(
                f"TouchMimic: {state_str}",
                f"Target: {target.summary()}",
                QSystemTrayIcon.MessageIcon.Information,
                1500,
            )

    def _update_live_radar(self) -> None:
        if self.engine.active:
            return  # Radar is driven directly by engine touch events when active

        pos = QCursor.pos()
        gx, gy = pos.x(), pos.y()
        rx, ry, rw, rh = self.engine.target_rect
        if rw <= 0 or rh <= 0:
            return

        norm_x = max(0.0, min(1.0, (gx - rx) / float(rw)))
        norm_y = max(0.0, min(1.0, (gy - ry) / float(rh)))
        touching = QApplication.mouseButtons() != Qt.MouseButton.NoButton

        self.radar.update_state(
            norm_x,
            norm_y,
            touching,
            1 if touching else 0,
            False,
            self.engine.margin_pct,
        )

    def _on_engine_touch_update(
        self, norm_x: float, norm_y: float, touching: bool, fingers: int
    ) -> None:
        self.radar.update_state(
            norm_x, norm_y, touching, fingers, self.engine.active, self.engine.margin_pct
        )

    def _on_engine_error(self, err: str) -> None:
        self.device_label.setText(f"Error: {err}")
        self.perm_frame.setVisible(True)

    def _sync_target_to_engine(self, geom: WindowGeometry) -> None:
        self.engine.target_rect = geom.rect
        if self.tracker.target_mode == "fullscreen":
            self.engine.top_inset_px = 0
        else:
            self.engine.top_inset_px = self.inset_spin.value()
        self.current_target_lbl.setText(geom.summary())
        pinned = self.tracker.target_mode == "pinned_window"
        self.pin_window_btn.setText("🔓 Unpin Window" if pinned else "📌 Pin Active Window")
        self.hud.update_appearance(self.engine.active, geom.caption, pinned)

    def _on_target_window_changed(self, geom: WindowGeometry) -> None:
        self._sync_target_to_engine(geom)

    def _on_target_mode_combo_changed(self, idx: int) -> None:
        mode = self.target_mode_combo.itemData(idx)
        if mode:
            self.tracker.set_target_mode(mode)

    def _on_toggle_pin(self) -> None:
        pinned = self.tracker.toggle_pin_current_window()
        idx = 1 if pinned else 0
        self.target_mode_combo.blockSignals(True)
        self.target_mode_combo.setCurrentIndex(idx)
        self.target_mode_combo.blockSignals(False)

    def _on_pick_window(self) -> None:
        geom = self.tracker.pick_window_interactively()
        if geom is not None:
            self.target_mode_combo.blockSignals(True)
            self.target_mode_combo.setCurrentIndex(1)
            self.target_mode_combo.blockSignals(False)
            self._sync_target_to_engine(geom)

    def _on_inset_changed(self, val: int) -> None:
        if self.tracker.target_mode != "fullscreen":
            self.engine.top_inset_px = val

    def _on_margin_changed(self, val: int) -> None:
        self.margin_lbl.setText(f"{val}%")
        self.engine.margin_pct = val / 100.0
        self.radar.margin_pct = self.engine.margin_pct
        self.radar.update()

    def _on_emul_mode_changed(self, idx: int) -> None:
        mode = self.emul_combo.itemData(idx)
        if mode:
            self.engine.emulation_mode = mode

    def _on_triggers_changed(self) -> None:
        self.engine.triple_tap_toggle = self.cb_triple_tap.isChecked()
        self.engine.hotkey_f8_toggle = self.cb_f8.isChecked()
        self.engine.ctrl_alt_t_toggle = self.cb_ctrl_alt_t.isChecked()

    def _on_dbus_command(self, cmd: str) -> None:
        if cmd == "toggle":
            self.engine.toggle()
        elif cmd == "on":
            self.engine.set_active(True)
        elif cmd == "off":
            self.engine.set_active(False)
        elif cmd == "pin":
            self._on_toggle_pin()
        elif cmd == "show":
            self._show_and_raise()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._show_and_raise()

    def _show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def get_status_dict(self) -> dict:
        target = self.tracker.current_target()
        return {
            "active": self.engine.active,
            "emulation_mode": self.engine.emulation_mode,
            "target_mode": self.tracker.target_mode,
            "target_window": {
                "caption": target.caption,
                "resource_class": target.resource_class,
                "x": target.x,
                "y": target.y,
                "width": target.width,
                "height": target.height,
            },
            "device_name": self.engine.device_name,
            "device_path": self.engine.device_path,
        }

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing the main window minimizes to HUD & System Tray unless quitting
        if self.hud.isVisible() or self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            self.quit_app()

    def quit_app(self) -> None:
        self.engine.stop()
        self.tracker.stop()
        self.hud.close()
        self.tray.hide()
        QApplication.quit()

