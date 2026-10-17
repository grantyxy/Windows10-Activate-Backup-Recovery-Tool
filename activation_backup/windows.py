"""Windows CIM 许可接口。查询不改系统；密钥仅通过子进程标准输入传递。"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .models import KEY_PATTERN, AppError, KeyCandidate, Product, Snapshot, redact

WINDOWS_APP_ID = "55c92734-d682-4d71-983e-d6ec3f16059f"
QUERY = r"""
$os = Get-CimInstance -ClassName Win32_OperatingSystem
if ($os.ProductType -ne 1 -or [int]$os.BuildNumber -lt 10240) { throw 'Unsupported Windows edition' }
$reg = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$items = @(Get-CimInstance -ClassName SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f'" | Where-Object { $_.PartialProductKey -and -not $_.LicenseIsAddon })
$products = @($items | ForEach-Object {
    @{ id=[string]$_.ID; name=[string]$_.Name; description=[string]$_.Description;
       status=[int]$_.LicenseStatus; partial_key=[string]$_.PartialProductKey;
       grace_minutes=[long]$_.GracePeriodRemaining }
})
$warnings = @()
$firmware = ''
try { $firmware = [string](Get-CimInstance SoftwareLicensingService).OA3xOriginalProductKey }
catch { $warnings += 'firmware_unavailable' }
$registryKey = ''
try { $registryKey = [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SoftwareProtectionPlatform').BackupProductKeyDefault }
catch { $warnings += 'registry_key_unavailable' }
$uuid = ''
try { $uuid = [string](Get-CimInstance Win32_ComputerSystemProduct).UUID }
catch { $warnings += 'hardware_id_unavailable' }
$guid = ''
try { $guid = [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid }
catch { $warnings += 'machine_guid_unavailable' }
$result = @{ os_name=[string]$os.Caption; edition=[string]$reg.EditionID;
    build=([string]$os.BuildNumber + '.' + [string]$reg.UBR);
    architecture=[string]$os.OSArchitecture; uuid=$uuid; machine_guid=$guid;
    firmware=$firmware; registry=$registryKey; products=$products; warnings=$warnings }
"""


def windows_directory() -> Path:
    if os.name != "nt":
        raise AppError("此功能需要 Windows 10 / 11。")
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer)):
        raise AppError("无法确定 Windows 系统目录。")
    return Path(buffer.value)


def is_admin() -> bool:
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def relaunch_elevated() -> None:
    if os.name != "nt":
        raise AppError("管理员模式仅适用于 Windows。")
    executable = sys.executable
    arguments = ""
    if not getattr(sys, "frozen", False):
        launcher = Path(__file__).resolve().parent.parent / "launch.pyw"
        pythonw = Path(executable).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(executable))
        arguments = subprocess.list2cmdline([str(launcher)])
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    shell_execute.restype = ctypes.c_void_p
    result = shell_execute(None, "runas", executable, arguments, str(Path(executable).parent), 1)
    if not result or result <= 32:
        raise AppError("未获得管理员权限。你可以继续查看和校验备份。")


def run_powershell(script: str, timeout: int = 60) -> dict:
    if sys.maxsize <= 2**32:
        raise AppError("请使用 64 位 Python 或本项目的 64 位便携版。")
    executable = windows_directory() / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # No shell=True, command-line secrets, profiles or temporary plaintext scripts.
    wrapper = (
        "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
        "try { " + script + "\n; @{ok=$true; data=$result} | ConvertTo-Json -Depth 12 -Compress "
        "} catch { @{ok=$false; error=$_.Exception.Message; "
        "hresult=('0x{0:X8}' -f ($_.Exception.HResult -band 0xffffffffL))} | "
        "ConvertTo-Json -Compress }"
    )
    # A single stdin expression avoids interactive multiline parsing. The payload,
    # including any key, remains on stdin, never on the process command line.
    payload = base64.b64encode(wrapper.encode("utf-8")).decode("ascii")
    wrapper = (
        "& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + payload
        + "'))))\n"
    )
    try:
        completed = subprocess.run(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=wrapper,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            "Windows 许可接口超时。若刚执行恢复，请刷新激活状态后再决定是否重试。"
        ) from exc
    except OSError as exc:
        raise AppError("无法运行 Windows PowerShell，请检查系统组件和应用执行策略。") from exc
    try:
        result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
    except (ValueError, TypeError) as exc:
        # Do not echo stdout/stderr: they may include a script or complete key.
        raise AppError(
            "Windows 接口未返回有效数据，请检查 PowerShell / WMI 是否被系统策略禁用。"
        ) from exc
    if not isinstance(result, dict) or result.get("ok") is not True or completed.returncode:
        detail = (
            redact(str(result.get("error", "系统查询失败")))
            if isinstance(result, dict)
            else "系统查询失败"
        )
        raise AppError(f"Windows 许可接口失败：{detail[:500]}")
    if not isinstance(result.get("data"), dict):
        raise AppError("Windows 接口返回了无法识别的数据。")
    return result["data"]


def snapshot_from_query(data: dict) -> Snapshot:
    warnings = []
    if data.get("warnings"):
        warnings.append("部分可选信息未能读取；可用信息仍会备份。")
    identifier = str(data.get("uuid", "")).strip().lower()
    source = "smbios"
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identifier) or set(
        identifier.replace("-", "")
    ) in ({"0"}, {"f"}):
        identifier, source = str(data.get("machine_guid", "")).strip().lower(), "machine_guid"
        warnings.append("未获取可靠的硬件 UUID，设备标识可能在重装后改变。")
    if not identifier:
        source = "unknown"
    fingerprint = (
        hashlib.sha256((source + ":" + identifier).encode()).hexdigest() if identifier else ""
    )
    keys = []
    for key_source in ("firmware", "registry"):
        value = str(data.get(key_source) or "").strip().upper()
        if KEY_PATTERN.fullmatch(value):
            keys.append(KeyCandidate(key_source, value))
        elif value:
            warnings.append("一项密钥数据格式异常，未收录该项。")
    if not keys:
        warnings.append("未读取到完整产品密钥；数字许可证不一定提供可导出的独立密钥。")
    if any(k.source == "registry" for k in keys):
        warnings.append("注册表备份密钥可能是通用安装密钥，不能据此确认拥有可迁移许可证。")
    products = tuple(Product.from_dict(p) for p in (data.get("products") or []))
    if not products:
        warnings.append("没有查询到带密钥的 Windows 基础许可证记录，激活状态暂无法确认。")
    result = Snapshot(
        datetime.now(UTC).isoformat(),
        str(data.get("os_name", "")),
        str(data.get("edition", "")),
        str(data.get("build", "")),
        str(data.get("architecture", "")),
        fingerprint,
        source,
        products,
        tuple(keys),
        tuple(warnings),
    )
    return Snapshot.from_dict(result.to_dict())


class WindowsProvider:
    def snapshot(self) -> Snapshot:
        return snapshot_from_query(run_powershell(QUERY))

    def store_path(self) -> Path:
        return windows_directory() / "System32/spp/store/2.0"

    def require_admin(self) -> None:
        if not is_admin():
            raise AppError("此操作需要管理员权限，请点击“以管理员身份重新打开”。")

    def install_key(self, key: str) -> None:
        self.require_admin()
        if not KEY_PATTERN.fullmatch(key):
            raise AppError("产品密钥格式无效。")
        response = run_powershell(
            "$service=Get-CimInstance SoftwareLicensingService; "
            "$r=Invoke-CimMethod -InputObject $service -MethodName InstallProductKey "
            "-Arguments @{ProductKey='" + key + "'}; $result=@{code=$r.ReturnValue}",
            120,
        )
        self._check_code(response)

    def activate(self, product_id: str) -> None:
        self.require_admin()
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", product_id):
            raise AppError("激活 ID 无效。")
        response = run_powershell(
            "$p=Get-CimInstance SoftwareLicensingProduct -Filter \"ApplicationID='"
            + WINDOWS_APP_ID
            + "' AND ID='"
            + product_id
            + "'\"; "
            "if (-not $p) { throw 'Windows license not found' }; "
            "$r=Invoke-CimMethod -InputObject $p -MethodName Activate; "
            "$result=@{code=$r.ReturnValue}",
            120,
        )
        self._check_code(response)

    @staticmethod
    def _check_code(response: dict) -> None:
        code = response.get("code")
        if type(code) is not int or code != 0:
            label = f"0x{code & 0xFFFFFFFF:08X}" if type(code) is int else "未知返回值"
            raise AppError(f"Windows 未确认操作成功（{label}）。请在系统激活页面查看详细原因。")
