import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from activation_backup import archive
from activation_backup.archive import BackupData
from activation_backup.models import AppError, Cancelled, compare_systems, redact
from activation_backup.service import ActivationService, read_store
from tests.fixtures import FAKE_KEY, PRODUCT_ID, FakeProvider, sample


class BackupServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = self.root / "source"
        (self.store / "cache").mkdir(parents=True)
        (self.store / "tokens.dat").write_bytes(b"tokens" * 100)
        (self.store / "cache/cache.dat").write_bytes(b"nested")
        self.service = ActivationService(FakeProvider(root=self.store))

    def test_full_backup_includes_actual_recursive_bytes(self):
        target = self.root / "new.w11ab"
        result = self.service.create_backup(target, "safe-test-password", True)
        restored = archive.load(target, "safe-test-password")
        self.assertEqual(result.file_count, 2)
        self.assertEqual(restored.files["store/cache/cache.dat"], b"nested")
        self.assertEqual((self.store / "tokens.dat").read_bytes(), b"tokens" * 100)

    def test_metadata_only_never_reads_store(self):
        with patch.object(self.service.provider, "store_path", side_effect=AssertionError):
            result = self.service.create_backup(
                self.root / "meta.w11ab", "safe-test-password", False
            )
        self.assertEqual(result.file_count, 0)
        self.assertIn("不包含", archive.load(result.path, "safe-test-password").notes[0])

    def test_cancelled_backup_leaves_no_output(self):
        target = self.root / "cancel.w11ab"
        with self.assertRaises(Cancelled):
            self.service.create_backup(target, "safe-test-password", True, cancel=lambda: True)
        self.assertFalse(target.exists())

    def test_cancellation_after_encryption_leaves_no_output(self):
        state = {"cancel": False}

        def progress(percent, text):
            if percent >= 70:
                state["cancel"] = True

        target = self.root / "cancel.w11ab"
        with self.assertRaises(Cancelled):
            self.service.create_backup(
                target, "safe-test-password", True, progress, lambda: state["cancel"]
            )
        self.assertFalse(target.exists())

    def test_missing_tokens_is_not_reported_as_full_backup(self):
        (self.store / "tokens.dat").unlink()
        with self.assertRaisesRegex(AppError, "tokens.dat"):
            self.service.create_backup(self.root / "bad.w11ab", "safe-test-password", True)
        self.assertFalse((self.root / "bad.w11ab").exists())

    def test_locked_store_fails_without_publishing(self):
        with (
            patch("activation_backup.service._inventory", side_effect=PermissionError),
            self.assertRaisesRegex(AppError, "未生成"),
        ):
            self.service.create_backup(self.root / "bad.w11ab", "safe-test-password", True)
        self.assertFalse((self.root / "bad.w11ab").exists())

    def test_changing_store_is_retried_and_rejected(self):
        from activation_backup.service import _inventory

        before = _inventory(self.store)
        after = {k: (v[0], v[1] + 1, v[2]) for k, v in before.items()}
        with (
            patch(
                "activation_backup.service._inventory", side_effect=[before, after, before, after]
            ) as inventory,
            self.assertRaisesRegex(AppError, "持续变化"),
        ):
            read_store(self.store)
        self.assertEqual(inventory.call_count, 4)

    def test_export_preserves_files_and_omits_full_keys(self):
        backup = BackupData(sample(), read_store(self.store))
        target = self.service.export_files(backup, self.root / "export")
        self.assertEqual((target / "store/cache/cache.dat").read_bytes(), b"nested")
        self.assertNotIn(FAKE_KEY, (target / "备份说明.json").read_text(encoding="utf-8"))
        with self.assertRaises(AppError):
            self.service.export_files(backup, target)

    def test_files_growing_after_inventory_cannot_exceed_memory_limit(self):
        small_inventory = {"store/tokens.dat": (1, 0, 0)}
        with (
            patch("activation_backup.service._inventory", return_value=small_inventory),
            patch("activation_backup.service.MAX_TOTAL", 100),
            self.assertRaisesRegex(AppError, "总容量"),
        ):
            read_store(self.store)


