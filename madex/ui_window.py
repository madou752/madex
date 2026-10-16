"""La fenêtre de MADEX : une fenêtre Google Chrome en mode application (Edge si Chrome est absent),
avec son propre profil.

Avoir un profil séparé (data/ui-profile) donne un processus à nous : on peut ouvrir et fermer la fenêtre
à la voix sans jamais toucher à ton navigateur.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import webbrowser

from . import config

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def _browser() -> tuple[str, str] | None:
    """Chrome d'abord (choix de Madou), sinon Edge. Renvoie (chemin, nom)."""
    local = os.environ.get("LOCALAPPDATA", "")
    chrome = (shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.join(local, r"Google\Chrome\Application\chrome.exe") if local else None)
    edge = (shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    order = [("chrome", chrome), ("edge", edge)] if config.UI_BROWSER != "edge" else [("edge", edge), ("chrome", chrome)]
    for name, paths in order:
        for path in paths:
            if path and os.path.exists(path):
                return path, name
    return None


def is_open() -> bool:
    return _proc is not None and _proc.poll() is None


def open_window():
    global _proc
    with _lock:
        if is_open():
            return
        url = f"http://127.0.0.1:{config.PORT}"
        found = _browser()
        if not found:
            webbrowser.open(url)
            return
        exe, name = found
        profile = config.DATA / ("ui-profile-chrome" if name == "chrome" else "ui-profile")   # profil à part : ton navigateur n'est jamais touché
        profile.mkdir(parents=True, exist_ok=True)
        _proc = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1180,820",
                                  "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"])


def close_window():
    global _proc
    with _lock:
        if is_open():
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(_proc.pid), "/T", "/F"], capture_output=True)
            else:
                _proc.terminate()
        _proc = None
