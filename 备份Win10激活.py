import os.path
import shutil
import winreg
from shutil import copytree
from zipfile import ZipFile


def copy_activate_folder():
    src = r"C:\Windows\System32\spp\store\2.0"
    dest = os.path.expanduser(r"~\Desktop\2.0")
    try:
        copytree(src, dest)
    except FileExistsError as e:
        shutil.rmtree(dest)
        copytree(src, dest)
    return dest


def get_current_key():
    registry = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              "SOFTWARE\Microsoft\Windows NT\CurrentVersion\SoftwareProtectionPlatform")
    key_info_number = winreg.QueryInfoKey(registry)[1]
    for x in range(key_info_number):
        if winreg.EnumValue(registry, x)[0] == "BackupProductKeyDefault":
            key =  winreg.EnumValue(registry, x)[1]
            break
    with open("key.txt", "w") as f:
        f.write(key)
        return os.path.realpath(f.name)


def compress_file(activate_folder: str, key_file: str):
    dest = input("请输入备份的激活文件要存放的位置：")

    with ZipFile(dest + '\\' + "activate_backup.zip", "w") as fh:
        fh.write(activate_folder)
        fh.write(key_file)
    shutil.rmtree(activate_folder)
    os.remove(key_file)

