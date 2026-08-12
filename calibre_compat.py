#!/usr/bin/env python3
"""Small Qt compatibility shims for running Calibre inside EAF's PyQt."""

from __future__ import annotations

import math
import sys
import types
import weakref

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QApplication, QWidget


_ACTION_MENUS = weakref.WeakKeyDictionary()


class ProgressIndicator(QWidget):
    """Pure-PyQt replacement for Calibre's Qt-linked spinner extension."""

    def __init__(self, parent=None, size=32, interval=80):
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(interval)))
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(int(size), int(size))

    def _advance(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self):
        self.show()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    startAnimation = start
    stopAnimation = stop

    def paintEvent(self, event):  # noqa: N802 - Qt API name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        radius = max(2.0, min(self.width(), self.height()) * 0.32)
        width = max(1.0, min(self.width(), self.height()) * 0.08)
        base = self.palette().windowText().color()
        for index in range(12):
            angle = math.radians(self._angle - index * 30)
            color = QColor(base)
            color.setAlphaF(max(0.12, 1.0 - index / 12.0))
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            inner = radius * 0.58
            painter.drawLine(
                int(center.x() + math.cos(angle) * inner),
                int(center.y() + math.sin(angle) * inner),
                int(center.x() + math.cos(angle) * radius),
                int(center.y() + math.sin(angle) * radius),
            )


class SpinAnimator(QWidget):
    updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.updated)

    def start(self):
        self._timer.start(80)

    def stop(self):
        self._timer.stop()

    def draw(self, painter, rect, color):
        del painter, rect, color


def set_no_activate_on_click(widget):
    widget.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)


def set_menu_on_action(action, menu):
    _ACTION_MENUS[action] = menu


def menu_for_action(action):
    return _ACTION_MENUS.get(action)


def set_image_allocation_limit(megabytes=0):
    del megabytes


def draw_snake_spinner(*args, **kwargs):
    del args, kwargs


def install_progress_indicator_shim():
    module = types.ModuleType("calibre_extensions.progress_indicator")
    module.QProgressIndicator = ProgressIndicator
    module.SpinAnimator = SpinAnimator
    module.draw_snake_spinner = draw_snake_spinner
    module.set_no_activate_on_click = set_no_activate_on_click
    module.set_menu_on_action = set_menu_on_action
    module.menu_for_action = menu_for_action
    module.set_image_allocation_limit = set_image_allocation_limit
    sys.modules[module.__name__] = module


def install_imageops_shim():
    """Avoid Calibre's imageops binary, which is linked to another Qt."""
    module = types.ModuleType("calibre_extensions.imageops")

    def overlay(source, canvas, left, top):
        painter = QPainter(canvas)
        painter.drawImage(int(left), int(top), source)
        painter.end()
        return canvas

    def grayscale(image):
        return image.convertToFormat(QImage.Format.Format_Grayscale8)

    def set_opacity(image, alpha):
        result = QImage(image)
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(result.rect(), QColor(0, 0, 0, int(255 * float(alpha))))
        painter.end()
        return result

    module.overlay = overlay
    module.texture_image = lambda canvas, texture: overlay(texture, canvas, 0, 0)
    module.remove_borders = lambda image, fuzz=0: image
    module.grayscale = grayscale
    module.set_opacity = set_opacity
    module.has_transparent_pixels = lambda image: image.hasAlphaChannel()
    module.gaussian_sharpen = lambda image, *args: image
    module.gaussian_blur = lambda image, *args: image
    module.despeckle = lambda image, *args: image
    module.oil_paint = lambda image, *args: image
    module.normalize = lambda image, *args: image
    module.quantize = lambda image, *args: image
    module.ordered_dither = lambda image, *args: image
    sys.modules[module.__name__] = module


def prepare_eaf_application():
    """Supply the few QApplication services expected by Calibre's viewer."""
    app = QApplication.instance()
    if app is None:
        return
    if not hasattr(app, "palette_changed"):
        class PaletteRelay(QWidget):
            palette_changed = pyqtSignal()
            shutdown_signal_received = pyqtSignal()

        relay = PaletteRelay()
        app._eaf_calibre_palette_relay = relay
        app.palette_changed = relay.palette_changed
        app.shutdown_signal_received = relay.shutdown_signal_received

    if not hasattr(app, "palette_manager"):
        class EAFPaletteManager:
            using_calibre_style = False

            @property
            def is_dark_theme(self):
                palette = QApplication.instance().palette()
                window = palette.color(QPalette.ColorRole.Window).lightness()
                text = palette.color(QPalette.ColorRole.WindowText).lightness()
                return window < text

            def tree_view_hover_style(self):
                palette = QApplication.instance().palette()
                color = palette.color(QPalette.ColorRole.Highlight).name()
                return (
                    "QTreeView::item:hover { background: " + color + "; "
                    "border: 0px; border-radius: 6px; }"
                )

            def refresh_palette(self):
                QApplication.instance().palette_changed.emit()

        app.palette_manager = EAFPaletteManager()
    app.is_dark_theme = app.palette_manager.is_dark_theme
    app.using_calibre_style = False
    app.file_event_hook = None
    if not hasattr(app, "load_builtin_fonts"):
        app.load_builtin_fonts = lambda: None
    if not hasattr(app, "ensure_window_on_screen"):
        def ensure_window_on_screen(widget):
            screen = widget.screen() or app.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            geometry = widget.frameGeometry()
            if not available.intersects(geometry):
                widget.move(available.topLeft())

        app.ensure_window_on_screen = ensure_window_on_screen
    module = sys.modules["calibre_extensions.progress_indicator"]
    app.pi = module
