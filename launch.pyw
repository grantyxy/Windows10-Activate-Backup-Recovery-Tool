"""双击启动图形界面；不读取命令行参数，不进行终端交互。"""
from activation_backup.bootstrap import launch

raise SystemExit(launch())
