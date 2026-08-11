# -*- mode: python ; coding: utf-8 -*-
"""Freeze the AirHand engine into an executable that needs no Python installation.

Run from `backend/`:

    .\\.venv\\Scripts\\pyinstaller.exe airhand.spec

Two data files have to be inside the bundle, because the engine already knows to look for them
there — `airhand/model.py` and `airhand/protocol.py` resolve `sys._MEIPASS` when frozen. Both go
to the bundle root, which is exactly where those two functions look.

**One directory, not one file.** Both were built and measured on 2026-08-11, time from launch to
the handshake being published, with the real camera source:

    one-dir    1.20 / 1.10 / 1.12 s    266 MB on disk
    one-file   4.56 / 3.32 / 3.25 s    108 MB in one file

One-file re-extracts the whole bundle on every launch and would spend most of the 3-second startup
budget doing it. The decisive difference is elsewhere, though: a one-file build runs a bootloader
parent that spawns the real process as a **child**, so killing what you launched leaves the engine
running. This engine holds a camera and can drive the cursor — a stop that does not stop it is not
an option.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

REPO_ROOT = Path(SPECPATH).parent

# MediaPipe ships native extension modules and `.binarypb` graph definitions that the module
# graph cannot see: they are loaded by path at runtime, not imported. Everything else in this
# spec is ordinary; this line is the one the build depends on.
mediapipe_datas, mediapipe_binaries, mediapipe_hidden = collect_all("mediapipe")

datas = [
    (str(REPO_ROOT / "models" / "hand_landmarker.task"), "."),
    (str(REPO_ROOT / "shared" / "protocol" / "protocol.json"), "."),
    *mediapipe_datas,
]

hiddenimports = [
    # pynput picks its backend at import time by platform, so the module graph never sees it.
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    *mediapipe_hidden,
]

excludes = [
    "tkinter",
    # Test-only, and pytest drags in a lot.
    "pytest",
    "_pytest",
]

# **Do not exclude matplotlib or sounddevice.** They look like dead weight — this engine draws
# nothing and plays nothing — but `import mediapipe` reaches
# `tasks.python.vision.drawing_utils`, which imports `matplotlib.pyplot` at module level. Excluding
# it produced a build that started, published a handshake and crashed the moment the source was
# built. Measured 2026-08-11; the synthetic source never imports MediaPipe, so the failure hides
# from any check that does not open a camera.

analysis = Analysis(
    ["engine_main.py"],
    pathex=[str(REPO_ROOT / "backend")],
    binaries=mediapipe_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

# `console=True` deliberately. Tauri spawns the engine with CREATE_NO_WINDOW, so no console
# appears in the packaged app — and the executable stays diagnosable when run by hand, which is
# the only way to test the freeze without the UI.
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="airhand-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="airhand-engine",
)
