# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file Windows build of InstacallMonitor.

Build with:  pyinstaller --noconfirm --clean packaging/InstacallMonitor.spec
Output:      dist/InstacallMonitor.exe

The app writes settings.toml / instacallmonitor.db / balance_monitor.log next to
the executable (see config.RUNTIME_DIR), so ship the .exe next to a .env file.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH injected by PyInstaller)

# plyer selects its backend at runtime; bundle the Windows notification backend.
hidden_imports = ["plyer.platforms.win.notification"] + collect_submodules("plyer.platforms.win")

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "monitor.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    # Built on Python 3.11+, where config.py uses the stdlib tomllib; the tomli
    # fallback ships a mypyc-compiled extension that PyInstaller cannot bundle.
    excludes=["tkinter", "pytest", "mypy", "ruff", "tomli"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="InstacallMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
