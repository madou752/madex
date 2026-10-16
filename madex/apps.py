"""Actions Windows en local (zéro token, instantané) : applis, sites, projets, volume.

Les applis sont cherchées dans TOUT le menu Démarrer (y compris celles du Microsoft Store),
avec une reconnaissance approximative du nom prononcé.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import time
import unicodedata
import webbrowser

from . import config
from .projects import resolve

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Noms parlés -> (commande de lancement, nom du processus pour fermer)
KNOWN = {
    "spotify": ("spotify:", "Spotify"),
    "vs code": ("code", "Code"), "visual studio code": ("code", "Code"), "vscode": ("code", "Code"),
    "chrome": ("chrome", "chrome"), "google chrome": ("chrome", "chrome"),
    "edge": ("msedge", "msedge"), "firefox": ("firefox", "firefox"),
    "explorateur": ("explorer", "explorer"), "explorateur de fichiers": ("explorer", "explorer"), "fichiers": ("explorer", "explorer"),
    "terminal": ("wt", "WindowsTerminal"), "invite de commandes": ("cmd", "cmd"),
    "bloc-notes": ("notepad", "notepad"), "bloc notes": ("notepad", "notepad"),
    "calculatrice": ("calc", "CalculatorApp"), "parametres": ("ms-settings:", "SystemSettings"),
    "gestionnaire des taches": ("taskmgr", "Taskmgr"),
    "teams": ("msteams:", "ms-teams"), "outlook": ("outlook", "olk"), "word": ("winword", "WINWORD"),
    "excel": ("excel", "EXCEL"), "powerpoint": ("powerpnt", "POWERPNT"),
}
SITES = {
    "youtube": "https://www.youtube.com", "github": "https://github.com", "gmail": "https://mail.google.com",
    "google": "https://www.google.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "netflix": "https://www.netflix.com", "twitch": "https://www.twitch.tv", "linkedin": "https://www.linkedin.com",
    "stack overflow": "https://stackoverflow.com", "figma": "https://www.figma.com", "notion": "https://www.notion.so",
    "mdn": "https://developer.mozilla.org", "npm": "https://www.npmjs.com", "vercel": "https://vercel.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com", "maps": "https://maps.google.com",
}

_start_apps: dict[str, str] = {}
_start_apps_at = 0.0


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^(l'|le |la |les |mon |ma |mes )", "", s.strip())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.-]", " ", s)).strip()


def start_apps() -> dict[str, str]:
    """Nom -> AppID de toutes les applis du menu Démarrer (Get-StartApps), en cache 30 min."""
    global _start_apps, _start_apps_at
    if os.name != "nt":
        return {}
    if not _start_apps or time.time() - _start_apps_at > 1800:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30, creationflags=NO_WINDOW)
        try:
            data = json.loads(out.stdout or "[]")
            data = data if isinstance(data, list) else [data]
            _start_apps = {norm(a["Name"]): a["AppID"] for a in data
                           if a.get("Name") and "uninstall" not in a["Name"].lower() and "désinstaller" not in a["Name"].lower()}
            _start_apps_at = time.time()
        except ValueError:
            pass
    return _start_apps


def find_app(name: str) -> tuple[str, str] | None:
    """-> (nom affiché, AppID ou commande)."""
    key = norm(name)
    if key in KNOWN:
        return key, KNOWN[key][0]
    apps = start_apps()
    if key in apps:
        return key, apps[key]
    starts = [n for n in apps if n.startswith(key + " ") or n == key]
    if starts:
        n = min(starts, key=len)
        return n, apps[n]
    close = difflib.get_close_matches(key, list(apps), n=1, cutoff=0.72)
    if close:
        return close[0], apps[close[0]]
    contains = [n for n in apps if len(key) >= 4 and key in n.split()]
    if contains:
        n = min(contains, key=len)
        return n, apps[n]
    return None


def open_app(name: str) -> str | None:
    hit = find_app(name)
    if not hit or os.name != "nt":
        return None
    label, target = hit
    if target.endswith(":") or target in {v[0] for v in KNOWN.values()}:
        subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=NO_WINDOW)
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], creationflags=NO_WINDOW)
    return label


def close_app(name: str) -> str | None:
    """Ferme proprement (comme la croix) : l'appli peut proposer d'enregistrer."""
    if os.name != "nt":
        return None
    key = norm(name)
    proc = KNOWN.get(key, (None, key))[1]
    proc, label = re.sub(r"[^\w .-]", "", proc), re.sub(r"[^\w .-]", "", key)   # pas d'injection PowerShell
    ps = ("$p = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
          f"($_.ProcessName -like '*{proc}*' -or $_.MainWindowTitle -like '*{label}*') }}; "
          "if ($p) { $p | ForEach-Object { [void]$_.CloseMainWindow() }; 'ok' }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=NO_WINDOW)
    return label if "ok" in out.stdout else None


def open_site(name: str) -> str | None:
    key = norm(name)
    url = SITES.get(key)
    if not url and re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", key.replace(" point ", ".")):
        url = "https://" + key.replace(" point ", ".")
    if not url:
        return None
    webbrowser.open(url)
    return key


def open_project(name: str) -> str | None:
    path = resolve(name)
    if not path:
        return None
    subprocess.Popen(["cmd", "/c", config.EDITOR_COMMAND, str(path)], creationflags=NO_WINDOW) if os.name == "nt" \
        else subprocess.Popen([config.EDITOR_COMMAND, str(path)])
    return path.name


# ------------------------------------------------------------ volume système
def _endpoint():
    import comtypes
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    from pycaw.pycaw import AudioUtilities
    speakers = AudioUtilities.GetSpeakers()
    if hasattr(speakers, "EndpointVolume"):
        return speakers.EndpointVolume
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume
    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def system_volume(value: int | None = None, delta: int = 0, mute: bool | None = None) -> int:
    ep = _endpoint()
    if mute is not None:
        ep.SetMute(1 if mute else 0, None)
    cur = round(ep.GetMasterVolumeLevelScalar() * 100)
    new = max(0, min(100, value if value is not None else cur + delta))
    if value is not None or delta:
        ep.SetMasterVolumeLevelScalar(new / 100, None)
    return new
