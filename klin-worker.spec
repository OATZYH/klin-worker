# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules


# In PyInstaller spec execution, __file__ is not guaranteed. Use CWD fallback.
project_root = Path(os.getcwd()).resolve()

datas = [
    (str(project_root / "alembic"), "alembic"),
    (str(project_root / "alembic.ini"), "."),
    (str(project_root / "VERSION"), "."),
]
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = [
    "aiosqlite",
    "alembic.command",
    "alembic.config",
    "lightrag.llm.ollama",
    "ollama",
    "pydantic_settings",
]

hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("raganything")

# Bundle the docling stack and its native ML dependencies in full.
# collect_all captures submodules, data files AND dynamic libs (.dll/.pyd) —
# strictly stronger than collect_submodules + collect_data_files for native
# extensions like docling_parse (C++/Cython) and torch / onnxruntime DLLs.
for pkg in (
    "docling",
    "docling_core",
    "docling_parse",
    "docling_ibm_models",
    "torch",
    "onnxruntime",
    "transformers",
    "huggingface_hub",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden


a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # MinerU parser — not used at runtime (we use docling).
        # Excluding it and its heavy transitive deps shrinks the binary.
        # NOTE: torch / torchvision are NOT excluded — docling-ibm-models
        # (layout + TableFormer) loads them at runtime.
        "mineru",
        "magic_pdf",
        "paddleocr",
        "paddlepaddle",
        "detectron2",
        "unimernet",
        "struct_eqtable",
    ],
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
