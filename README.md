# Windows 11 激活备份与恢复助手

基于 PyQt6 的 Windows 桌面工具。由 [grantyxy 的原仓库](https://github.com/grantyxy/Windows10-Activate-Backup-Recovery-Tool) 继续开发，保留 GPL-3.0 许可证和原脚本入口。

工具用于备份本机已有的许可资料，并通过 Windows 自带许可接口尝试恢复激活。它不会生成许可证，也不绕过激活。

## 能做什么

- 查询 Windows 版本、系统构建、许可状态、许可通道和密钥尾号。使用 CIM，不依赖已被弃用的 `wmic.exe`。
- 读取固件 OEM 密钥和注册表 `BackupProductKeyDefault`（存在且可读取时），默认只显示尾号。
- 可选递归备份系统目录下 `spp/store/2.0` 的文件；任何文件读取失败都不会生成标称完整的备份。
- 使用密码加密整个 `.w11ab` 归档；采用 AES-256-GCM、随机盐、随机 nonce 和 scrypt 密钥派生。
- 保存前解密复验，打开时检查每个文件的大小和 SHA-256，并阻止不安全路径、重复条目和超限归档。
- 对比设备指纹与 Windows 版本类型。条件匹配、系统尚未激活、用户明确选择密钥并确认后，调用 Windows 安装密钥、尝试激活，再读取最终状态。
- 单独尝试系统重新激活，或打开 Windows 激活设置 / 疑难解答。
- 将归档文件导出到新目录，便于资料保留；导出说明不包含完整密钥。
- 后台执行耗时操作，备份可取消；异常会显示中文提示，操作记录会遮蔽完整产品密钥。

## 恢复能力的准确边界

**备份资料完整，不等于能够离线还原数字许可证。** Windows 的数字许可证与设备硬件相关联。相同设备重装时还需使用匹配的 Windows 版本；硬件变更可能需要账号关联及激活疑难解答。详见[微软的重新激活说明](https://support.microsoft.com/zh-cn/windows/activation/reactivating-windows-after-a-hardware-change)。

注册表备份密钥可能是通用安装密钥，固件密钥也可能属于出厂时的另一版本。程序不能仅凭“读到了完整密钥”判断其可用性、购买来源或是否可以迁移。界面不会把未读取到产品密钥等同于没有数字许可证，也不会声称已经核实账号关联。

本工具**不覆盖系统 `tokens.dat` / `data.dat`，不停止许可服务，不修改系统目录权限**。许可存储只是运行中系统的尽力快照：程序检查读前读后的文件清单、大小和修改时间，并在变化时重试一次，但没有使用卷影副本，不能保证数据库事务一致性。文件的导出不是系统激活恢复。

安装产品密钥可能改变本机状态。若安装成功而激活失败，程序会明确报告“密钥已安装，但尚未确认激活”，不会把命令执行结束当成恢复成功，也不会自动卸载密钥或进行可能破坏现状的回滚。最终状态以 Windows 返回的目标许可证 `LicenseStatus` 为准。

## 直接运行便携版

1. 将便携版 ZIP **完整解压**到可写目录，保留 `WindowsActivationBackup.exe` 与 `_internal` 的相对位置。
2. 双击 `WindowsActivationBackup.exe`。普通查询、打开备份不主动请求管理员权限。
3. 在“概览”中核对状态。需要读取受保护文件或恢复时，可以点击“以管理员身份重新打开”，并在系统 UAC 提示中自行确认。
4. 程序没有代码签名；首次启动时系统可能显示来源提示。

便携版已包含 Python、Qt 及构建工具收集的运行库，无需另装 Python。若程序依赖加载失败，会显示原生中文提示；可先检查是否完整解压，再按提示从[微软官方下载页](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)安装或修复 Visual C++ v14 x64 运行库。构建配置会隔离 DLL 搜索路径，避免将其他工具的同名系统库误带入发行包。

### 创建备份

选择“创建备份” → 选择新文件位置 → 设置并确认至少 10 个字符的密码 → 点击“加密并创建备份”。

默认勾选“包含许可存储文件”。如果文件受保护或被占用，程序会停止并说明原因；可以取得管理员权限后重试，也可以取消该选项，创建明确标注为“仅许可信息”的备份。程序不覆盖已有文件，不删除原脚本曾使用的桌面 `2.0` 目录。

备份完成后，把 `.w11ab` 复制到另一块磁盘，并妥善保存密码。**密码丢失无法恢复。** 完整密钥不会写入明文临时文件；加密与解密数据会存在进程内存中，关闭备份会释放应用引用，但不承诺对 Python / Qt 的内存副本进行可靠擦除。

### 校验与恢复

1. 打开“校验与恢复”，选择备份并输入密码，点击“解密并校验”。
2. 查看文件数量、来源系统和设备匹配结果。仅校验备份不会修改系统。
3. 如果 Windows 已激活，程序禁用密钥恢复，避免无意义地更换现有密钥。
4. 如果设备与版本类型匹配，选择一项备份密钥，再点击“安装所选密钥并尝试激活”。确认对话框默认选择“否”。
5. 没有完整密钥、硬件发生变化或版本不匹配时，使用系统激活设置 / 疑难解答；也可在已解密备份中查看所选密钥，供自己人工核对。

“尝试系统重新激活”使用本机当前安装的许可证，不安装备份密钥；批量许可设备可能联系其已配置的组织激活服务。本工具不添加或更改激活服务器。

旧版脚本产生的 ZIP 没有本版本的格式标识、认证和清单，而且原实现可能只保存目录条目。为避免误判完整性，本版本不将旧 ZIP 接受为可恢复备份。请在重装前重新创建 `.w11ab`。

## 从源码运行与构建

目标环境：Windows 10 / 11，64 位 Python 3.11 或以上。便携版在 Windows x64、Python 3.12 环境构建。其他系统可运行不涉及 Windows 的单元测试，但不支持真实许可操作；Windows ARM64 尚未验证。

安装运行依赖后双击 `launch.pyw`，或双击保留的 `备份Win10激活.py`。Python 入口没有命令行参数解析和终端交互。也可通过安装项目生成桌面程序入口。

开发环境命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm WindowsActivationBackup.spec
```

构建结果位于 `dist/WindowsActivationBackup/`，是包含运行时的目录式便携版。GitHub Actions 工作流执行同样的检查并上传构建产物，不自动创建发行版。

受限环境中，如果系统临时目录不可写，请将 `TEMP`、`TMP` 指向工作区内可写目录后运行测试。界面测试使用 Qt 的 `offscreen` 平台，不需要操作桌面。

## 结构与验证

| 文件 | 职责 |
| --- | --- |
| `activation_backup/models.py` | 许可快照、格式检查、设备与版本匹配 |
| `activation_backup/windows.py` | CIM 查询、管理员检查、系统许可方法 |
| `activation_backup/archive.py` | 加密格式、容量限制、清单与路径校验、原子保存 |
| `activation_backup/service.py` | 完整备份、导出、恢复与状态复验 |
| `activation_backup/ui.py` | PyQt6 界面、后台线程、确认与结果展示 |
| `tests/` | 合成数据测试；不执行真实密钥安装或激活写入 |

自动测试覆盖加密往返、错误密码、数据篡改、路径穿越、归档缺项、权限失败、取消、文件变化、设备不匹配、恢复中间失败、未激活的最终状态、密钥脱敏，以及界面从创建备份到打开校验的流程。

真实“重装 → 恢复”需要另备已授权的测试虚拟机或测试设备。不能用模拟测试或成功读取本机状态代替这项验收。生产机器上的密钥安装、重新激活与 UAC 交互不由自动测试执行。

## 接口与依赖资料

- [Windows 许可状态与产品接口](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/sppwmi/softwarelicensingproduct)
- [Windows 产品密钥安装接口](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/sppwmi/installproductkey-softwarelicensingservice)
- [Windows 激活命令的语义与权限](https://learn.microsoft.com/en-us/windows-server/get-started/activation-slmgr-vbs-options)
- [第三方组件说明](docs/THIRD_PARTY_NOTICES.md)
- [备份文件格式](docs/FORMAT.md)

项目沿用仓库的 [GPL-3.0 许可证](LICENSE)。
