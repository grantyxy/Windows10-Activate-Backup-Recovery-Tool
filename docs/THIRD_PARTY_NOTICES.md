# 第三方组件

本项目沿用原仓库的 GPL-3.0。发布时应同时提供本项目对应版本的源代码、构建说明和许可证。

运行时依赖为 PyQt6 / Qt、PyQt6-sip、cryptography 及其依赖；构建工具为 PyInstaller。组件的精确版本记录在依赖文件以及发行包中的 `DEPENDENCIES.txt`。

构建配置从已安装发行包的元数据中收集第三方 LICENSE / COPYING 文件，随便携版放在 `_internal/third_party_licenses/`。其中 PyQt6 的开源发行包附 GPL-3.0，Qt 运行库发行包附 LGPL-3.0；其余依赖以各自附带的许可文件为准。目录式打包保留 Qt 动态库，不将其静态链接到本项目代码。

上游项目与对应源代码入口：

- Python：https://www.python.org/downloads/source/
- PyQt6：https://www.riverbankcomputing.com/software/pyqt/download
- Qt：https://download.qt.io/archive/qt/
- PyQt6-sip：https://pypi.org/project/PyQt6-sip/
- cryptography：https://github.com/pyca/cryptography
- cffi：https://github.com/python-cffi/cffi
- PyInstaller：https://github.com/pyinstaller/pyinstaller

本工具并非微软官方软件。Windows 商标及许可证由相应权利人持有；备份或安装产品密钥不改变原许可证的授权范围。
