# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-recept voor de losse app. Bouwen (vanuit de hoofdmap van de repo):
#   pip install -r packaging/requirements-build.txt
#   pyinstaller --noconfirm packaging/ptz_control.spec
# Resultaat in dist/: Windows "PTZ Control.exe" (één bestand), macOS "PTZ Control.app".
# Een Windows-exe moet op Windows gebouwd worden en een Mac-app op een Mac (geen kruis-bouwen);
# dat doet .github/workflows/build.yml automatisch.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH komt van PyInstaller)
VERSION = os.environ.get("PTZ_VERSION", "0.0.0").lstrip("v")
ICO = os.path.join(ROOT, "app", "ptz.ico")
ICNS = os.path.join(ROOT, "PTZ Control.app", "Contents", "Resources", "AppIcon.icns")

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "ptz_control.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "index.html"), "."), (os.path.join(ROOT, "app", "icon.png"), ".")],
    hiddenimports=["backup", "youtube", "tracker", "certifi", "pystray", "PIL.Image"]
    + (["pystray._win32"] if sys.platform == "win32" else ["pystray._darwin", "AppKit", "Foundation"] if sys.platform == "darwin" else []),
    # YOLO/torch is te groot voor de losse app: volgen gebruikt daar de OpenCV-persoonsdetector (HOG)
    excludes=["ultralytics", "torch", "torchvision", "matplotlib", "tkinter", "pandas", "scipy", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

if sys.platform == "darwin":
    exe = EXE(  # noqa: F821
        pyz, a.scripts, [], exclude_binaries=True, name="PTZ Control",
        console=False, argv_emulation=False, icon=ICNS, upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="PTZ Control", upx=False)  # noqa: F821
    app = BUNDLE(  # noqa: F821
        coll, name="PTZ Control.app", icon=ICNS, bundle_identifier="nl.hesselsit.ptzcontrol", version=VERSION,
        info_plist={
            "CFBundleName": "PTZ Control",
            "CFBundleDisplayName": "PTZ Control",
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,              # geen Dock-icoon: de app zit in de menubalk
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # macOS 15+: zonder deze tekst kan de app de camera's in het lokale netwerk niet bereiken
            "NSLocalNetworkUsageDescription": "PTZ Control bedient de PTZ-camera's in het lokale netwerk.",
        },
    )
else:
    exe = EXE(  # noqa: F821
        pyz, a.scripts, a.binaries, a.datas, [], name="PTZ Control",
        console=False,                       # geen zwart venster; icoon in het systeemvak
        icon=ICO, upx=False, runtime_tmpdir=None,
    )
