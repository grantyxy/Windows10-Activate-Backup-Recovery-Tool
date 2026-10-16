import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from activation_backup import archive
from activation_backup.archive import BackupData
from activation_backup.models import AppError
from tests.fixtures import FAKE_KEY, sample


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.backup = BackupData(
            sample(),
            {"store/tokens.dat": b"test tokens", "store/cache/cache.dat": b"cache"},
            ("测试归档",),
        )
        self.password = "test-password-2026"

    def alter_zip(self, mutate):
        source = zipfile.ZipFile(io.BytesIO(archive.encode_zip(self.backup)))
        entries = {n: source.read(n) for n in source.namelist()}
        source.close()
        mutate(entries)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return output.getvalue()

    def change_manifest(self, change):
        def mutate(entries):
            manifest = json.loads(entries["manifest.json"])
            change(manifest)
            entries["manifest.json"] = json.dumps(manifest).encode()

        return self.alter_zip(mutate)

    def test_encrypted_round_trip_preserves_nested_files(self):
        encrypted = archive.encrypt(self.backup, self.password)
        self.assertNotIn(FAKE_KEY.encode(), encrypted)
        restored = archive.decrypt(encrypted, self.password)
        self.assertEqual(restored, self.backup)

    def test_random_salt_and_nonce(self):
        self.assertNotEqual(
            archive.encrypt(self.backup, self.password), archive.encrypt(self.backup, self.password)
        )

    def test_wrong_password_is_rejected(self):
        with self.assertRaisesRegex(AppError, "密码"):
            archive.decrypt(archive.encrypt(self.backup, self.password), "wrong-password")

    def test_ciphertext_and_header_tampering_rejected(self):
        data = archive.encrypt(self.backup, self.password)
        for offset in (len(archive.MAGIC), len(data) - 1):
            with self.subTest(offset=offset), self.assertRaises(AppError):
                changed = bytearray(data)
                changed[offset] ^= 1
                archive.decrypt(bytes(changed), self.password)

    def test_short_password_rejected(self):
        for password in ("short", " " * 12):
            with self.subTest(password=password), self.assertRaises(AppError):
                archive.encrypt(self.backup, password)

    def test_legacy_and_truncated_files_rejected(self):
        for data in (b"", b"PK" * 100, archive.MAGIC + b"x" * 20):
            with self.subTest(length=len(data)), self.assertRaises(AppError):
                archive.decrypt(data, self.password)

    def test_checksum_detects_modified_file(self):
        data = self.alter_zip(lambda entries: entries.update({"store/tokens.dat": b"changed"}))
        with self.assertRaisesRegex(AppError, "完整性"):
            archive.decode_zip(data)

    def test_unlisted_and_missing_files_rejected(self):
        for mutate in (
            lambda e: e.update({"store/extra": b"x"}),
            lambda e: e.pop("store/tokens.dat"),
        ):
            with self.subTest(mutate=mutate), self.assertRaisesRegex(AppError, "清单"):
                archive.decode_zip(self.alter_zip(mutate))

    def test_path_traversal_windows_aliases_and_ads_rejected(self):
        names = [
            "../secret",
            "store/../secret",
            "store/C:/x",
            "store/a\\b",
            "store/NUL.txt",
            "store/a:stream",
            "store/a.",
            "store//x",
            "store/ x/..",
            "/store/x",
        ]
        for name in names:
            with self.subTest(name=name), self.assertRaises(AppError):
                archive.safe_member(name)

    def test_duplicate_casefold_names_rejected(self):
        data = self.alter_zip(lambda entries: entries.update({"store/TOKENS.dat": b"x"}))
        with self.assertRaisesRegex(AppError, "重复"):
            archive.decode_zip(data)

    def test_symlink_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("manifest.json", b"{}")
            info = zipfile.ZipInfo("store/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, b"../outside")
        with self.assertRaises(AppError):
            archive.decode_zip(output.getvalue())

    def test_unsupported_version_and_bad_snapshot_rejected(self):
        for change in (
            lambda m: m.update(version=999),
            lambda m: m.update(snapshot={}),
            lambda m: m.update(version=True),
        ):
            with self.subTest(change=change), self.assertRaises(AppError):
                archive.decode_zip(self.change_manifest(change))

    def test_duplicate_json_fields_rejected(self):
        data = self.alter_zip(
            lambda entries: entries.update({"manifest.json": b'{"version":1,"version":1}'})
        )
        with self.assertRaisesRegex(AppError, "重复"):
            archive.decode_zip(data)

    def test_size_limit_checked_before_decompression(self):
        from unittest.mock import patch

        data = archive.encode_zip(self.backup)
        with patch.object(archive, "MAX_FILE", 4), self.assertRaisesRegex(AppError, "大小"):
            archive.decode_zip(data)

    def test_save_is_exclusive_and_no_temp_left(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "中文备份.w11ab"
            encrypted = archive.encrypt(self.backup, self.password)
            archive.atomic_write(target, encrypted)
            self.assertEqual(archive.load(target, self.password), self.backup)
            with self.assertRaises(AppError):
                archive.atomic_write(target, b"overwrite")
            self.assertEqual(target.read_bytes(), encrypted)
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_failed_publish_cleans_temporary_file(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(archive.os, "rename", side_effect=PermissionError),
                patch.object(archive.os, "link", side_effect=PermissionError),
                self.assertRaises(AppError),
            ):
                archive.atomic_write(Path(folder) / "backup.w11ab", b"encrypted")
            self.assertEqual(list(Path(folder).iterdir()), [])
