"""在 Qt 无法导入时也能给出可操作的原生 Windows 提示。"""

import ctypes
import os
import webbrowser

RUNTIME_HELP = "https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist"


def launch() -> int:
    try:
        from .app import run
    except (ImportError, OSError):
        if os.name == "nt":
            answer = ctypes.windll.user32.MessageBoxW(
                None,
                "界面运行库未能加载。请确认程序已完整解压，并安装或修复微软 Visual C++ v14 x64 运行库。\n\n"
                "从源码启动时，还需安装 requirements.txt 中的依赖。\n\n"
                "是否打开微软官方运行库下载说明？",
                "Windows 激活备份 · 启动失败",
                0x00000004 | 0x00000010 | 0x00000100,
            )
            if answer == 6:
                webbrowser.open(RUNTIME_HELP)
        return 1
    return run()
