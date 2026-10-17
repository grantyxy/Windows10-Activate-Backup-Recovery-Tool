import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from activation_backup.archive import BackupData
from activation_backup.models import AppError, Cancelled
from activation_backup.service import ActivationService
from activation_backup.ui import MainWindow
from tests.fixtures import FAKE_KEY, FakeProvider, sample


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.messages = []
        for kind in ("information", "warning"):
            patcher = patch.object(
                QMessageBox, kind, side_effect=lambda *args: self.messages.append(args[-1])
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No)
        self.question = patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = FakeProvider()
        self.window = MainWindow(ActivationService(self.provider), auto_query=False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        if self.window.task:
            self.window.task.cancel_event.set()
            self.wait_for_task()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_for_task(self):
        deadline = time.monotonic() + 20
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window.task, "后台任务未结束")

    def test_navigation_and_real_async_refresh(self):
        QTest.mouseClick(self.window.refresh_button, Qt.MouseButton.LeftButton)
        self.wait_for_task()
        self.assertEqual(self.window.status_label.text(), "未激活")
        for page in range(4):
            self.window.navigation.setCurrentRow(page)
            self.assertEqual(self.window.pages.currentIndex(), page)

    def test_mismatched_password_never_starts_backup(self):
        self.window.destination.setText(str(Path(self.directory.name) / "x.w11ab"))
        self.window.new_password.setText("test-password-one")
        self.window.confirm_password.setText("test-password-two")
        self.window.create_backup()
        self.assertIsNone(self.window.task)
        self.assertEqual(self.provider.calls, [])
        self.assertTrue(any("不一致" in m for m in self.messages))

    def test_create_then_open_encrypted_backup_from_ui(self):
        target = Path(self.directory.name) / "中文界面备份.w11ab"
        self.window.destination.setText(str(target))
        self.window.new_password.setText("test-password-123")
        self.window.confirm_password.setText("test-password-123")
        self.window.include_store.setChecked(False)
        self.window.create_backup()
        self.wait_for_task()
        self.assertTrue(target.is_file())
        self.assertEqual(self.window.new_password.text(), "")
        self.window.source.setText(str(target))
        self.window.open_password.setText("test-password-123")
        self.window.verify_backup()
        self.wait_for_task()
        self.assertIsNotNone(self.window.backup)
        self.assertIn("完整性校验通过", self.window.backup_details.text())
        self.assertEqual(self.window.open_password.text(), "")
        self.assertFalse(self.window.restore_button.isEnabled())
        self.window.key_choice.setCurrentIndex(1)
        self.assertTrue(self.window.restore_button.isEnabled())
        self.window.restore_selected()
        self.question.assert_called_once()
        self.assertNotIn(("install", FAKE_KEY), self.provider.calls)

    def test_current_activation_and_mismatch_disable_restore(self):
        self.window.show_snapshot(sample(active=True))
        self.window.backup_opened(BackupData(sample()))
        self.window.key_choice.setCurrentIndex(1)
        self.assertFalse(self.window.restore_button.isEnabled())
        self.window.show_snapshot(sample(machine_hash="b" * 64))
        self.assertFalse(self.window.restore_button.isEnabled())
        self.assertTrue(self.window.view_key_button.isEnabled())

    def test_changing_source_invalidates_old_decrypted_backup(self):
        self.window.backup_opened(BackupData(sample()))
        self.window.source.setText("different.w11ab")
        self.assertIsNone(self.window.backup)
        self.assertEqual(self.window.key_choice.count(), 1)

    def test_failure_recovers_ui_and_redacts_key(self):
        def fail(progress, cancel):
            raise AppError("error " + FAKE_KEY)

        self.window._run("测试失败路径", fail, lambda value: None)
        self.wait_for_task()
        self.assertTrue(self.window.pages.isEnabled())
        self.assertNotIn(FAKE_KEY, self.window.logs.toPlainText())
        self.assertNotIn(FAKE_KEY, "".join(self.messages))

    def test_busy_close_waits_for_thread_and_cancel_is_clean(self):
        def work(progress, cancel):
            while not cancel():
                time.sleep(0.01)
            raise Cancelled("操作已取消")

        self.window._run("测试取消", work, lambda value: None, cancellable=True)
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.window.cancel()
        self.wait_for_task()
        self.assertTrue(self.window.pages.isEnabled())
        self.assertIn("已取消", self.window.activity.text())

    def test_default_masking(self):
        self.window.show_snapshot(sample())
        self.window.backup_opened(BackupData(sample()))
        for text in (
            self.window.key_choice.itemText(1),
            self.window.system_fields["备份密钥"].text(),
            self.window.logs.toPlainText(),
        ):
            self.assertNotIn(FAKE_KEY, text)
