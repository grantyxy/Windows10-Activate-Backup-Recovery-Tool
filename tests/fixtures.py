"""所有密钥、设备标识均为合成数据；测试不会调用真实恢复接口。"""

from dataclasses import replace

from activation_backup.models import KeyCandidate, Product, Snapshot

FAKE_KEY = "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
PRODUCT_ID = "12345678-1234-1234-1234-123456789012"


def sample(*, active: bool = False, **changes) -> Snapshot:
    snapshot = Snapshot(
        "2026-10-17T00:00:00+00:00",
        "Windows 11 专业版",
        "Professional",
        "26100.1",
        "64 位",
        "a" * 64,
        "smbios",
        (
            Product(
                PRODUCT_ID,
                "Windows Professional",
                "Windows Operating System, RETAIL channel",
                int(active),
                "EEEEE",
            ),
        ),
        (KeyCandidate("registry", FAKE_KEY),),
        (),
    )
    return replace(snapshot, **changes)


class FakeProvider:
    def __init__(self, snapshots=None, root=None):
        self.snapshots = list(snapshots or [sample()])
        self.root = root
        self.calls = []
        self.activation_error = None
        self.install_error = None
        self.admin_error = None

    def snapshot(self):
        self.calls.append("query")
        value = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        if isinstance(value, Exception):
            raise value
        return value

    def store_path(self):
        return self.root

    def require_admin(self):
        self.calls.append("admin")
        if self.admin_error:
            raise self.admin_error

    def install_key(self, key):
        self.calls.append(("install", key))
        if self.install_error:
            raise self.install_error

    def activate(self, product_id):
        self.calls.append(("activate", product_id))
        if self.activation_error:
            raise self.activation_error
