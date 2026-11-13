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
# versie meebakken voor de updatecontrole (updater.py leest version.txt uit de app)
_VFILE = os.path.join(ROOT, "build", "version.txt")
os.makedirs(os.path.dirname(_VFILE), exist_ok=True)
with open(_VFILE, "w", encoding="utf-8") as _f:
    _f.write(VERSION)
# Persoonsdetector voor 'persoon volgen' (YOLOX-tiny, Apache 2.0): eerst packaging/haal_model.py draaien.
# Zonder dit bestand herkent de app alleen hele personen (HOG), dus geen half silhouet bij inzoomen.
MODEL = os.path.join(ROOT, "models", "yolox_tiny.onnx")
if not os.path.isfile(MODEL):
    raise SystemExit("models/yolox_tiny.onnx ontbreekt: draai eerst  python packaging/haal_model.py")
ICO = os.path.join(ROOT, "app", "ptz.ico")
ICNS = os.path.join(ROOT, "PTZ Control.app", "Contents", "Resources", "AppIcon.icns")

# USB direct naar de videomixer: libusb-bibliotheek uit libusb-package meenemen (zit als package-data in het pakket)
try:
    from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
    USB_DATAS = collect_data_files("libusb_package") + collect_dynamic_libs("libusb_package")
except Exception:  # noqa: BLE001
    USB_DATAS = []

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "ptz_control.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "index.html"), "."), (os.path.join(ROOT, "app", "icon.png"), "."), (_VFILE, "."), (MODEL, "models")] + USB_DATAS,
    hiddenimports=["backup", "youtube", "updater", "wininstall", "tracker", "midi", "roland_v1hd", "v1hd_usb", "usb", "usb.core", "usb.util", "usb.backend.libusb1", "libusb_package", "actlog", "oefen", "liederen", "mido", "mido.backends.rtmidi", "rtmidi", "certifi", "pystray", "PIL.Image"]
    + (["pystray._win32"] if sys.platform == "win32" else ["pystray._darwin", "AppKit", "Foundation"] if sys.platform == "darwin" else []),
    # YOLO/torch is te groot voor de losse app: volgen gebruikt daar YOLOX (ONNX) via OpenCV (cv2.dnn)
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
