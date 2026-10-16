"""许可快照与边界校验；完整产品密钥不进入 repr 或诊断报告。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

KEY_PATTERN = re.compile(r"[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\Z")
KEY_IN_TEXT = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}(?![A-Z0-9])", re.I)
STATUS_LABELS = {
    0: "未激活",
    1: "已激活",
    2: "初始宽限期",
    3: "硬件变更宽限期",
    4: "非正版宽限期",
    5: "通知模式",
    6: "延长宽限期",
}
KEY_SOURCES = {"firmware": "固件 OEM 密钥", "registry": "注册表备份密钥"}


class AppError(Exception):
    """可直接向用户显示的错误。"""


class Cancelled(AppError):
    pass


def redact(text: str) -> str:
    return KEY_IN_TEXT.sub(lambda match: "*****-*****-*****-*****-" + match[0][-5:], str(text))


def _text(data: dict, name: str, limit: int = 512, *, empty: bool = True) -> str:
    value = data.get(name)
    if not isinstance(value, str) or len(value) > limit or (not empty and not value):
        raise AppError(f"备份字段无效：{name}")
    if any(ord(char) < 32 for char in value):
        raise AppError(f"备份字段包含控制字符：{name}")
    return value


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    description: str
    status: int
    partial_key: str
    grace_minutes: int = 0

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, "未知状态")

    @classmethod
    def from_dict(cls, data: Any) -> Product:
        if not isinstance(data, dict):
            raise AppError("备份中的许可证记录无效。")
        status, grace = data.get("status"), data.get("grace_minutes")
        if type(status) is not int or status not in STATUS_LABELS:
            raise AppError("备份中的激活状态无效。")
        if type(grace) is not int or not 0 <= grace <= 2**32 - 1:
            raise AppError("备份中的宽限期无效。")
        identifier = _text(data, "id", 36, empty=False)
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier):
            raise AppError("备份中的激活 ID 无效。")
        partial = _text(data, "partial_key", 5)
        if partial and not re.fullmatch(r"[A-Z0-9]{5}", partial):
            raise AppError("备份中的密钥尾号无效。")
        return cls(
            identifier, _text(data, "name"), _text(data, "description"), status, partial, grace
        )


@dataclass(frozen=True)
class KeyCandidate:
    source: str
    value: str = field(repr=False)

    @property
    def masked(self) -> str:
        return "*****-*****-*****-*****-" + self.value[-5:]

    @property
    def label(self) -> str:
        return KEY_SOURCES[self.source]

    @classmethod
    def from_dict(cls, data: Any) -> KeyCandidate:
        if not isinstance(data, dict) or data.get("source") not in KEY_SOURCES:
            raise AppError("备份中的密钥来源无效。")
        value = _text(data, "value", 29, empty=False)
        if not KEY_PATTERN.fullmatch(value):
            raise AppError("备份中的产品密钥格式无效。")
        return cls(data["source"], value)


@dataclass(frozen=True)
class Snapshot:
    captured_at: str
    os_name: str
    edition: str
    build: str
    architecture: str
    machine_hash: str
    machine_id_source: str
    products: tuple[Product, ...] = ()
    keys: tuple[KeyCandidate, ...] = field(default=(), repr=False)
    warnings: tuple[str, ...] = ()

    @property
    def primary(self) -> Product | None:
        return next(
            (p for p in self.products if p.status == 1), self.products[0] if self.products else None
        )

    @property
    def activated(self) -> bool:
        return any(p.status == 1 for p in self.products)

    def to_dict(self, *, secrets: bool = True) -> dict:
        data = asdict(self)
        if not secrets:
            data["keys"] = [{"source": k.source, "masked": k.masked} for k in self.keys]
            data["machine_hash"] = self.machine_hash[:12] if self.machine_hash else ""
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Snapshot:
        if not isinstance(data, dict):
            raise AppError("备份中的系统信息无效。")
        stamp = _text(data, "captured_at", 64, empty=False)
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise AppError("备份时间无效。") from exc
        machine_hash = _text(data, "machine_hash", 64)
        source = _text(data, "machine_id_source", 32)
        if source not in ("smbios", "machine_guid", "unknown"):
            raise AppError("设备标识来源无效。")
        if machine_hash and not re.fullmatch(r"[a-f0-9]{64}", machine_hash):
            raise AppError("设备指纹格式无效。")
        products, keys, warnings = data.get("products"), data.get("keys"), data.get("warnings")
        if not isinstance(products, (list, tuple)) or len(products) > 128:
            raise AppError("许可证数量无效。")
        if not isinstance(keys, (list, tuple)) or len(keys) > 8:
            raise AppError("密钥数量无效。")
        if not isinstance(warnings, (list, tuple)) or len(warnings) > 32:
            raise AppError("备份提示信息无效。")
        for warning in warnings:
            _text({"warning": warning}, "warning", 1024)
        result = cls(
            stamp,
            _text(data, "os_name"),
            _text(data, "edition"),
            _text(data, "build", 32),
            _text(data, "architecture", 64),
            machine_hash,
            source,
            tuple(Product.from_dict(p) for p in products),
            tuple(KeyCandidate.from_dict(k) for k in keys),
            tuple(warnings),
        )
        if len({p.id for p in result.products}) != len(result.products):
            raise AppError("备份包含重复的许可证。")
        return result


@dataclass(frozen=True)
class Compatibility:
    can_install_key: bool
    messages: tuple[str, ...]


def compare_systems(saved: Snapshot, current: Snapshot) -> Compatibility:
    reasons = []
    if not saved.machine_hash or not current.machine_hash:
        reasons.append("无法确认设备身份，请使用 Windows 激活设置进行恢复。")
    elif (
        saved.machine_id_source != current.machine_id_source
        or saved.machine_hash != current.machine_hash
    ):
        reasons.append("设备指纹不匹配；自动安装备份密钥已禁用。")
    if (
        not saved.edition
        or not current.edition
        or saved.edition.casefold() != current.edition.casefold()
    ):
        reasons.append("Windows 版本类型不匹配或未知；自动安装备份密钥已禁用。")
    allowed = not reasons
    if allowed:
        reasons.append("设备指纹与 Windows 版本类型一致。")
    if saved.build != current.build:
        reasons.append("系统构建号不同；归档文件仅供保留，不写入系统许可存储。")
    if not saved.keys:
        reasons.append("备份没有可读取的完整密钥，可尝试系统重新激活或疑难解答。")
    return Compatibility(allowed and bool(saved.keys), tuple(reasons))
