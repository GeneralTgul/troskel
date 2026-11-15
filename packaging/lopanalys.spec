# PyInstaller spec: one-folder build (faster start than one-file, and Qt WebEngine needs its resources).
# Run from the repo root:  pyinstaller --noconfirm packaging/lopanalys.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src" / "lopanalys"

datas = [
    (str(SRC / "i18n"), "lopanalys/i18n"),
    (str(SRC / "report" / "templates"), "lopanalys/report/templates"),
    (str(SRC / "report" / "assets"), "lopanalys/report/assets"),
    (str(SRC / "ui" / "assets"), "lopanalys/ui/assets"),
    (str(ROOT / "LICENSE"), "."),   # the AGPL text goes with every copy of the program
]
datas = [d for d in datas if Path(d[0]).exists()]
if (ROOT / "models").exists():
    datas.append((str(ROOT / "models"), "models"))   # pose/models.py looks here first
datas += collect_data_files("mediapipe")
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    [str(SRC / "ui" / "app.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    binaries=binaries,
    # Some packages are imported lazily inside functions (pose, report): take every submodule along,
    # so nothing depends on what PyInstaller's import analysis happens to find.
    hiddenimports=collect_submodules("lopanalys") + ["bleak.backends.winrt", "winrt", "matplotlib.backends.backend_svg",
                                                    # the encrypted backup (storage/privacy.py) imports these lazily
                                                    "cryptography.hazmat.primitives.ciphers.aead",
                                                    "cryptography.hazmat.primitives.kdf.scrypt",
                                                    # mail to the athlete (share/mail.py), imported lazily from the UI
                                                    "smtplib", "ssl", "email.message", "email.mime.application"],
    excludes=["tkinter", "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtDesigner"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Lopanalys", console=False,
          icon=str(SRC / "ui" / "assets" / "app.ico") if (SRC / "ui" / "assets" / "app.ico").exists() else None)
coll = COLLECT(exe, a.binaries, a.datas, name="Lopanalys")
