from __future__ import annotations

from datetime import datetime
import os
import sys
from pathlib import Path
from textwrap import dedent

from PySide6.QtCore import QEvent, QLockFile, QPoint, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QCursor,
    QGuiApplication,
    QIcon,
    QContextMenuEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QPixmap,
    QColor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGraphicsDropShadowEffect,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QMenu,
    QLayout,
    QSizePolicy,
    QSystemTrayIcon,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .data import LimitWindow, UsageSnapshot, load_usage


APP_ORG = "blakkguard"
APP_ID = "codex-usage-widget"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = PROJECT_ROOT / "run.sh"
AUTOSTART_ENTRY = Path.home() / ".config" / "autostart" / f"{APP_ID}.desktop"
ICON_DIR = PROJECT_ROOT / "icons"
DARK_ICON_PATH = ICON_DIR / "codex_usage_widget_dark.svg"
LIGHT_ICON_PATH = ICON_DIR / "codex_usage_widget_light.svg"
COLOR_ICON_PATH = ICON_DIR / "codex_usage_widget_color.svg"
DISPLAY_PREFS = [
    ("Model", "show_model", "showModel"),
    ("Used percent", "show_used", "showUsed"),
    ("Free percent", "show_free", "showFree"),
    ("Reset countdown", "show_countdown", "showCountdown"),
    ("Last updated", "show_updated", "showUpdated"),
]

WIDGET_LAYOUT_MODES = [
    ("Horizontal", "horizontal"),
    ("Stacked", "stacked"),
]

THEME_MODES = [
    ("System", "system"),
    ("Dark", "dark"),
    ("Light", "light"),
]


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "never"
    seconds = (now - dt).total_seconds()
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    return f"{_duration(seconds)} ago"


def _reset_text(window: LimitWindow, now: datetime) -> tuple[str, str]:
    if not window.has_data:
        return "waiting", "no local data"
    assert window.reset_at is not None
    used = f"{window.used_percent}%"
    seconds = (window.reset_at - now).total_seconds()
    if seconds <= 0:
        return used, "reset passed"
    return used, f"{_duration(seconds)}"


def _usage_color(window: LimitWindow) -> str:
    if window.used_percent is None:
        return ""
    free = 100 - window.used_percent
    if free <= 15:
        return "#ff4d4d"
    if free <= 25:
        return "#ff9f1c"
    if free <= 40:
        return "#f5d547"
    return ""


def _fallback_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.GlobalColor.gray)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(Qt.GlobalColor.white)
    painter.drawLine(32, 32, 32, 16)
    painter.drawLine(32, 32, 46, 32)
    painter.end()
    return QIcon(pixmap)


def _configure_tray_icon_assets() -> None:
    icon_root = str(ICON_DIR)
    search_paths = [icon_root, *[path for path in QIcon.themeSearchPaths() if path != icon_root]]
    QIcon.setThemeSearchPaths(search_paths)
    if not QIcon.fallbackThemeName():
        QIcon.setFallbackThemeName("hicolor")


def _is_dark_theme() -> bool:
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    palette = QApplication.palette()
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    return window.lightness() < text.lightness()


def _resolved_theme(mode: str) -> str:
    if mode == "system":
        return "dark" if _is_dark_theme() else "light"
    if mode in {"dark", "light"}:
        return mode
    return "system"


def _load_tray_icon() -> QIcon:
    icon_path = DARK_ICON_PATH if _is_dark_theme() else LIGHT_ICON_PATH
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    return _fallback_icon()


def _load_window_icon() -> QIcon:
    if COLOR_ICON_PATH.exists():
        icon = QIcon(str(COLOR_ICON_PATH))
        if not icon.isNull():
            return icon
    return _fallback_icon()


def _theme_colors(mode: str) -> tuple[str, str]:
    if mode == "dark":
        return "#ffffff", "#292C30"
    if mode == "light":
        return "#000000", "#EFF0F1"
    if _is_dark_theme():
        return "#ffffff", "#292C30"
    return "#000000", "#EFF0F1"


