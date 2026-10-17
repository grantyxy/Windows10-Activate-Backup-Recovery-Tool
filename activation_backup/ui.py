"""PyQt6 图形界面；系统访问、加密和文件处理均在后台线程执行。"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QStandardPaths, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .archive import BackupData
from .models import AppError, Cancelled, Snapshot, compare_systems, redact
from .service import ActivationService, BackupResult, RecoveryResult
from .windows import is_admin, relaunch_elevated

STYLE = """
QMainWindow, QWidget#root { background: #f4f6fa; color: #192638; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 10pt; color: #192638; }
QFrame#sidebar { background: #14263f; border: none; }
QFrame#sidebar QLabel { color: #dce6f5; background: transparent; }
QLabel#brand { color: white; font-size: 20pt; font-weight: 700; }
QLabel#small { color: #687a91; font-size: 9pt; }
QLabel#title { font-size: 23pt; font-weight: 700; }
QLabel#section { font-size: 13pt; font-weight: 600; }
QLabel#status { font-size: 26pt; font-weight: 700; color: #13725c; }
QLabel#hint { background: #eaf1fd; color: #35547f; padding: 14px; border-radius: 8px; }
QListWidget#navigation { background: transparent; border: none; outline: none; color: #bfccdf; }
QListWidget#navigation::item { padding: 15px 12px; margin: 4px 0; border-radius: 8px; color: #bfccdf; }
QListWidget#navigation::item:selected { background: #284263; color: white; }
QFrame#card { background: white; border: 1px solid #dce3ed; border-radius: 12px; }
QPushButton { background: white; border: 1px solid #cbd5e2; border-radius: 7px; padding: 10px 17px; color: #29435f; }
QPushButton:hover { background: #edf3fc; border-color: #739acf; }
QPushButton:pressed { background: #dfeaf8; }
QPushButton#primary { background: #2464d5; color: white; border-color: #2464d5; font-weight: 600; }
QPushButton#primary:hover { background: #1b54bb; }
QPushButton:disabled, QPushButton#primary:disabled { color: #98a3b0; background: #e9edf2; border-color: #e0e5ed; }
QLineEdit, QComboBox { background: white; border: 1px solid #ccd6e3; border-radius: 7px; padding: 10px; selection-background-color: #2464d5; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #2464d5; }
QLineEdit:disabled { background: #f0f3f7; }
QCheckBox { spacing: 9px; padding: 6px 0; }
QPlainTextEdit { background: white; border: 1px solid #dce3ed; border-radius: 8px; padding: 10px; }
QProgressBar { border: none; background: #e2e8f2; height: 7px; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #2464d5; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: #f4f6fa; }
QToolTip { color: #192638; background: white; border: 1px solid #ccd6e3; }
"""


def label(text: str = "", name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text: str, callback, *, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(14)
    layout.addWidget(label(title, "section"))
    return frame, layout


def password_field(placeholder: str) -> QLineEdit:
    widget = QLineEdit()
    widget.setEchoMode(QLineEdit.EchoMode.Password)
    widget.setPlaceholderText(placeholder)
    widget.setMaxLength(1024)
    return widget


class Task(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    cancelled = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self.work = work
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.result.emit(self.work(self.progress.emit, self.cancel_event.is_set))
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.error.emit(redact(str(exc)) or "操作失败，请检查系统状态与文件权限。")
        finally:
            self.work = None


class MainWindow(QMainWindow):
    def __init__(self, service: ActivationService | None = None, *, auto_query: bool = True):
        super().__init__()
        self.service = service or ActivationService()
        self.snapshot: Snapshot | None = None
        self.backup: BackupData | None = None
        self.task: Task | None = None
        self._success = None
        self.setWindowTitle(f"Windows 激活备份 · {__version__}")
        self.resize(1140, 820)
        self.setMinimumSize(940, 650)
        self.setStyleSheet(STYLE)
        self._build()
        self._sync_actions()
        if auto_query:
            QTimer.singleShot(0, self.refresh)

    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 34, 22, 24)
        side.setSpacing(20)
        side.addWidget(label("Windows\n激活备份", "brand"))
        side.addWidget(label("留存许可资料\n为重新安装做好准备"))
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.addItems(["概览", "创建备份", "校验与恢复", "操作记录"])
        side.addWidget(self.navigation, 1)
        side.addWidget(label("本地加密 · 密钥脱敏"))
        side.addWidget(label(f"v{__version__}  /  PyQt6"))
        outer.addWidget(sidebar)
        body = QVBoxLayout()
        body.setContentsMargins(30, 26, 30, 22)
        body.setSpacing(16)
        self.pages = QStackedWidget()
        for builder in (self._overview, self._backup_page, self._recovery_page, self._log_page):
            page = builder()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        body.addWidget(self.pages, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.activity = label("就绪", "small")
        body.addWidget(self.activity)
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        progress_row.addWidget(self.progress, 1)
        self.cancel_button = button("取消备份", self.cancel)
        self.cancel_button.setVisible(False)
        progress_row.addWidget(self.cancel_button)
        body.addLayout(progress_row)
        outer.addLayout(body, 1)

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 10, 4)
        layout.setSpacing(18)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "small"))
        return widget, layout

    def _overview(self):
        page, layout = self._page(
            "许可状态，一目了然", "读取本机 Windows 信息，确认备份前的激活状态。"
        )
        frame, content = card("本机激活状态")
        self.status_label = label("等待查询", "status")
        content.addWidget(self.status_label)
        self.status_detail = label("查询仅读取系统信息。", "small")
        content.addWidget(self.status_detail)
        row = QHBoxLayout()
        self.refresh_button = button("刷新状态", self.refresh, primary=True)
        row.addWidget(self.refresh_button)
        row.addWidget(button("打开 Windows 激活设置", self.open_settings))
        row.addStretch()
        content.addLayout(row)
        layout.addWidget(frame)
        frame, content = card("系统与许可证")
        self.system_fields = {}
        form = QFormLayout()
        form.setVerticalSpacing(12)
        for name in (
            "系统",
            "版本类型",
            "系统构建",
            "许可证通道",
            "密钥尾号",
            "备份密钥",
            "查询时间",
        ):
            value = label("—")
            self.system_fields[name] = value
            form.addRow(label(name, "small"), value)
        content.addLayout(form)
        layout.addWidget(frame)
        self.system_notes = label("数字许可证是否关联账号，请以 Windows 激活设置页面为准。", "hint")
        layout.addWidget(self.system_notes)
        self.admin_button = button(
            "已在管理员模式运行" if is_admin() else "以管理员身份重新打开", self.elevate
        )
        self.admin_button.setEnabled(not is_admin())
        layout.addWidget(self.admin_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return page

    def _backup_page(self):
        page, layout = self._page(
            "创建一份加密备份", "保存许可信息、可读取的产品密钥，以及可选的许可存储文件。"
        )
        frame, content = card("备份位置")
        row = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setReadOnly(True)
        self.destination.setPlaceholderText("选择 .w11ab 备份文件的保存位置")
        row.addWidget(self.destination, 1)
        row.addWidget(button("选择位置…", self.choose_destination))
        content.addLayout(row)
        self.include_store = QCheckBox("包含许可存储文件（可能需要管理员权限）")
        self.include_store.setChecked(True)
        content.addWidget(self.include_store)
        content.addWidget(
            label(
                "将递归读取 spp/store/2.0。文件无法完整读取时会停止，不会生成缺项却显示成功的备份。",
                "small",
            )
        )
        layout.addWidget(frame)
        frame, content = card("设置备份密码")
        self.new_password = password_field("至少 10 个字符，建议使用较长的独立密码")
        self.confirm_password = password_field("再次输入密码")
        self.show_password = QCheckBox("显示密码")
        self.show_password.toggled.connect(self.toggle_password)
        content.addWidget(self.new_password)
        content.addWidget(self.confirm_password)
        content.addWidget(self.show_password)
        content.addWidget(
            label(
                "整个备份经过加密。密码不保存，遗失后无法恢复；重装前请把备份复制到另一块磁盘。",
                "small",
            )
        )
        layout.addWidget(frame)
        layout.addWidget(
            label("备份成功表示资料已保存并通过校验，不代表数字许可证一定能够离线恢复。", "hint")
        )
        self.create_button = button("加密并创建备份", self.create_backup, primary=True)
        layout.addWidget(self.create_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return page

    def _recovery_page(self):
        page, layout = self._page(
            "先校验，再恢复", "检查备份完整性与设备匹配情况，然后选择适合的恢复方式。"
        )
        frame, content = card("打开加密备份")
        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText("选择 .w11ab 文件")
        self.source.textChanged.connect(self._invalidate_backup)
        row.addWidget(self.source, 1)
        row.addWidget(button("选择备份…", self.choose_source))
        content.addLayout(row)
        row = QHBoxLayout()
        self.open_password = password_field("输入创建此备份时的密码")
        row.addWidget(self.open_password, 1)
        self.verify_button = button("解密并校验", self.verify_backup, primary=True)
        row.addWidget(self.verify_button)
        content.addLayout(row)
        layout.addWidget(frame)
        frame, content = card("校验结果与恢复操作")
        self.backup_details = label("尚未打开备份。校验不会更改当前 Windows 的激活状态。")
        content.addWidget(self.backup_details)
        self.key_choice = QComboBox()
        self.key_choice.addItem("选择备份密钥（默认不安装）", -1)
        self.key_choice.currentIndexChanged.connect(self._sync_actions)
        content.addWidget(self.key_choice)
        content.addWidget(
            label(
                "固件密钥可能属于出厂版本；注册表密钥可能是通用安装密钥。读取到密钥不等于确认其可用性。",
                "small",
            )
        )
        row = QHBoxLayout()
        self.restore_button = button("安装所选密钥并尝试激活", self.restore_selected, primary=True)
        self.view_key_button = button("查看所选密钥", self.view_key)
        row.addWidget(self.restore_button)
        row.addWidget(self.view_key_button)
        row.addStretch()
        content.addLayout(row)
        row = QHBoxLayout()
        self.export_button = button("导出归档文件…", self.export_files)
        row.addWidget(self.export_button)
        row.addWidget(button("关闭已解密备份", self.forget_backup))
        row.addStretch()
        content.addLayout(row)
        layout.addWidget(frame)
        frame, content = card("数字许可证恢复")
        content.addWidget(
            label(
                "同一设备重装相同版本后，可联网尝试重新激活。硬件变更或账号关联问题请在系统激活页面使用疑难解答。",
                "small",
            )
        )
        row = QHBoxLayout()
        self.activate_button = button("尝试系统重新激活", self.activate_current)
        row.addWidget(self.activate_button)
        row.addWidget(button("打开激活设置 / 疑难解答", self.open_settings))
        row.addStretch()
        content.addLayout(row)
        layout.addWidget(frame)
        layout.addWidget(
            label(
                "归档文件只导出到你选择的新目录。本工具不会覆盖系统许可存储；最终是否激活以 Windows 返回的状态为准。",
                "hint",
            )
        )
        layout.addStretch()
        return page

    def _log_page(self):
        page, layout = self._page("操作记录", "只记录本次运行中的操作结果，产品密钥自动脱敏。")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.logs.setMinimumHeight(320)
        layout.addWidget(self.logs, 1)
        row = QHBoxLayout()
        row.addWidget(button("导出记录…", self.export_log))
        row.addWidget(button("清空记录", self.logs.clear))
        row.addStretch()
        layout.addLayout(row)
        return page

    def log(self, text: str):
        self.logs.appendPlainText(f"[{datetime.now():%H:%M:%S}] {redact(text)}")

    def _run(self, title: str, work, success, *, cancellable: bool = False):
        if self.task is not None:
            return
        self.pages.setEnabled(False)
        self.activity.setText(title)
        self.log(title)
        self.progress.setRange(0, 100 if cancellable else 0)
        self.progress.setValue(0)
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(True)
        self._success = success
        self.task = Task(work, self)
        self.task.progress.connect(self._progress)
        self.task.result.connect(self._result)
        self.task.error.connect(self._error)
        self.task.cancelled.connect(self._cancelled)
        self.task.finished.connect(self._finished)
        self.task.start()

    def _progress(self, percent: int, text: str):
        self.progress.setRange(0, 100)
        self.progress.setValue(percent)
        self.activity.setText(text)

    def _result(self, value):
        if self._success:
            try:
                self._success(value)
            except Exception as exc:
                self._error("结果显示失败，请查看操作记录并刷新状态。\n" + redact(str(exc)))

    def _error(self, message: str):
        self.activity.setText("操作未完成，请查看提示或操作记录。")
        self.log(message)
        QMessageBox.warning(self, "操作未完成", message)

    def _cancelled(self, message: str):
        self.activity.setText(message)
        self.log(message)

    def _finished(self):
        task, self.task = self.task, None
        self._success = None
        self.pages.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.cancel_button.setVisible(False)
        if task:
            task.deleteLater()
        self._sync_actions()

    def _sync_actions(self, *_):
        if not hasattr(self, "restore_button"):
            return
        selected = self.key_choice.currentData()
        has_key = (
            self.backup is not None
            and type(selected) is int
            and 0 <= selected < len(self.backup.snapshot.keys)
        )
        compatible = (
            self.backup is not None
            and self.snapshot is not None
            and compare_systems(self.backup.snapshot, self.snapshot).can_install_key
        )
        self.restore_button.setEnabled(bool(has_key and compatible and not self.snapshot.activated))
        self.view_key_button.setEnabled(bool(has_key))
        self.export_button.setEnabled(bool(self.backup and self.backup.files))
        self.activate_button.setEnabled(
            bool(self.snapshot and self.snapshot.primary and not self.snapshot.activated)
        )

    def refresh(self):
        self._run("正在查询本机许可状态…", lambda p, c: self.service.query(), self.show_snapshot)

    def show_snapshot(self, snapshot: Snapshot):
        self.snapshot = snapshot
        product = snapshot.primary
        self.status_label.setText(product.status_label if product else "状态未确认")
        self.status_label.setStyleSheet(
            "color: #13725c;" if snapshot.activated else "color: #9b631c;"
        )
        self.status_detail.setText(
            "Windows 软件许可服务返回的状态。账号关联情况请在系统设置中查看。"
        )
        values = {
            "系统": snapshot.os_name + " · " + snapshot.architecture,
            "版本类型": snapshot.edition or "未知",
            "系统构建": snapshot.build,
            "许可证通道": product.description if product else "未获取到记录",
            "密钥尾号": product.partial_key if product else "—",
            "备份密钥": "、".join(k.label + " · " + k.value[-5:] for k in snapshot.keys)
            or "未读取到完整密钥",
            "查询时间": datetime.fromisoformat(snapshot.captured_at)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S"),
        }
        for name, value in values.items():
            self.system_fields[name].setText(value)
        self.system_notes.setText(
            "\n".join(snapshot.warnings)
            or "已读取许可信息。数字许可证关联情况请以系统激活页面为准。"
        )
        self.activity.setText("本机许可信息已更新。")
        self.log("本机状态：" + (product.status_label if product else "未确认"))
        self._render_backup()
        self._sync_actions()

    def choose_destination(self):
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        default = str(Path(folder) / f"Windows激活备份-{datetime.now():%Y%m%d-%H%M%S}.w11ab")
        path, _ = QFileDialog.getSaveFileName(
            self, "选择新的备份文件", default, "加密激活备份 (*.w11ab)"
        )
        if path:
            if not path.lower().endswith(".w11ab"):
                path += ".w11ab"
            self.destination.setText(path)

    def toggle_password(self, visible: bool):
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.new_password.setEchoMode(mode)
        self.confirm_password.setEchoMode(mode)

    def create_backup(self):
        password = self.new_password.text()
        if not self.destination.text():
            QMessageBox.information(self, "选择位置", "请先选择备份文件的保存位置。")
            return
        if len(password) < 10 or not password.strip():
            QMessageBox.information(self, "密码太短", "备份密码至少需要 10 个字符。")
            return
        if password != self.confirm_password.text():
            QMessageBox.information(self, "密码不一致", "两次输入的密码不一致，请重新输入。")
            return
        path, include = Path(self.destination.text()), self.include_store.isChecked()
        self._run(
            "正在创建加密备份…",
            lambda p, c: self.service.create_backup(path, password, include, p, c),
            self.backup_created,
            cancellable=True,
        )

    def backup_created(self, result: BackupResult):
        self.new_password.clear()
        self.confirm_password.clear()
        self.show_password.setChecked(False)
        self.show_snapshot(result.snapshot)
        message = (
            f"备份已保存并通过校验。\n包含 {result.file_count} 个许可存储文件。\n{result.path}"
        )
        self.activity.setText("备份已完成。请将备份复制到另一块磁盘并妥善保管密码。")
        self.log(message)
        QMessageBox.information(self, "备份完成", message)

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择加密激活备份", "", "加密激活备份 (*.w11ab);;所有文件 (*)"
        )
        if path:
            self.source.setText(path)

    def _invalidate_backup(self, *_):
        self.backup = None
        self._render_backup()
        self._sync_actions()

    def verify_backup(self):
        if not self.source.text() or not self.open_password.text():
            QMessageBox.information(self, "信息不完整", "请选择备份文件并输入密码。")
            return
        path, password = Path(self.source.text()), self.open_password.text()
        self._invalidate_backup()
        self.open_password.clear()
        self._run(
            "正在解密并校验备份…",
            lambda p, c: self.service.open_backup(path, password),
            self.backup_opened,
        )

    def backup_opened(self, backup: BackupData):
        self.backup = backup
        self._render_backup(reset_keys=True)
        self.activity.setText("密码认证与全部文件完整性校验通过。")
        self.log(f"备份校验通过：{len(backup.files)} 个许可存储文件。")
        self._sync_actions()

    def _render_backup(self, *, reset_keys: bool = False):
        if not hasattr(self, "backup_details"):
            return
        if self.backup is None:
            self.backup_details.setText("尚未打开备份。校验不会更改当前 Windows 的激活状态。")
            self.key_choice.clear()
            self.key_choice.addItem("选择备份密钥（默认不安装）", -1)
            return
        saved = self.backup.snapshot
        details = [
            "完整性校验通过",
            f"来源系统：{saved.os_name} · {saved.edition} · {saved.build}",
            f"备份时间：{datetime.fromisoformat(saved.captured_at).astimezone():%Y-%m-%d %H:%M:%S}",
            f"归档文件：{len(self.backup.files)} 个，合计 {sum(map(len, self.backup.files.values())) / 1024:.1f} KiB",
        ]
        if self.snapshot:
            details.extend(compare_systems(saved, self.snapshot).messages)
            if self.snapshot.activated:
                details.append("当前 Windows 已激活，无需安装备份密钥。")
        else:
            details.append("尚未取得本机信息，自动安装密钥不可用；仍可查看和导出备份。")
        details.extend(self.backup.notes)
        self.backup_details.setText("\n".join(details))
        if reset_keys:
            self.key_choice.clear()
            self.key_choice.addItem("选择备份密钥（默认不安装）", -1)
            for index, key in enumerate(saved.keys):
                self.key_choice.addItem(f"{key.label}  ·  {key.masked}", index)

    def forget_backup(self):
        self._invalidate_backup()
        self.open_password.clear()
        self.activity.setText("已关闭解密后的备份。")

    def restore_selected(self):
        if not self.backup or not self.restore_button.isEnabled():
            return
        backup, index = self.backup, self.key_choice.currentData()
        key = backup.snapshot.keys[index]
        answer = QMessageBox.question(
            self,
            "确认安装产品密钥",
            f"将把以下密钥安装到本机，并通过 Windows 尝试激活：\n{key.label}\n{key.masked}\n\n这会更改本机产品密钥。固件密钥可能属于出厂版本，备份密钥也可能无法用于激活。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run(
                "正在通过 Windows 恢复密钥并核实结果…",
                lambda p, c: self.service.restore_key(backup, index),
                self.recovery_finished,
            )

    def activate_current(self):
        answer = QMessageBox.question(
            self,
            "确认重新激活",
            "将使用本机已安装的许可证，通过 Windows 尝试激活。此操作可能需要连接微软激活服务或组织的激活服务器。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run(
                "正在重新激活并读取最终状态…",
                lambda p, c: self.service.activate_current(),
                self.recovery_finished,
            )

    def recovery_finished(self, result: RecoveryResult):
        self.show_snapshot(result.snapshot)
        self.activity.setText(
            "Windows 已确认激活。" if result.activated else "尚未确认激活成功，请查看结果。"
        )
        self.log(result.message)
        method = QMessageBox.information if result.activated else QMessageBox.warning
        method(self, "恢复结果", result.message)

    def view_key(self):
        if not self.backup:
            return
        index = self.key_choice.currentData()
        if type(index) is not int or index < 0:
            return
        key = self.backup.snapshot.keys[index]
        dialog = QDialog(self)
        dialog.setWindowTitle("备份中的产品密钥")
        dialog.resize(520, 210)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(key.label + "；可读取不代表可激活或可迁移。"))
        value = QLineEdit(key.value)
        value.setReadOnly(True)
        value.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(value)
        show = QCheckBox("显示完整密钥（注意周围环境与屏幕共享）")
        show.toggled.connect(
            lambda visible: value.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        layout.addWidget(show)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        value.clear()
        dialog.deleteLater()

    def export_files(self):
        if not self.backup:
            return
        answer = QMessageBox.question(
            self,
            "导出许可归档",
            "导出的许可文件将不再受备份密码保护，请保存在可信位置。此操作只导出资料，不将文件写回系统。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录的上级文件夹")
        if folder:
            target = Path(folder) / f"许可归档-{datetime.now():%Y%m%d-%H%M%S}"
            backup = self.backup
            self._run(
                "正在导出已校验的归档文件…",
                lambda p, c: self.service.export_files(backup, target),
                self.files_exported,
            )

    def files_exported(self, path: Path):
        self.activity.setText("归档文件已导出。")
        self.log(f"归档文件已导出到：{path}")
        QMessageBox.information(self, "导出完成", str(path))

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出操作记录", "激活备份操作记录.txt", "文本文件 (*.txt)"
        )
        if path:
            try:
                # Exclusive creation protects existing files even after a native dialog confirmation.
                with Path(path).open("x", encoding="utf-8") as stream:
                    stream.write(redact(self.logs.toPlainText()))
            except OSError:
                QMessageBox.warning(
                    self, "导出失败", "无法创建文件，请选择新的文件名并检查目录权限。"
                )

    def open_settings(self):
        if not QDesktopServices.openUrl(QUrl("ms-settings:activation")):
            QMessageBox.information(self, "打开失败", "请手动打开 Windows 设置 → 系统 → 激活。")

    def elevate(self):
        try:
            relaunch_elevated()
        except AppError as exc:
            QMessageBox.information(self, "管理员模式", str(exc))
            return
        self.close()

    def cancel(self):
        if self.task:
            self.task.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.activity.setText("正在取消，等待当前读取或校验步骤结束…")

    def closeEvent(self, event: QCloseEvent):
        if self.task is not None:
            QMessageBox.information(
                self, "操作进行中", "请等待当前操作结束；备份操作可以先点击“取消备份”。"
            )
            event.ignore()
            return
        self.backup = None
        self.snapshot = None
        event.accept()
