import base64
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from activation_backup.models import AppError
from activation_backup.windows import WindowsProvider, run_powershell, snapshot_from_query
from tests.fixtures import FAKE_KEY, PRODUCT_ID


class WindowsAdapterTests(unittest.TestCase):
    def test_missing_optional_keys_does_not_crash_query(self):
        snapshot = snapshot_from_query(
            {
                "os_name": "Windows 11",
                "edition": "Professional",
                "build": "26100.1",
                "architecture": "64-bit",
                "products": [],
                "uuid": "",
                "machine_guid": "",
            }
        )
        self.assertEqual(snapshot.keys, ())
        self.assertFalse(snapshot.activated)
        self.assertEqual(snapshot.machine_id_source, "unknown")

    def test_machine_guid_fallback_is_identified(self):
        snapshot = snapshot_from_query(
            {"uuid": "00000000-0000-0000-0000-000000000000", "machine_guid": "fake-machine-guid"}
        )
        self.assertEqual(snapshot.machine_id_source, "machine_guid")
        self.assertEqual(len(snapshot.machine_hash), 64)

    def test_power_shell_key_uses_stdin_not_command_line(self):
        result = subprocess.CompletedProcess(
            [], 0, json.dumps({"ok": True, "data": {"code": 0}}), ""
        )
        with (
            patch("activation_backup.windows.windows_directory", return_value=Path("C:/Windows")),
            patch("activation_backup.windows.is_admin", return_value=True),
            patch("activation_backup.windows.subprocess.run", return_value=result) as run,
        ):
            WindowsProvider().install_key(FAKE_KEY)
        arguments, options = run.call_args
        self.assertNotIn(FAKE_KEY, " ".join(arguments[0]))
        self.assertFalse(options.get("shell", False))
        encoded = options["input"].split("FromBase64String('")[1].split("'")[0]
        self.assertIn(FAKE_KEY, base64.b64decode(encoded).decode())
        self.assertEqual(arguments[0][-1], "-")

    def test_shell_injection_in_key_or_id_rejected(self):
        with (
            patch("activation_backup.windows.is_admin", return_value=True),
            patch("activation_backup.windows.run_powershell") as run,
        ):
            for value in (FAKE_KEY + "'; evil", "x", ""):
                with self.subTest(value=value), self.assertRaises(AppError):
                    WindowsProvider().install_key(value)
            with self.assertRaises(AppError):
                WindowsProvider().activate(PRODUCT_ID + "'; evil")
            run.assert_not_called()

    def test_nonzero_and_missing_return_value_not_success(self):
        for response in ({"code": 0xC004F050}, {"code": None}, {}, {"code": False}):
            with self.subTest(response=response), self.assertRaises(AppError):
                WindowsProvider._check_code(response)

    def test_timeout_and_invalid_json_are_actionable(self):
        with patch("activation_backup.windows.windows_directory", return_value=Path("C:/Windows")):
            with (
                patch(
                    "activation_backup.windows.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("powershell", 60),
                ),
                self.assertRaisesRegex(AppError, "超时"),
            ):
                run_powershell("$result=@{}")
            result = subprocess.CompletedProcess([], 1, "not-json " + FAKE_KEY, FAKE_KEY)
            with (
                patch("activation_backup.windows.subprocess.run", return_value=result),
                self.assertRaises(AppError) as exc,
            ):
                run_powershell("$result=@{}")
            self.assertNotIn(FAKE_KEY, str(exc.exception))
