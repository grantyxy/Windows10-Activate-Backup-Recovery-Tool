"""备份 / 校验 / 恢复流程；通过 provider 隔离真实 Windows 操作与测试。"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import archive
from .archive import MAX_FILE, MAX_FILES, MAX_TOTAL, BackupData
from .models import AppError, Cancelled, Snapshot, compare_systems, redact
from .windows import WindowsProvider

Progress = Callable[[int, str], None]


def _ignore_progress(percent: int, text: str) -> None:
    pass


def _never_cancel() -> bool:
    return False


def _check_cancel(cancel: Callable[[], bool]) -> None:
    if cancel():
        raise Cancelled("操作已取消，未生成新的备份文件。")


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inventory(root: Path) -> dict[str, tuple[int, int, int]]:
    if _is_link(root.lstat()) or not root.is_dir():
        raise AppError("许可存储路径不是普通目录。")
    result = {}
    total = 0

    def fail(error: OSError) -> None:
        raise error

    for directory, folders, filenames in os.walk(root, followlinks=False, onerror=fail):
        for name in folders + filenames:
            path = Path(directory) / name
            info = path.lstat()
            if _is_link(info):
                raise AppError("许可存储包含链接或重解析点，已停止备份。")
            if name in filenames:
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                    raise AppError("许可存储中存在不支持的文件或过大的文件。")
                member = archive.safe_member("store/" + path.relative_to(root).as_posix())
                result[member] = (info.st_size, info.st_mtime_ns, info.st_ino)
                total += info.st_size
                if len(result) > MAX_FILES or total > MAX_TOTAL:
                    raise AppError("许可存储超出备份容量限制（512 个文件 / 64 MiB）。")
    if "store/tokens.dat" not in {name.casefold() for name in result}:
        raise AppError("许可存储缺少 tokens.dat，不能标记为包含许可存储的备份。")
    return result


def read_store(
    root: Path, progress: Progress = _ignore_progress, cancel: Callable[[], bool] = _never_cancel
) -> dict[str, bytes]:
    """前后比较整个目录；文件变动时重试一次，不停止服务或改写权限。"""
    try:
        for _attempt in range(2):
            before = _inventory(root)
            files = {}
            total_read = 0
            for index, member in enumerate(sorted(before)):
                _check_cancel(cancel)
                path = root.joinpath(*member.split("/")[1:])
                if _is_link(path.lstat()):
                    raise AppError("读取时检测到许可存储文件被替换为链接。")
                with path.open("rb") as stream:
                    content = stream.read(MAX_FILE + 1)
                if len(content) > MAX_FILE:
                    raise AppError("读取过程中许可文件超出容量限制。")
                total_read += len(content)
                if total_read > MAX_TOTAL:
                    raise AppError("读取过程中许可存储超出总容量限制。")
                files[member] = content
                progress(20 + int(40 * (index + 1) / len(before)), "正在读取许可存储文件…")
            after = _inventory(root)
            if before == after and all(len(files[name]) == before[name][0] for name in files):
                return files
            progress(20, "许可文件发生变化，正在重新读取…")
    except PermissionError as exc:
        raise AppError(
            "无法读取全部许可存储文件。请以管理员身份重新打开，或取消“包含许可存储文件”后仅备份许可信息；本次未生成备份。"
        ) from exc
    except OSError as exc:
        raise AppError(
            "许可存储文件无法读取或正被占用。请稍后重试，或仅备份许可信息；本次未生成备份。"
        ) from exc
    raise AppError("许可存储在读取期间持续变化。请稍后重试；本次未生成备份。")


@dataclass(frozen=True)
class BackupResult:
    path: Path
    snapshot: Snapshot
    file_count: int


@dataclass(frozen=True)
class RecoveryResult:
    activated: bool
    message: str
    snapshot: Snapshot


class ActivationService:
    def __init__(self, provider=None):
        self.provider = provider if provider is not None else WindowsProvider()

    def query(self) -> Snapshot:
        return self.provider.snapshot()

    def create_backup(
        self,
        path: Path,
        password: str,
        include_store: bool,
        progress: Progress = _ignore_progress,
        cancel: Callable[[], bool] = _never_cancel,
    ) -> BackupResult:
        if len(password) < 10 or not password.strip():
            raise AppError("备份密码至少需要 10 个字符。")
        if Path(path).exists():
            raise AppError("目标文件已存在，请选择新的备份文件名。")
        _check_cancel(cancel)
        progress(5, "正在读取 Windows 许可信息…")
        snapshot = self.query()
        _check_cancel(cancel)
        files = read_store(self.provider.store_path(), progress, cancel) if include_store else {}
        notes = (
            ("许可存储为运行中系统的尽力快照；未使用卷影副本，不保证可直接还原。",)
            if include_store
            else ("本备份仅包含许可信息与可读取的密钥，不包含许可存储文件。",)
        )
        backup = BackupData(snapshot, files, notes)
        progress(70, "正在加密备份…")
        data = archive.encrypt(backup, password)
        _check_cancel(cancel)
        progress(85, "正在复验加密数据与文件校验值…")
        verified = archive.decrypt(data, password)
        if verified.snapshot != snapshot or verified.files != files:
            raise AppError("写入前复验失败，未保存备份。")
        _check_cancel(cancel)
        archive.atomic_write(path, data)
        progress(100, "备份已保存并通过完整性校验。")
        return BackupResult(Path(path), snapshot, len(files))

    def open_backup(self, path: Path, password: str) -> BackupData:
        return archive.load(path, password)

    def activate_current(self) -> RecoveryResult:
        current = self.query()
        if current.activated:
            return RecoveryResult(True, "当前 Windows 已激活，无需再次激活。", current)
        if not current.primary:
            raise AppError("没有可重新激活的 Windows 许可证，请打开系统激活设置。")
        self.provider.require_admin()
        return self._activate_and_verify(current.primary.id, installed=False)

    def restore_key(self, backup: BackupData, index: int) -> RecoveryResult:
        saved = Snapshot.from_dict(backup.snapshot.to_dict())
        current = self.query()  # Recheck at execution time, not just at preview time.
        if current.activated:
            raise AppError("当前 Windows 已激活，无需恢复备份密钥。")
        compatibility = compare_systems(saved, current)
        if not compatibility.can_install_key:
            raise AppError("\n".join(compatibility.messages))
        if type(index) is not int or not 0 <= index < len(saved.keys):
            raise AppError("请选择一项有效的备份密钥。")
        self.provider.require_admin()
        key = saved.keys[index]
        try:
            self.provider.install_key(key.value)
        except AppError as exc:
            raise AppError(
                "密钥安装未得到成功确认。请刷新系统状态后再操作。\n" + redact(str(exc))
            ) from exc
        try:
            after_install = self.query()
            matches = [p for p in after_install.products if p.partial_key == key.value[-5:]]
            if len(matches) != 1:
                raise AppError("无法唯一确认刚安装密钥对应的 Windows 许可证。")
            return self._activate_and_verify(matches[0].id, installed=True)
        except AppError as exc:
            raise AppError(
                "密钥已安装，但后续恢复未完成；请打开系统激活设置检查。\n" + redact(str(exc))
            ) from exc

    def _activate_and_verify(self, product_id: str, *, installed: bool) -> RecoveryResult:
        activation_error = ""
        try:
            self.provider.activate(product_id)
        except AppError as exc:
            activation_error = redact(str(exc))
        try:
            after = self.query()
        except AppError as exc:
            raise AppError("已尝试激活，但无法读取最终状态；请在 Windows 设置中核实。") from exc
        target = next((p for p in after.products if p.id == product_id), None)
        activated = target is not None and target.status == 1
        if activated:
            message = "Windows 已确认目标许可证激活成功。"
        else:
            message = (
                "产品密钥已安装；" if installed else ""
            ) + "Windows 尚未确认激活成功。请检查网络、版本及许可证有效性，或使用系统疑难解答。"
            if activation_error:
                message += "\n" + activation_error
        return RecoveryResult(activated, message, after)

    def export_files(self, backup: BackupData, destination: Path) -> Path:
        if not backup.files:
            raise AppError("此备份没有许可存储文件可导出。")
        # Revalidate even for direct API callers. Never extract ZIP paths into System32.
        checked = archive.decode_zip(archive.encode_zip(backup))
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise AppError("导出目录已存在，请选择一个新目录。")
        temporary = Path(tempfile.mkdtemp(prefix=".activation-export-", dir=destination.parent))
        try:
            for member, content in checked.files.items():
                target = temporary.joinpath(*member.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            report = {"snapshot": checked.snapshot.to_dict(secrets=False), "notes": checked.notes}
            (temporary / "备份说明.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if destination.exists():
                raise AppError("导出目录已存在，请选择新目录。")
            os.rename(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return destination