def _autostart_desktop_entry() -> str:
    return dedent(
        f"""\
        [Desktop Entry]
        Type=Application
        Name=Codex Usage Widget
        Exec={RUN_SCRIPT}
        Path={PROJECT_ROOT}
        Terminal=false
        Hidden=false
        NoDisplay=true
        X-GNOME-Autostart-enabled=true
        X-GNOME-Autostart-Delay=2
        """
    )


class WidgetContextMenu(QDialog):
    def __init__(self, widget: "UsageWidget") -> None:
        super().__init__(widget)
        self.widget = widget
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("widgetContextMenu")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

        self.panel = QFrame(self)
        self.panel.setObjectName("contextPanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 8, 0, 8)
        panel_layout.setSpacing(0)

        self.minimize_button = self._make_button("Minimize to Tray", self.widget.hide_to_tray)
        self.refresh_button = self._make_button("Refresh", self.widget.refresh)
        self.always_on_top = QCheckBox("Always on Top")
        self.always_on_top.setChecked(self.widget.always_on_top)
        self.always_on_top.toggled.connect(self.widget.set_always_on_top)
        self.close_to_tray = QCheckBox("Closing minimizes to tray")
        self.close_to_tray.setChecked(self.widget.close_to_tray)
        self.close_to_tray.toggled.connect(self.widget.set_close_to_tray)
        self.settings_button = self._make_button("Settings", self.widget.show_settings)
        self.exit_button = self._make_button("Exit", self.widget.request_exit)

        panel_layout.addWidget(self.minimize_button)
        panel_layout.addWidget(self.refresh_button)
        panel_layout.addWidget(self.always_on_top)
        panel_layout.addWidget(self.close_to_tray)
        panel_layout.addWidget(self.settings_button)
        panel_layout.addWidget(self._separator())
        panel_layout.addWidget(self.exit_button)

        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(22)
        shadow.setXOffset(0)
        shadow.setYOffset(3)
        shadow.setColor(QColor(0, 0, 0, 85))
        self.panel.setGraphicsEffect(shadow)

        self._apply_theme()

    def _make_button(self, text: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.setFlat(True)
        button.clicked.connect(lambda _checked=False: self._invoke(callback))
        return button

    def _invoke(self, callback) -> None:
        self.hide()
        callback()

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(1)
        return line

    def _apply_theme(self) -> None:
        mode = _resolved_theme(self.widget.widget_theme_mode)
        text, background = _theme_colors(mode)
        hover = "rgba(255, 255, 255, 0.08)" if mode == "dark" else "rgba(0, 0, 0, 0.08)"
        line = "#595B5C"
        self.setStyleSheet(
            f"""
            QDialog#widgetContextMenu {{
                background: transparent;
            }}
            QFrame#contextPanel {{
                background-color: {background};
                color: {text};
                border: 1px solid {line};
                border-radius: 8px;
            }}
            QPushButton {{
                border: none;
                background: transparent;
                color: {text};
                padding: 8px 18px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QCheckBox {{
                color: {text};
                padding: 8px 18px;
            }}
            QFrame#contextPanel QFrame {{
                background-color: {line};
            }}
            """
        )

    def popup_at(self, global_pos: QPoint) -> None:
        self.always_on_top.setChecked(self.widget.always_on_top)
        self.close_to_tray.setChecked(self.widget.close_to_tray)
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.geometry() if screen is not None else self.widget.geometry()
        self.setGeometry(screen_geo)
        self.panel.adjustSize()
        self.panel.resize(self.panel.sizeHint())
        panel_pos = global_pos - self.geometry().topLeft()
        max_x = max(0, self.width() - self.panel.width())
        max_y = max(0, self.height() - self.panel.height())
        self.panel.move(
            max(0, min(panel_pos.x(), max_x)),
            max(0, min(panel_pos.y(), max_y)),
        )
        self.show()
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.hide()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        QTimer.singleShot(0, self.widget._fit_to_content)


class UsageWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot: UsageSnapshot = load_usage()
        self.drag_start: QPoint | None = None
        self.always_on_top = True
        self.last_position: QPoint | None = None
        self._allow_close = False
        self.settings = QSettings(APP_ORG, APP_ID)
        self.on_visibility_changed = None
        self.show_border = self.settings.value("showBorder", True, type=bool)
        self.show_model = self.settings.value("showModel", True, type=bool)
        self.show_used = self.settings.value("showUsed", True, type=bool)
        self.show_free = self.settings.value("showFree", True, type=bool)
        self.show_countdown = self.settings.value("showCountdown", True, type=bool)
        self.show_updated = self.settings.value("showUpdated", True, type=bool)
        self.close_to_tray = self.settings.value("closeToTray", True, type=bool)
        self.widget_layout_mode = self.settings.value(
            "widgetLayoutMode",
            self.settings.value("limitLayoutMode", "horizontal"),
            type=str,
        )
        self.widget_theme_mode = self.settings.value("widgetThemeMode", "system", type=str)
        self.autostart_enabled = self.settings.value("autostartEnabled", False, type=bool)
        self.context_menu = WidgetContextMenu(self)
        self._applying_theme = False

        self.setObjectName("usageWidget")
        self.setWindowTitle("Codex Usage")
        self.setWindowIcon(_load_window_icon())
        self._apply_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.panel = QFrame(self)
        self.panel.setObjectName("usagePanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        # Keep the top-level window flush with the screen edge; the panel already has its own
        # internal padding and border radius, so this outer layout does not need extra inset.
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.panel)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(2)

        self.arrow = QToolButton()
        self.arrow.setArrowType(Qt.ArrowType.RightArrow)
        self.arrow.setAutoRaise(True)
        self.arrow.setToolTip("Show context details")
        self.arrow.clicked.connect(self.toggle_details)

        self.model_label = QLabel()
        self.five_used = QLabel()
        self.five_reset = QLabel()
        self.week_used = QLabel()
        self.week_reset = QLabel()
        self.updated_label = QLabel()
        self.detail_label = QLabel()
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setVisible(False)
        five_font = self.five_used.font()
        five_font.setBold(True)
        self.five_used.setFont(five_font)
        week_font = self.week_used.font()
        week_font.setBold(True)
        self.week_used.setFont(week_font)
        self.five_title = self._section_title("5h")
        self.week_title = self._section_title("Week")

        self.model_separator = self._separator()
        self.limit_separator = self._separator()
        self.updated_separator = self._separator()
        self.content_layout = QGridLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setHorizontalSpacing(8)
        self.content_layout.setVerticalSpacing(2)
        panel_layout.addLayout(self.content_layout)
        panel_layout.addWidget(self.detail_label)

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._install_drag_filter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.focusChanged.connect(self._on_focus_changed)
            app.focusWindowChanged.connect(self._on_focus_window_changed)
            app.applicationStateChanged.connect(self._on_application_state_changed)
        self._sync_autostart_entry()
        self._apply_widget_layout()
        self.refresh()
        self._restore_position()
        QTimer.singleShot(0, self._apply_theme)

    def _install_drag_filter(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _section_title(self, text: str) -> QLabel:
        title_label = QLabel(text)
        title_label.setStyleSheet("font-weight: 600;")
        return title_label

    def _clear_layout(self, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
            elif child_layout is not None:
                self._clear_layout(child_layout)  # type: ignore[arg-type]

    def _apply_widget_layout(self) -> None:
        self._clear_layout(self.content_layout)
        if self.widget_layout_mode == "horizontal":
            self._layout_horizontal()
        else:
            self._layout_stacked()

    def _layout_horizontal(self) -> None:
        self.content_layout.addWidget(self.arrow, 0, 0, 2, 1)
        self.content_layout.addWidget(self.model_label, 0, 1)
        self.content_layout.addWidget(self.model_separator, 0, 2, 2, 1)
        self.content_layout.addWidget(self.five_title, 0, 3)
        self.content_layout.addWidget(self.five_used, 0, 4)
        self.content_layout.addWidget(self.five_reset, 1, 4)
        self.content_layout.addWidget(self.limit_separator, 0, 5, 2, 1)
        self.content_layout.addWidget(self.week_title, 0, 6)
        self.content_layout.addWidget(self.week_used, 0, 7)
        self.content_layout.addWidget(self.week_reset, 1, 7)
        self.content_layout.addWidget(self.updated_separator, 0, 8, 2, 1)
        self.content_layout.addWidget(self.updated_label, 0, 9)

    def _layout_stacked(self) -> None:
        self.content_layout.addWidget(self.arrow, 0, 0, 3, 1)
        self.content_layout.addWidget(self.model_label, 0, 1, 1, 2)
        self.content_layout.addWidget(self.updated_label, 0, 3)
        self.content_layout.addWidget(self.five_title, 1, 1)
        self.content_layout.addWidget(self.five_used, 1, 2)
        self.content_layout.addWidget(self.five_reset, 1, 3)
        self.content_layout.addWidget(self.week_title, 2, 1)
        self.content_layout.addWidget(self.week_used, 2, 2)
        self.content_layout.addWidget(self.week_reset, 2, 3)

    def toggle_details(self) -> None:
        visible = not self.detail_label.isVisible()
        self.detail_label.setVisible(visible)
        self.arrow.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
        QTimer.singleShot(0, self._fit_to_content)

    def _apply_window_flags(self) -> None:
        # Tool windows are often treated as palette panels and can be kept inside the work area
        # by the window manager. Use a normal frameless top-level window so the widget can be
        # dragged all the way to the screen edges.
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window | Qt.WindowType.WindowDoesNotAcceptFocus
        if self.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        visible = self.isVisible()
        position = self._current_position()
        if visible:
            self.hide()
        self.setWindowFlags(flags)
        if visible:
            self._move_to(position)
            self.show()
            self.raise_()
            self.activateWindow()

    def set_always_on_top(self, enabled: bool) -> None:
        self.last_position = self._current_position()
        self.always_on_top = enabled
        self._apply_window_flags()
        self.raise_()
        self.activateWindow()

    def set_show_border(self, enabled: bool) -> None:
        self.show_border = enabled
        self.settings.setValue("showBorder", enabled)
        self._apply_theme()
        self._fit_to_content()

    def set_display_pref(self, key: str, enabled: bool) -> None:
        setattr(self, key, enabled)
        setting_key = next(setting for _label, attr, setting in DISPLAY_PREFS if attr == key)
        self.settings.setValue(setting_key, enabled)
        self.update_labels()

    def set_autostart_enabled(self, enabled: bool) -> None:
        self.autostart_enabled = enabled
        self.settings.setValue("autostartEnabled", enabled)
        self._sync_autostart_entry()

    def set_close_to_tray(self, enabled: bool) -> None:
        self.close_to_tray = enabled
        self.settings.setValue("closeToTray", enabled)
        if hasattr(self, "context_menu"):
            self.context_menu.close_to_tray.setChecked(enabled)

    def set_widget_theme_mode(self, mode: str) -> None:
        if mode not in {m for _label, m in THEME_MODES}:
            mode = "system"
        self.widget_theme_mode = mode
        self.settings.setValue("widgetThemeMode", mode)
        self._apply_theme()
        self.update_labels()

    def _sync_autostart_entry(self) -> None:
        if self.autostart_enabled:
            AUTOSTART_ENTRY.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_ENTRY.write_text(_autostart_desktop_entry(), encoding="utf-8")
            return
        AUTOSTART_ENTRY.unlink(missing_ok=True)

    def apply_preferences(self, preferences: dict[str, bool | str]) -> None:
        self.set_show_border(preferences["show_border"])
        for _label, attr, setting in DISPLAY_PREFS:
            setattr(self, attr, preferences[attr])
            self.settings.setValue(setting, preferences[attr])
        self.set_widget_layout_mode(preferences["widget_layout_mode"])
        self.set_widget_theme_mode(preferences["widget_theme_mode"])
        self.set_autostart_enabled(preferences["autostart_enabled"])
        self.set_close_to_tray(preferences["close_to_tray"])
        self.update_labels()

    def set_widget_layout_mode(self, mode: str) -> None:
        if mode not in {m for _label, m in WIDGET_LAYOUT_MODES}:
            mode = "horizontal"
        self.widget_layout_mode = mode
        self.settings.setValue("widgetLayoutMode", mode)
        self._apply_widget_layout()
        self.update_labels()

    def _apply_theme(self) -> None:
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            mode = _resolved_theme(self.widget_theme_mode)
            text, background = _theme_colors(mode)
            border = "1px solid #595B5C" if self.show_border else "none"
            self.panel.setStyleSheet(
                f"""
                QFrame#usagePanel {{
                    background-color: {background};
                    color: {text};
                    border: {border};
                    border-radius: 8px;
                }}
                QFrame#usagePanel QLabel,
                QFrame#usagePanel QToolButton,
                QFrame#usagePanel QFrame {{
                    background-color: transparent;
                    color: {text};
                }}
                QFrame#usagePanel QToolButton {{
                    border: none;
                }}
                """
            )
            self.panel.style().unpolish(self.panel)
            self.panel.style().polish(self.panel)
            self.panel.update()
            self.context_menu._apply_theme()
        finally:
            self._applying_theme = False

    def _apply_border(self) -> None:
        self._apply_theme()

    def _current_position(self) -> QPoint:
        if self.isVisible():
            self.last_position = self.pos()
        return self.last_position or self.pos()

    def _move_to(self, position: QPoint | None) -> None:
        if position is not None:
            self.move(position)
            self.last_position = position
            self.settings.setValue("position", position)

    def _restore_position(self) -> None:
        position = self.settings.value("position")
        if isinstance(position, QPoint):
            self._move_to(position)

    def refresh(self) -> None:
        self.snapshot = load_usage()
        self.update_labels()

    def update_labels(self) -> None:
        now = datetime.now().astimezone()
        thread = self.snapshot.thread
        limits = self.snapshot.rate_limits
        model = thread.model or "unknown model"
        if thread.reasoning_effort:
            model = f"{model} ({thread.reasoning_effort})"
        self.model_label.setText(model)
        self.model_label.setVisible(self.show_model)

        self._update_limit_labels(limits.primary, self.five_used, self.five_reset, now)
        self._update_limit_labels(limits.secondary, self.week_used, self.week_reset, now)

        self.updated_label.setText(f"Updated {_ago(limits.updated_at, now)}")
        self.updated_label.setVisible(self.show_updated)
        self._update_separator_visibility()
        reset_at_5h = limits.primary.reset_at.strftime("%b %-d %-I:%M %p") if limits.primary.reset_at else "none"
        reset_at_week = limits.secondary.reset_at.strftime("%b %-d %-I:%M %p") if limits.secondary.reset_at else "none"
        thread_updated = thread.updated_at.strftime("%b %-d %-I:%M %p") if thread.updated_at else "none"
        tokens = f"{thread.tokens_used:,}" if thread.tokens_used is not None else "unknown"
        self.detail_label.setText(
            "\n".join(
                [
                    f"Plan: {limits.plan_type or 'unknown'} | Allowed: {limits.allowed if limits.allowed is not None else 'n/a'}",
                    f"5h reset: {reset_at_5h} | Weekly reset: {reset_at_week}",
                    f"Thread updated: {thread_updated} | Tokens: {tokens}",
                    f"CWD: {thread.cwd or 'unknown'}",
                    f"Log id: {limits.log_id or 'none'} | Read: {self.snapshot.read_at.strftime('%-I:%M:%S %p')}",
                    f"Error: {self.snapshot.error}" if self.snapshot.error else "",
                ]
            ).strip()
        )
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        panel_layout = self.panel.layout()
        if panel_layout is not None:
            panel_layout.invalidate()
            panel_layout.activate()
        self.panel.updateGeometry()
        self.updateGeometry()
        self.adjustSize()
        self.resize(self.sizeHint())

    def _update_separator_visibility(self) -> None:
        horizontal = self.widget_layout_mode == "horizontal"
        self.model_separator.setVisible(horizontal and self.model_label.isVisible())
        self.limit_separator.setVisible(horizontal)
        self.updated_separator.setVisible(horizontal and self.updated_label.isVisible())

    def _update_limit_labels(self, window: LimitWindow, usage: QLabel, reset: QLabel, now: datetime) -> None:
        parts: list[str] = []
        if not window.has_data:
            parts.append("waiting")
        elif window.used_percent is not None:
            free = 100 - window.used_percent
            if self.show_used:
                parts.append(f"{window.used_percent}% used")
            if self.show_free:
                parts.append(f"{free}% free")

        if self.widget_layout_mode == "horizontal":
            text = " ".join(parts)
        else:
            text = "\n".join(parts)
        color = _usage_color(window)
        usage.setText(text)
        usage.setVisible(bool(text))
        usage.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")

        _, reset_text = _reset_text(window, now)
        reset.setText(f"reset: {reset_text}" if window.has_data else reset_text)
        reset.setVisible(self.show_countdown)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        app = QApplication.instance()
        if watched is app and self.context_menu.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if not self.context_menu.geometry().contains(event.globalPosition().toPoint()):
                    self._close_context_menu()
            elif event.type() == QEvent.Type.WindowDeactivate:
                self._close_context_menu()
        if isinstance(watched, QWidget):
            top_level = watched.window()
            if top_level is not self:
                return super().eventFilter(watched, event)
            if event.type() == QEvent.Type.ContextMenu and isinstance(event, QContextMenuEvent):
                self.show_context_menu(event.globalPos())
                return True
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if watched is self.arrow:
                    return False
                if event.button() == Qt.MouseButton.LeftButton:
                    self._start_window_drag(event)
                    return True
            if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                if self.drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self.drag_start)
                    self.last_position = self.pos()
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                self._save_position()
                self.drag_start = None
                return False
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide_to_tray)
        elif event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            if self.widget_theme_mode == "system" and not self._applying_theme:
                QTimer.singleShot(0, self._apply_theme)
            if self.on_visibility_changed is not None:
                self.on_visibility_changed()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_window_drag(event)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_start)
            self.last_position = self.pos()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._save_position()
        self.drag_start = None
        event.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if self.close_to_tray:
            event.ignore()
            self.hide_to_tray()
            return
        self._allow_close = True
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def contextMenuEvent(self, event) -> None:
        self.show_context_menu(event.globalPos())

    def _event_global_pos(self, watched: QWidget, event: QMouseEvent) -> QPoint:
        return watched.mapToGlobal(event.position().toPoint())

    def _start_window_drag(self, event: QMouseEvent) -> None:
        use_manual_move = QApplication.platformName().lower() == "xcb"
        handle = self.windowHandle()
        if not use_manual_move and handle is not None and handle.startSystemMove():
            self.drag_start = None
            return
        self.drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _save_position(self) -> None:
        self.last_position = self.pos()
        self.settings.setValue("position", self.last_position)

    def hide_to_tray(self) -> None:
        self._close_context_menu()
        self.hide()
        if self.on_visibility_changed is not None:
            self.on_visibility_changed()

    def request_exit(self) -> None:
        self._allow_close = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        if self.on_visibility_changed is not None:
            self.on_visibility_changed()

    def show_context_menu(self, global_pos: QPoint) -> None:
        self._close_context_menu()
        self.context_menu.popup_at(global_pos)

    def _close_context_menu(self) -> None:
        if self.context_menu.isVisible():
            self.context_menu.hide()
            self.context_menu.close()
        self._fit_to_content()

    def _on_focus_window_changed(self, window) -> None:
        if not self.context_menu.isVisible():
            return
        popup_window = self.context_menu.windowHandle()
        if popup_window is None or window is None or window is not popup_window:
            self._close_context_menu()

    def _on_application_state_changed(self, state) -> None:
        if self.context_menu.isVisible() and state == Qt.ApplicationState.ApplicationInactive:
            self._close_context_menu()

    def _on_focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        return

    def _build_context_menu(self) -> None:
        return

    def show_settings(self) -> bool:
        self._close_context_menu()
        dialog = SettingsDialog(self)
        dialog.setModal(True)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.always_on_top)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.apply_preferences(dialog.preferences())
        return True

    def hideEvent(self, event) -> None:
        self._close_context_menu()
        super().hideEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, widget: UsageWidget) -> None:
        super().__init__(widget)
        self.setWindowTitle("Codex Usage Settings")
        mode = _resolved_theme(widget.widget_theme_mode)
        text, background = _theme_colors(mode)
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {background};
                color: {text};
            }}
            QLabel, QCheckBox {{
                color: {text};
                background-color: transparent;
            }}
            QComboBox {{
                color: {text};
                background-color: {background};
                border: 1px solid {text};
                padding: 4px 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {background};
                color: {text};
                selection-background-color: {text};
                selection-color: {background};
            }}
            QDialogButtonBox QPushButton {{
                color: {text};
                background-color: {background};
                border: 1px solid {text};
                padding: 4px 12px;
            }}
            """
        )
        self.checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        autostart = QCheckBox("Autostart at login")
        autostart.setChecked(widget.autostart_enabled)
        layout.addWidget(autostart)
        self.checkboxes["autostart_enabled"] = autostart

        close_to_tray = QCheckBox("Closing minimizes to tray")
        close_to_tray.setChecked(widget.close_to_tray)
        layout.addWidget(close_to_tray)
        self.checkboxes["close_to_tray"] = close_to_tray

        layout.addWidget(QLabel("Theme"))
        self.widget_theme = QComboBox()
        for label, mode in THEME_MODES:
            self.widget_theme.addItem(label, mode)
        index = self.widget_theme.findData(widget.widget_theme_mode)
        self.widget_theme.setCurrentIndex(max(0, index))
        layout.addWidget(self.widget_theme)

        border = QCheckBox("Border")
        border.setChecked(widget.show_border)
        layout.addWidget(border)
        self.checkboxes["show_border"] = border

        display_label = QLabel("Display")
        display_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(display_label)
        for label, attr, _setting in DISPLAY_PREFS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(getattr(widget, attr))
            layout.addWidget(checkbox)
            self.checkboxes[attr] = checkbox

        layout.addWidget(QLabel("Widget layout"))
        self.limit_layout = QComboBox()
        for label, mode in WIDGET_LAYOUT_MODES:
            self.limit_layout.addItem(label, mode)
        current_mode = widget.widget_layout_mode
        index = self.limit_layout.findData(current_mode)
        self.limit_layout.setCurrentIndex(max(0, index))
        layout.addWidget(self.limit_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def preferences(self) -> dict[str, bool | str]:
        prefs: dict[str, bool | str] = {key: checkbox.isChecked() for key, checkbox in self.checkboxes.items()}
        prefs["widget_layout_mode"] = self.limit_layout.currentData() or "horizontal"
        prefs["widget_theme_mode"] = self.widget_theme.currentData() or "system"
        return prefs


class UsageTray:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.widget = UsageWidget()
        self.widget.on_visibility_changed = self.update
        self.tray = QSystemTrayIcon(self._tray_icon(), self.app)
        self.tray.setToolTip("Codex usage")
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)

        self.menu = QMenu()
        self.show_action = QAction("Show Window", self.menu)
        self.show_action.triggered.connect(lambda _checked=False: self.toggle_window())
        self.refresh_action = QAction("Refresh", self.menu)
        self.refresh_action.triggered.connect(lambda _checked=False: self.refresh())
        self.always_action = QAction("Always on Top", self.menu)
        self.always_action.setCheckable(True)
        self.always_action.setChecked(self.widget.always_on_top)
        self.always_action.triggered.connect(lambda checked=False: self.widget.set_always_on_top(checked))
        self.settings_action = QAction("Settings", self.menu)
        self.settings_action.triggered.connect(lambda _checked=False: self.show_settings())
        self.exit_action = QAction("Exit", self.menu)
        self.exit_action.triggered.connect(lambda _checked=False: self.exit())
        self.menu.addAction(self.show_action)
        self.menu.addAction(self.refresh_action)
        self.menu.addAction(self.always_action)
        self.menu.addAction(self.settings_action)
        self.menu.addSeparator()
        self.menu.addAction(self.exit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(60_000)

        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update)
        self.countdown_timer.start(30_000)

        self.widget.show()
        self.update()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self.menu.popup(QCursor.pos())

    def show_window(self) -> None:
        self.widget.showNormal()
        self.widget.raise_()
        self.widget.activateWindow()
        self.update()

    def toggle_window(self) -> None:
        if self._window_visible():
            self.widget.hide_to_tray()
        else:
            self.show_window()

    def refresh(self) -> None:
        self.widget.refresh()
        self.update()

    def set_display_pref(self, key: str, enabled: bool) -> None:
        self.widget.set_display_pref(key, enabled)
        self.update()

    def show_settings(self) -> None:
        if self.widget.show_settings():
            self.update()

    def update(self) -> None:
        self.widget.update_labels()
        self.tray.setIcon(self._tray_icon())
        self.tray.setToolTip(self._tooltip())
        self.show_action.setText("Minimize to Tray" if self._window_visible() else "Show Window")
        self.always_action.setChecked(self.widget.always_on_top)

    def _system_theme_changed(self) -> None:
        self.update()

    def _window_visible(self) -> bool:
        return self.widget.isVisible() and not self.widget.isMinimized()

    def _tray_icon(self) -> QIcon:
        return _load_tray_icon()

    def _tooltip(self) -> str:
        now = datetime.now().astimezone()
        snapshot = self.widget.snapshot
        five_used, five_reset = _reset_text(snapshot.rate_limits.primary, now)
        week_used, week_reset = _reset_text(snapshot.rate_limits.secondary, now)
        model = snapshot.thread.model or "unknown model"
        return "\n".join(
            [
                f"Model: {model}",
                f"5h: {five_used}, reset {five_reset}",
                f"Week: {week_used}, reset {week_reset}",
                f"Updated: {_ago(snapshot.rate_limits.updated_at, now)}",
            ]
        )

    def exit(self) -> None:
        self.tray.hide()
        self.widget.request_exit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Codex Usage Widget")
    QGuiApplication.setDesktopFileName(APP_ID)
    app.setWindowIcon(_load_window_icon())
    app.setQuitOnLastWindowClosed(False)
    _configure_tray_icon_assets()

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.RuntimeLocation
    )
    lock_dir = Path(runtime_dir) if runtime_dir else Path("/tmp")
    lock = QLockFile(str(lock_dir / "codex-usage-widget.lock"))
    lock.setStaleLockTime(30_000)
    locked = lock.tryLock(100)
    if not locked:
        lock.removeStaleLockFile()
        locked = lock.tryLock(100)
    if not locked:
        print("Codex Usage Widget is already running.", file=sys.stderr)
        return 0
    app._codex_usage_lock = lock

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray is not available in this desktop session.", file=sys.stderr)
    tray = UsageTray(app)
    app._codex_usage_tray = tray
    return app.exec()
