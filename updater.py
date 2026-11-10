"""Updatecontrole: is er een nieuwere versie van PTZ Control?

Twee manieren, afhankelijk van hoe de app draait:
- **git**: de app draait uit een git-checkout (Start PTZ Control). `git fetch` en vergelijken met de upstream-branch;
  bijwerken = `git pull --ff-only`, zo nodig packages bijwerken, daarna de server opnieuw starten.
- **release**: de losse app (PyInstaller). Nieuwste GitHub-release vergelijken met de ingebouwde versie;
  bijwerken = het bestand voor dit platform downloaden, daarna vervangt een klein hulpscript de .exe/.app zodra
  deze app gestopt is en start de nieuwe versie (een draaiend programma kan zichzelf niet overschrijven).
Alleen stdlib.
"""
import json
import os
import re
import shutil
import stat
import tempfile
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

REPO = "gidie12/ptz-control"
INTERVAL = int(os.environ.get("PTZ_UPDATE_INTERVAL", 6 * 3600))   # elke 6 uur
FIRST_DELAY = int(os.environ.get("PTZ_UPDATE_FIRST", 20))          # eerste controle kort na de start
API_BASE = os.environ.get("PTZ_UPDATE_API", "https://api.github.com")


def _ver_tuple(v):
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


class Updater:
    def __init__(self, here, frozen, res_dir, data_dir, on_change, restart):
        self.here, self.frozen, self.res_dir, self.data_dir = here, frozen, res_dir, data_dir
        self.on_change, self.restart = on_change, restart
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.mode = self._detect_mode()
        self.state = {"type": "update", "mode": self.mode, "current": self._current(), "available": False,
                      "latest": None, "notes": [], "count": 0, "url": None, "can_install": False,
                      "checked": None, "error": None, "installing": False, "progress": None}

    # ------------------------------------------------------------ algemeen
    def _detect_mode(self):
        if os.environ.get("PTZ_UPDATE", "").lower() in ("0", "uit", "off", "no"):
            return "off"
        if self.frozen:
            return "release"
        if os.path.isdir(os.path.join(self.here, ".git")) and shutil.which("git"):
            return "git"
        return "off"

    def _git(self, *args, timeout=60):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo", LC_ALL="C")
        r = subprocess.run(["git", "-C", self.here, *args], capture_output=True, text=True, timeout=timeout, env=env)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "git-fout").strip().splitlines()[-1])
        return r.stdout.strip()

    def _current(self):
        if self.mode == "git":
            try:
                return self._git("describe", "--tags", "--always", "--dirty", timeout=10)
            except Exception:  # noqa: BLE001
                return "onbekend"
        if self.mode == "release":
            try:
                with open(os.path.join(self.res_dir, "version.txt"), encoding="utf-8") as f:
                    return f.read().strip() or "0.0.0"
            except OSError:
                return "0.0.0"
        return None

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)
            snap = dict(self.state)
        self.on_change(snap)

    def start(self):
        if self.mode != "off":
            threading.Thread(target=self._loop, daemon=True, name="updater").start()

    def refresh(self):
        self._wake.set()

    def _loop(self):
        self._wake.wait(FIRST_DELAY)
        while True:
            self._wake.clear()
            self.check()
            self._wake.wait(INTERVAL)

    # ------------------------------------------------------------ controleren
    def check(self):
        if self.mode == "off" or self.state["installing"]:
            return self.snapshot()
        try:
            res = self._check_git() if self.mode == "git" else self._check_release()
            res["error"] = None
        except Exception as e:  # noqa: BLE001
            # netwerk/inlogfout: geen melding tonen, alleen in de status
            res = {"error": f"Controleren mislukt: {e}"}
        res["checked"] = time.strftime("%H:%M")
        self._set(**res)
        return self.snapshot()

    def _upstream(self):
        try:
            return self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=10)
        except RuntimeError:
            return "origin/" + self._git("rev-parse", "--abbrev-ref", "HEAD", timeout=10)

    def _check_git(self):
        self._git("fetch", "--quiet", "--tags", "origin", timeout=90)
        up = self._upstream()
        behind = int(self._git("rev-list", "--count", f"HEAD..{up}") or 0)
        ahead = int(self._git("rev-list", "--count", f"{up}..HEAD") or 0)
        notes = self._git("log", "--format=%s", "-n", "8", f"HEAD..{up}").splitlines() if behind else []
        latest = self._git("describe", "--tags", "--always", up) if behind else None
        dirty = bool(self._git("status", "--porcelain", "--untracked-files=no"))
        return {"current": self._current(), "available": behind > 0, "count": behind, "notes": notes, "latest": latest,
                "url": f"https://github.com/{REPO}/commits", "can_install": behind > 0 and ahead == 0 and not dirty,
                "blocked": ("er zijn lokale wijzigingen" if dirty else "deze computer heeft eigen commits" if ahead else None)}

    def _token(self):
        tok = os.environ.get("PTZ_GITHUB_TOKEN", "").strip()
        if not tok:
            try:
                with open(os.path.join(self.data_dir, "github_token.txt"), encoding="utf-8") as f:
                    tok = f.read().strip()
            except OSError:
                pass
        return tok

    @staticmethod
    def _ssl():
        ctx = ssl.create_default_context()   # systeemcertificaten (en SSL_CERT_FILE)
        try:
            import certifi  # noqa: PLC0415  # python.org-Python op macOS heeft vaak geen eigen rootcertificaten
            ctx.load_verify_locations(certifi.where())
        except Exception:  # noqa: BLE001
            pass
        return ctx

    @staticmethod
    def _asset_suffix():
        return "-Windows.exe" if sys.platform == "win32" else "-macOS.zip" if sys.platform == "darwin" else None

    def _app_path(self):
        """Pad van de draaiende losse app: de .exe (Windows) of de .app-map (macOS)."""
        exe = os.path.abspath(sys.executable)
        if sys.platform == "darwin" and ".app/Contents/MacOS" in exe:
            return exe[: exe.index(".app/Contents/MacOS") + 4]
        return exe

    def _check_release(self):
        req = urllib.request.Request(f"{API_BASE}/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "PTZ-Control"})
        tok = self._token()
        if tok:
            req.add_header("Authorization", "Bearer " + tok)
        try:
            with urllib.request.urlopen(req, timeout=15, context=self._ssl()) as r:
                rel = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("geen release gevonden") from e
            raise
        latest = rel.get("tag_name") or ""
        cur = self.state["current"]
        avail = _ver_tuple(latest) > _ver_tuple(cur)
        notes = [ln.strip().lstrip("-*• ").replace("**", "").strip() for ln in (rel.get("body") or "").splitlines()
                 if ln.strip() and not ln.strip().startswith("#") and "Changelog" not in ln and "Alle wijzigingen:" not in ln
                 and "://" not in ln][:8]
        suf = self._asset_suffix()
        asset = next((a for a in rel.get("assets") or [] if suf and a.get("name", "").endswith(suf)), None)
        self._asset = asset
        return {"available": avail, "latest": latest, "count": 0, "notes": notes if avail else [],
                "url": rel.get("html_url") or f"https://github.com/{REPO}/releases/latest",
                "can_install": bool(avail and asset), "size": (asset or {}).get("size"),
                "blocked": None if asset or not avail else "geen download voor dit besturingssysteem in deze release"}

    # ------------------------------------------------------------ bijwerken
    def install(self):
        if self.mode not in ("git", "release"):
            raise RuntimeError("Bijwerken is hier niet beschikbaar.")
        st = self.check()
        if not st["available"]:
            raise RuntimeError("Er is geen nieuwe versie.")
        if not st["can_install"]:
            raise RuntimeError(f"Automatisch bijwerken kan niet: {st.get('blocked') or 'onbekende reden'}.")
        self._set(installing=True, error=None, progress=None)
        target = self._install if self.mode == "git" else self._install_release
        threading.Thread(target=target, daemon=True, name="update-install").start()

    def _install(self):
        try:
            old = self._git("rev-parse", "HEAD")
            self._git("pull", "--ff-only", "--quiet", timeout=180)
            new = self._git("rev-parse", "HEAD")
            changed = self._git("diff", "--name-only", old, new).splitlines()
            if "requirements.txt" in changed:
                self._pip()
            print(f"Bijgewerkt naar {self._current()}; PTZ Control start opnieuw.")
            time.sleep(0.5)
            self.restart()
        except Exception as e:  # noqa: BLE001
            self._set(installing=False, error=f"Bijwerken mislukt: {e}")

    def _pip(self):
        req = os.path.join(self.here, "requirements.txt")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError("packages bijwerken mislukt: " + (r.stderr.strip().splitlines() or ["?"])[-1])
        # de starter vergelijkt requirements.txt met deze kopie; gelijk houden zodat hij niet opnieuw installeert
        venv = os.path.join(self.here, ".venv")
        if os.path.isdir(venv) and os.path.realpath(sys.prefix) == os.path.realpath(venv):
            shutil.copyfile(req, os.path.join(venv, "requirements.installed"))

    # ------------------------------------------------------------ losse app vervangen
    def _download(self, url, dest, size):
        req = urllib.request.Request(url, headers={"User-Agent": "PTZ-Control", "Accept": "application/octet-stream"})
        got, last = 0, -1
        with urllib.request.urlopen(req, timeout=60, context=self._ssl()) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                pct = int(got * 100 / total) if total else None
                if pct is not None and pct != last and (pct - last >= 2 or pct == 100):
                    last = pct
                    self._set(progress=pct)
        if size and got != size:
            raise RuntimeError(f"download onvolledig ({got} van {size} bytes)")

    def _install_release(self):
        try:
            asset = getattr(self, "_asset", None)
            if not asset:
                raise RuntimeError("geen download gevonden")
            work = tempfile.mkdtemp(prefix="ptz-update-")
            dest = os.path.join(work, asset["name"])
            self._download(asset["browser_download_url"], dest, asset.get("size"))
            app = self._app_path()
            self._set(progress=100)
            if sys.platform == "win32":
                script = self._script_windows(app, dest, work)
                flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
                subprocess.Popen(["cmd", "/c", script], creationflags=flags, close_fds=True)
            else:
                new_app = self._unzip_mac(dest, work)
                script = self._script_mac(app, new_app, work)
                subprocess.Popen(["/bin/bash", script], start_new_session=True, close_fds=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"Nieuwe versie gedownload; PTZ Control wordt vervangen en opnieuw gestart ({app}).")
            time.sleep(0.5)
            os._exit(0)
        except Exception as e:  # noqa: BLE001
            self._set(installing=False, progress=None, error=f"Bijwerken mislukt: {e}")

    @staticmethod
    def _unzip_mac(zip_path, work):
        out = os.path.join(work, "uitgepakt")
        r = subprocess.run(["ditto", "-x", "-k", zip_path, out], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("uitpakken mislukt: " + (r.stderr.strip() or "?"))
        apps = [n for n in os.listdir(out) if n.endswith(".app")]
        if not apps:
            raise RuntimeError("geen .app in de download")
        return os.path.join(out, apps[0])

    @staticmethod
    def _script_mac(app, new_app, work):
        """Wacht tot deze app gestopt is, wisselt de .app om (oude eerst opzij) en start de nieuwe.
        Lukt omwisselen niet (geen schrijfrechten), dan blijft de oude staan en toont Finder de nieuwe."""
        q = lambda p: "'" + p.replace("'", "'\\''") + "'"  # noqa: E731
        path = os.path.join(work, "bijwerken.sh")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"""#!/bin/bash
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
APP={q(app)}; NEW={q(new_app)}
rm -rf "$APP.oud"
if mv "$APP" "$APP.oud" && mv "$NEW" "$APP"; then
  rm -rf "$APP.oud"
  xattr -dr com.apple.quarantine "$APP" 2>/dev/null
  open "$APP" --args --no-open
else
  [ -d "$APP.oud" ] && [ ! -d "$APP" ] && mv "$APP.oud" "$APP"
  open -R "$NEW"; open "$APP" --args --no-open
fi
""")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    @staticmethod
    def _script_windows(exe, new_exe, work):
        """Wacht tot deze .exe gestopt is, zet de nieuwe ervoor in de plaats en start hem."""
        path = os.path.join(work, "bijwerken.bat")
        pid = os.getpid()
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"""@echo off
chcp 65001 >nul
:wacht
tasklist /fi "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wacht)
set /a n=0
:kopie
copy /y "{new_exe}" "{exe}" >nul && goto start
set /a n+=1
if %n% lss 20 (timeout /t 1 /nobreak >nul & goto kopie)
start "" explorer /select,"{new_exe}"
:start
start "" "{exe}" --no-open
""")
        return path
