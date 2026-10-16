# Build with: python -m PyInstaller --noconfirm WindowsActivationBackup.spec
from pathlib import Path
from importlib.metadata import distribution, version
import os
import sys

root = Path(SPECPATH)
# Do not resolve system DLLs against unrelated tools on the developer's PATH.
# For example, a Poppler ICU DLL is not ABI-compatible with Windows' ICU shim.
windows = Path(os.environ['SystemRoot'])
os.environ['PATH'] = os.pathsep.join([
    str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
    str(windows / 'System32'), str(windows),
])
data_files = [(str(root / 'LICENSE'), '.'), (str(root / 'README.md'), '.'),
              (str(root / 'docs'), 'docs')]
packages = ['PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'cryptography', 'cffi', 'pycparser', 'pyinstaller']
for name in packages:
    package = distribution(name)
    for entry in package.files or []:
        if entry.name.upper().startswith(('LICENSE', 'COPYING')):
            data_files.append((str(package.locate_file(entry)), 'third_party_licenses/' + name))
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    data_files.append((str(python_license), 'third_party_licenses/Python'))
dependency_file = Path(workpath) / 'DEPENDENCIES.txt'
dependency_file.parent.mkdir(parents=True, exist_ok=True)
dependency_file.write_text('Python ' + sys.version + '\n' + '\n'.join(name + '==' + version(name) for name in packages), encoding='utf-8')
data_files.append((str(dependency_file), '.'))
analysis = Analysis(
    [str(root / 'launch.pyw')],
    pathex=[str(root)],
    binaries=[],
    datas=data_files,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PySide6', 'PyQt5'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name='WindowsActivationBackup', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    uac_admin=False,
)
collect = COLLECT(exe, analysis.binaries, analysis.datas,
                  strip=False, upx=False, name='WindowsActivationBackup')
