"""有版本的加密归档：先认证、再限量解析、逐文件校验、不覆盖目标。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .models import AppError, Snapshot

MAGIC = b"W11ABK\x01"
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_ARCHIVE = 80 * 1024 * 1024
MAX_FILES = 512
MAX_MANIFEST = 1024 * 1024
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class BackupData:
    snapshot: Snapshot
    files: dict[str, bytes] = field(default_factory=dict, repr=False)
    notes: tuple[str, ...] = ()


def safe_member(name: str) -> str:
    if not isinstance(name, str) or len(name) > 240 or not name.startswith("store/"):
        raise AppError("归档包含无效的文件路径。")
    parts = name.split("/")
    if len(parts) < 2 or len(parts) > 16:
        raise AppError("归档文件目录层级无效。")
    for part in parts:
        if (
            not part
            or part in (".", "..")
            or part.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"\\|?*' for c in part)
            or part.split(".")[0].upper() in RESERVED
        ):
            raise AppError("归档包含不安全的文件路径。")
    if str(PurePosixPath(name)) != name:
        raise AppError("归档文件路径不规范。")
    return name


def encode_zip(backup: BackupData) -> bytes:
    snapshot = Snapshot.from_dict(backup.snapshot.to_dict())
    if len(backup.files) > MAX_FILES or sum(len(v) for v in backup.files.values()) > MAX_TOTAL:
        raise AppError("许可存储超出归档容量限制（512 个文件 / 64 MiB）。")
    manifest_files = {}
    folded = set()
    for name, value in backup.files.items():
        safe_member(name)
        if name.casefold() in folded or not isinstance(value, bytes) or len(value) > MAX_FILE:
            raise AppError("备份文件重复或超过单文件容量限制。")
        folded.add(name.casefold())
        manifest_files[name] = {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    manifest = {
        "format": "windows-activation-backup",
        "version": 1,
        "snapshot": snapshot.to_dict(),
        "files": manifest_files,
        "notes": list(backup.notes),
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST:
        raise AppError("备份清单超出容量限制。")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        for name, value in backup.files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AppError("归档清单包含重复字段。")
        result[key] = value
    return result


def decode_zip(payload: bytes) -> BackupData:
    if len(payload) > MAX_ARCHIVE:
        raise AppError("归档过大。")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_FILES + 1:
                raise AppError("归档条目数量无效。")
            names = [entry.filename for entry in entries]
            if len({name.casefold() for name in names}) != len(names):
                raise AppError("归档包含重复文件。")
            if "manifest.json" not in names:
                raise AppError("归档缺少校验清单；旧版 ZIP 不能作为完整恢复备份。")
            if sum(entry.file_size for entry in entries) > MAX_TOTAL + MAX_MANIFEST:
                raise AppError("归档解压后的数据量超出限制。")
            for entry in entries:
                if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise AppError("归档包含不支持的条目类型。")
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise AppError("归档压缩方式不受支持。")
                limit = MAX_MANIFEST if entry.filename == "manifest.json" else MAX_FILE
                if entry.file_size > limit:
                    raise AppError("归档文件大小超出限制。")
                if entry.filename != "manifest.json":
                    safe_member(entry.filename)
            manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_object)
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != "windows-activation-backup"
            ):
                raise AppError("不是本工具支持的备份格式。")
            if type(manifest.get("version")) is not int or manifest["version"] != 1:
                raise AppError("此备份版本不受支持，请使用创建备份时的版本或更新版本。")
            records = manifest.get("files")
            if not isinstance(records, dict) or set(records) != set(names) - {"manifest.json"}:
                raise AppError("归档文件与清单不一致。")
            notes = manifest.get("notes")
            if (
                not isinstance(notes, list)
                or len(notes) > 32
                or any(not isinstance(n, str) or len(n) > 1024 for n in notes)
            ):
                raise AppError("归档备注格式无效。")
            files = {}
            for name, record in records.items():
                if (
                    not isinstance(record, dict)
                    or type(record.get("size")) is not int
                    or not isinstance(record.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                ):
                    raise AppError("归档文件校验信息无效。")
                content = archive.read(name)
                if (
                    len(content) != record["size"]
                    or hashlib.sha256(content).hexdigest() != record["sha256"]
                ):
                    raise AppError("归档完整性校验失败，文件可能损坏。")
                files[name] = content
            return BackupData(Snapshot.from_dict(manifest.get("snapshot")), files, tuple(notes))
    except (
        zipfile.BadZipFile,
        ValueError,
        UnicodeError,
        RuntimeError,
        NotImplementedError,
        RecursionError,
        EOFError,
    ) as exc:
        raise AppError("归档损坏或格式无效。") from exc


def _derive(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        raise AppError("请输入备份密码（最长 1024 个字符）。")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def encrypt(backup: BackupData, password: str) -> bytes:
    if len(password) < 10 or not password.strip():
        raise AppError("备份密码至少需要 10 个字符，请妥善保管，遗失后无法恢复。")
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    return header + AESGCM(_derive(password, salt)).encrypt(nonce, encode_zip(backup), header)


def decrypt(data: bytes, password: str) -> BackupData:
    if len(data) > MAX_ARCHIVE or len(data) < len(MAGIC) + 28 + 16:
        raise AppError("备份文件大小无效或文件不完整。")
    if not data.startswith(MAGIC):
        raise AppError("请选择本工具的 .w11ab 加密备份；旧版 ZIP 不能作为完整恢复备份。")
    offset = len(MAGIC)
    salt, nonce = data[offset : offset + 16], data[offset + 16 : offset + 28]
    header = data[: offset + 28]
    try:
        payload = AESGCM(_derive(password, salt)).decrypt(nonce, data[len(header) :], header)
    except InvalidTag as exc:
        raise AppError("密码不正确，或备份文件已损坏 / 被修改。") from exc
    return decode_zip(payload)


def load(path: Path, password: str) -> BackupData:
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_ARCHIVE + 1)
        return decrypt(data, password)
    except OSError as exc:
        raise AppError("无法读取备份文件，请检查位置和读取权限。") from exc


def atomic_write(path: Path, data: bytes) -> None:
    """只写同目录加密临时文件；完整写入后原子发布；绝不覆盖既有文件。"""
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise AppError("目标文件已存在，请选择新文件名，原备份不会被覆盖。")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".activation-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(temporary, path)  # Windows rename fails if the destination exists.
        else:
            os.link(temporary, path)  # Exclusive publication, including concurrent writers.
    except OSError as exc:
        raise AppError("无法保存文件；请检查目标是否已存在、目录权限及剩余空间。") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