class RecoveryTests(unittest.TestCase):
    def test_different_device_or_edition_prevents_any_write(self):
        for current in (
            sample(machine_hash="b" * 64),
            sample(edition="Core"),
            sample(machine_hash=""),
        ):
            provider = FakeProvider([current])
            with self.subTest(current=current.edition), self.assertRaises(AppError):
                ActivationService(provider).restore_key(BackupData(sample()), 0)
            self.assertEqual(provider.calls, ["query"])

    def test_activated_system_does_not_install_key(self):
        provider = FakeProvider([sample(active=True)])
        with self.assertRaisesRegex(AppError, "已激活"):
            ActivationService(provider).restore_key(BackupData(sample()), 0)
        self.assertEqual(provider.calls, ["query"])

    def test_admin_failure_prevents_install(self):
        provider = FakeProvider()
        provider.admin_error = AppError("需要管理员")
        with self.assertRaises(AppError):
            ActivationService(provider).restore_key(BackupData(sample()), 0)
        self.assertNotIn(("install", FAKE_KEY), provider.calls)

    def test_restore_targets_new_product_and_verifies_status(self):
        provider = FakeProvider([sample(), sample(), sample(active=True)])
        result = ActivationService(provider).restore_key(BackupData(sample()), 0)
        self.assertTrue(result.activated)
        self.assertEqual(
            provider.calls,
            ["query", "admin", ("install", FAKE_KEY), "query", ("activate", PRODUCT_ID), "query"],
        )

    def test_activation_command_success_without_status_is_not_success(self):
        provider = FakeProvider()
        result = ActivationService(provider).restore_key(BackupData(sample()), 0)
        self.assertFalse(result.activated)
        self.assertIn("尚未确认", result.message)
        self.assertIn("密钥已安装", result.message)

    def test_install_failure_is_redacted_and_never_activates(self):
        provider = FakeProvider()
        provider.install_error = AppError("失败：" + FAKE_KEY)
        with self.assertRaises(AppError) as error:
            ActivationService(provider).restore_key(BackupData(sample()), 0)
        self.assertNotIn(FAKE_KEY, str(error.exception))
        self.assertNotIn(("activate", PRODUCT_ID), provider.calls)

    def test_query_failure_after_install_reports_partial_change(self):
        provider = FakeProvider([sample(), AppError("WMI 故障")])
        with self.assertRaisesRegex(AppError, "密钥已安装"):
            ActivationService(provider).restore_key(BackupData(sample()), 0)

    def test_no_matching_product_never_activates_other_license(self):
        provider = FakeProvider([sample(), sample(products=())])
        with self.assertRaisesRegex(AppError, "无法唯一确认"):
            ActivationService(provider).restore_key(BackupData(sample()), 0)
        self.assertNotIn(("activate", PRODUCT_ID), provider.calls)

    def test_reactivation_of_active_system_is_read_only(self):
        provider = FakeProvider([sample(active=True)])
        self.assertTrue(ActivationService(provider).activate_current().activated)
        self.assertEqual(provider.calls, ["query"])

    def test_activation_error_still_reads_final_status(self):
        provider = FakeProvider([sample(), sample()])
        provider.activation_error = AppError("网络连接失败")
        result = ActivationService(provider).activate_current()
        self.assertFalse(result.activated)
        self.assertIn("网络连接失败", result.message)
        self.assertEqual(provider.calls[-1], "query")

    def test_full_key_never_in_repr_or_public_report(self):
        import json

        value = sample()
        self.assertNotIn(FAKE_KEY, repr(value))
        self.assertNotIn(FAKE_KEY, json.dumps(value.to_dict(secrets=False)))
        self.assertNotIn(FAKE_KEY, redact("error " + FAKE_KEY))

    def test_build_change_does_not_imply_edition_change(self):
        result = compare_systems(sample(), sample(build="26200.9999"))
        self.assertTrue(result.can_install_key)
        self.assertIn("构建号不同", "".join(result.messages))
