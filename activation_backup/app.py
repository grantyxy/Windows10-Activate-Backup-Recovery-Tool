"""桌面入口，不解析命令行参数或使用终端交互。"""

from __future__ import annotations

import os
import sys

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .ui import MainWindow


def run() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Windows Activation Backup")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("grantyxy")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#f4f6fa"),
        (QPalette.ColorRole.WindowText, "#192638"),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, "#edf3fc"),
        (QPalette.ColorRole.Text, "#192638"),
        (QPalette.ColorRole.Button, "#ffffff"),
        (QPalette.ColorRole.ButtonText, "#192638"),
        (QPalette.ColorRole.Highlight, "#2464d5"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    if os.name != "nt" or sys.maxsize <= 2**32:
        QMessageBox.warning(None, "系统不支持", "请在 Windows 10 / 11 中使用 64 位版本运行本工具。")
        return 1
    window = MainWindow()
    window.show()
    return app.exec()
