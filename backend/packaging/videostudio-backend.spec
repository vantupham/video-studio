# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the backend sidecar. Always build --onedir (COLLECT): onefile
# executables unpack to %TEMP% at every launch and trigger antivirus false positives.
#
#   cd backend && uv run pyinstaller packaging/videostudio-backend.spec --noconfirm
#
# Output: backend/dist/videostudio-backend/{videostudio-backend[.exe], _internal/}
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

BACKEND = Path(SPECPATH).resolve().parent
REPO = BACKEND.parent

datas = [
    # Alembic loads migration scripts from files, so ship them as data, not bytecode.
    (str(BACKEND / "videostudio" / "db" / "migrations"), "videostudio/db/migrations"),
    (str(REPO / "packages" / "ir-schema" / "content-ir.schema.json"), "videostudio/ir"),
]

hiddenimports = (
    collect_submodules("videostudio")
    + collect_submodules("uvicorn")
    + collect_submodules("websockets")
)

a = Analysis(
    [str(BACKEND / "packaging" / "entry.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = REPO / "apps" / "desktop" / "src-tauri" / "icons" / "icon.ico"

exe = EXE(
    pyz,
    a.scripts,
    # Force Python UTF-8 mode in the frozen app (Vietnamese paths/text on Windows).
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="videostudio-backend",
    debug=False,
    strip=False,
    upx=False,
    # Console subsystem keeps stdout/stderr valid; the Tauri shell hides the window
    # with CREATE_NO_WINDOW and reads the port from stdout.
    console=True,
    icon=str(icon) if sys.platform == "win32" and icon.is_file() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="videostudio-backend",
)
