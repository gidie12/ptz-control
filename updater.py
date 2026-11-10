"""Updatecontrole: is er een nieuwere versie van PTZ Control?

Twee manieren, afhankelijk van hoe de app draait:
- **git**: de app draait uit een git-checkout (Start PTZ Control). `git fetch` en vergelijken met de upstream-branch;
  bijwerken = `git pull --ff-only`, zo nodig packages bijwerken, daarna de server opnieuw starten.
- **release**: de losse app (PyInstaller). Nieuwste GitHub-release vergelijken met de ingebouwde versie;
  bijwerken = de downloadpagina openen (een draaiende .exe/.app kan zichzelf niet vervangen).
Alleen stdlib.
"""
import json
import os
import re
import shutil
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
                      "checked": None, "error": None, "installing": False}

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

    def _check_release(self):
        req = urllib.request.Request(f"{API_BASE}/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "PTZ-Control"})
        tok = self._token()
        if tok:
            req.add_header("Authorization", "Bearer " + tok)
        ctx = None
        try:
            import certifi  # noqa: PLC0415
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # noqa: BLE001
            pass
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                rel = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("geen release gevonden (privé-repository? zet een token in github_token.txt)") from e
            raise
        latest = rel.get("tag_name") or ""
        cur = self.state["current"]
        avail = _ver_tuple(latest) > _ver_tuple(cur)
        notes = [ln.strip().lstrip("-*• ").strip() for ln in (rel.get("body") or "").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")][:8]
        return {"available": avail, "latest": latest, "count": 0, "notes": notes if avail else [],
                "url": rel.get("html_url") or f"https://github.com/{REPO}/releases/latest", "can_install": False, "blocked": None}

    # ------------------------------------------------------------ bijwerken (alleen git)
    def install(self):
        if self.mode != "git":
            raise RuntimeError("Bijwerken kan alleen bij de versie uit git; download de nieuwe losse app.")
        st = self.check()
        if not st["available"]:
            raise RuntimeError("Er is geen nieuwe versie.")
        if not st["can_install"]:
            raise RuntimeError(f"Automatisch bijwerken kan niet: {st.get('blocked') or 'onbekende reden'}.")
        self._set(installing=True, error=None)
        threading.Thread(target=self._install, daemon=True, name="update-install").start()

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
