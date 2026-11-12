"""Windows: de losse app op een vaste plek zetten en zichzelf vervangen bij een update (alleen stdlib).

🧑 Vaste plek = `%LOCALAPPDATA%\\Programs\\PTZ Control\\PTZ Control.exe` (per gebruiker, geen beheerdersrechten nodig,
de app mag daar zijn eigen .exe vervangen). Niet in Program Files: daar kan de app zichzelf niet bijwerken.
Instellingen blijven apart in `%APPDATA%\\PTZ Control`.

Vervangen zonder hulpscript: Windows staat toe dat een draaiende .exe **hernoemd** wordt (alleen overschrijven en
wissen kan niet). Dus: nieuwe versie als `<exe>.nieuw` naast de oude zetten (zelfde map = zelfde schijf),
oude hernoemen naar `<exe>.oud`, `.nieuw` naar `<exe>`, nieuwe versie starten met `--wacht-poort` en stoppen.
De nieuwe versie wacht zelf tot de poort vrij is. `.oud`/`.nieuw` worden bij de volgende start opgeruimd.

Een eerder hulpscript (`bijwerken.bat` met tasklist/findstr/ping) bleef op Windows hangen: zonder console opende
elk consoleprogramma een eigen venster, en wachten op een PID via tasklist is onbetrouwbaar (PID's worden
hergebruikt). Niet terugzetten.
"""
import os
import subprocess
import sys

APP_NAME = "PTZ Control"
EXE_NAME = APP_NAME + ".exe"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def install_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Programs", APP_NAME)


def target_exe():
    return os.path.join(install_dir(), EXE_NAME)


def same_path(a, b):
    n = lambda p: os.path.normcase(os.path.realpath(os.path.abspath(p)))  # noqa: E731
    return n(a) == n(b)


def is_installed(exe):
    """Draait deze .exe vanaf de vaste plek?"""
    return same_path(os.path.dirname(exe), install_dir())


def cleanup(exe):
    """Restanten van een vorige wissel weghalen (`.oud` draait nu niet meer)."""
    for suffix in (".oud", ".nieuw"):
        try:
            os.remove(exe + suffix)
        except OSError:
            pass


def swap_exe(new_file, exe):
    """Zet `new_file` op de plek van `exe`, ook als `exe` nu draait. Gooit RuntimeError met een leesbare reden;
    dan staat alles weer zoals het was. `new_file` blijft bestaan (kopie, geen verplaatsing)."""
    folder = os.path.dirname(exe)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"map {folder} kan niet gemaakt worden ({e.strerror or e})") from e
    nieuw, oud = exe + ".nieuw", exe + ".oud"
    try:
        _copy(new_file, nieuw)
    except OSError as e:
        raise RuntimeError(f"geen schrijfrechten in {folder} ({e.strerror or e})") from e
    moved = False
    try:
        if os.path.exists(exe):
            try:
                os.remove(oud)
            except OSError:
                pass
            if os.path.exists(oud):   # oude .oud draait nog (bv. tweede kopie): andere naam kiezen
                oud = exe + f".oud{os.getpid()}"
            os.replace(exe, oud)
            moved = True
        os.replace(nieuw, exe)
    except OSError as e:
        if moved and not os.path.exists(exe):
            try:
                os.replace(oud, exe)
            except OSError:
                pass
        try:
            os.remove(nieuw)
        except OSError:
            pass
        raise RuntimeError(f"{os.path.basename(exe)} kan niet vervangen worden ({e.strerror or e})") from e


def _copy(src, dst):
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            b = fi.read(1 << 20)
            if not b:
                break
            fo.write(b)
        fo.flush()
        os.fsync(fo.fileno())


def child_env():
    """Omgeving voor een nieuwe PTZ Control.exe die vanuit deze app gestart wordt. Zonder dit erft de nieuwe
    onefile-exe de `_PYI_*`-variabelen van deze bootloader en kan hij de (straks gewiste) tijdelijke map van
    deze app proberen te gebruiken. PYINSTALLER_RESET_ENVIRONMENT is de officiële manier (PyInstaller ≥ 6.10)."""
    env = {k: v for k, v in os.environ.items() if not (k.startswith("_PYI_") or k == "_MEIPASS2")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def start_exe(exe, args=()):
    """Start `exe` los van dit proces (overleeft het afsluiten van deze app)."""
    cmd = [exe, *args]
    kw = {"cwd": os.path.dirname(exe) or None, "env": child_env(), "close_fds": True,
          "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform != "win32":
        subprocess.Popen(cmd, start_new_session=True, **kw)  # noqa: S603
        return
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        # De PyInstaller-bootloader kan de app in een job-object zetten dat bij afsluiten alle kinderen meeneemt.
        subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)  # noqa: S603
    except OSError:   # job staat losmaken niet toe
        subprocess.Popen(cmd, creationflags=flags, **kw)  # noqa: S603


_PS_SHORTCUTS = r"""
$ErrorActionPreference = 'Stop'
$exe = $env:PTZ_EXE
$s = New-Object -ComObject WScript.Shell
$ok = 0
foreach ($d in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
  try {
    if (-not $d) { continue }
    $l = $s.CreateShortcut((Join-Path $d 'PTZ Control.lnk'))
    $l.TargetPath = $exe
    $l.WorkingDirectory = (Split-Path $exe)
    $l.IconLocation = "$exe,0"
    $l.Description = 'PTZ Control'
    $l.Save()
    $ok++
  } catch {}
}
if ($ok -eq 0) { exit 1 }
"""


def make_shortcuts(exe):
    """Snelkoppeling *PTZ Control* in het Startmenu en op het bureaublad, direct naar de .exe (kan dus aan de
    taakbalk). Een oude snelkoppeling met dezelfde naam (naar de .bat van de broncode) wordt vervangen.
    Geeft True als er minstens één gemaakt is."""
    if sys.platform != "win32":
        return False
    env = dict(os.environ, PTZ_EXE=exe)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", _PS_SHORTCUTS], env=env, capture_output=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def show_in_explorer(path):
    if sys.platform == "win32":
        try:
            subprocess.Popen(["explorer", "/select,", path])  # noqa: S603, S607
        except OSError:
            pass


def ask_yes_no(text, title=APP_NAME):
    """Ja/nee-vraag (Windows MessageBox). Elders altijd nee."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415
        # MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND; IDYES = 6
        return ctypes.windll.user32.MessageBoxW(None, text, title, 0x04 | 0x20 | 0x10000) == 6
    except Exception:  # noqa: BLE001
        return False


def info(text, title=APP_NAME):
    if sys.platform != "win32":
        print(text)
        return
    try:
        import ctypes  # noqa: PLC0415
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x10000)
    except Exception:  # noqa: BLE001
        print(text)
