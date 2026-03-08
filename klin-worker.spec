# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_submodules


# In PyInstaller spec execution, __file__ is not guaranteed. Use CWD fallback.
project_root = Path(os.getcwd()).resolve()

datas = [
    (str(project_root / "alembic"), "alembic"),
    (str(project_root / "alembic.ini"), "."),
]

hiddenimports = [
    "aiosqlite",
    "alembic.command",
    "alembic.config",
    "lightrag.llm.ollama",
    "ollama",
    "pydantic_settings",
]

hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("raganything")


a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="klin-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
