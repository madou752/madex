"""Mises à jour automatiques de Madex.

Deux sources, DÉSACTIVÉES par défaut (à activer seulement si vous maîtrisez la source) :
1. MADEX_AUTO_ZIP=1 : un zip « madex-phaseX.Y.zip » déposé dans Téléchargements est repéré, puis installé
   quand Madex est au repos (aucune session, il ne parle pas), avant de redémarrer.
   Attention : n'importe quel fichier portant ce nom serait exécuté, d'où la désactivation par défaut.
2. MADEX_AUTO_GIT=1 : si le dossier de Madex est un dépôt git avec un « origin », `git pull --ff-only` toutes les 6 h.
L'installation manuelle d'un zip depuis l'interface reste toujours possible.

Ce qui n'est JAMAIS touché : data/ (réglages, règles, empreinte vocale…), .venv/, .env (clés), models/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from . import config, secrets_store

VERSION_FILE = config.ROOT / "VERSION"
STATE = config.DATA / "maj.json"
ZIP_RE = re.compile(r"^madex-phase(\d+)(?:\.(\d+))?(?:\.(\d+))?(?: ?\(\d+\))?\.zip$", re.I)
KEEP = {"data", ".venv", ".env", "models", ".git", "__pycache__"}
AUTO_ZIP = secrets_store.get("MADEX_AUTO_ZIP") == "1"     # dans le .env (ou l'environnement)
AUTO_GIT = secrets_store.get("MADEX_AUTO_GIT") == "1"


def current_version() -> tuple:
    try:
        return tuple(int(x) for x in VERSION_FILE.read_text().strip().split("."))
    except (OSError, ValueError):
        return (0,)


def fmt(v: tuple) -> str:
    return ".".join(str(x) for x in v)


def download_dirs() -> list[Path]:
    home = Path.home()
    cands = [home / "Downloads", home / "Téléchargements", home / "OneDrive" / "Downloads",
             home / "OneDrive" / "Téléchargements"]
    return [d for d in cands if d.is_dir()]


def find_zip() -> tuple[tuple, Path] | None:
    best = None
    for d in download_dirs():
        for f in d.glob("madex-phase*.zip"):
            m = ZIP_RE.match(f.name)
            if not m:
                continue
            v = tuple(int(x) for x in m.groups() if x is not None)
            if v > current_version() and (best is None or v > best[0]):
                best = (v, f)
    return best


def _safe_extract(zf: zipfile.ZipFile, dest: Path):
    root = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"chemin suspect dans le zip : {member.filename}")
    zf.extractall(dest)


def install_zip(path: Path) -> str:
    """Copie la nouvelle version par-dessus l'actuelle. Renvoie un résumé, lève ValueError si le zip est invalide."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(path) as zf:
            _safe_extract(zf, tmp)
        src = tmp / "madex" if (tmp / "madex" / "main.py").exists() else tmp
        if not (src / "main.py").exists() or not (src / "madex" / "core.py").exists():
            raise ValueError("ce zip ne contient pas Madex")
        old_req = (config.ROOT / "requirements.txt").read_text(encoding="utf-8") if (config.ROOT / "requirements.txt").exists() else ""
        copied = 0
        for item in src.rglob("*"):
            rel = item.relative_to(src)
            if rel.parts and rel.parts[0] in KEEP or "__pycache__" in rel.parts:
                continue
            dest = config.ROOT / rel
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
                copied += 1
        new_req = (config.ROOT / "requirements.txt").read_text(encoding="utf-8")
    if new_req != old_req:
        pip_install()
    _after_install()
    return f"{copied} fichiers mis à jour" + (", dépendances installées" if new_req != old_req else "")


def pip_install():
    py = config.ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    from .claude_cli import NO_WINDOW
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(config.ROOT / "requirements.txt")],
                   cwd=config.ROOT, timeout=900, creationflags=NO_WINDOW)


def _after_install():
    """Ménage après une mise à jour : fichiers des anciennes versions devenus inutiles."""
    for old in ("madex/voiceid.py", "models/voix_locuteur.onnx"):
        try:
            (config.ROOT / old).unlink(missing_ok=True)
        except OSError:
            pass


def git_update() -> str | None:
    if not (config.ROOT / ".git").exists():
        return None
    from .claude_cli import NO_WINDOW
    run = lambda *a: subprocess.run(["git", "-C", str(config.ROOT), *a], capture_output=True, text=True,  # noqa: E731
                                    timeout=120, creationflags=NO_WINDOW)
    if run("remote").stdout.strip() == "":
        return None
    run("fetch", "--quiet")
    behind = run("rev-list", "--count", "HEAD..@{u}").stdout.strip()
    if not behind or behind == "0":
        return None
    old_req = (config.ROOT / "requirements.txt").read_text(encoding="utf-8")
    out = run("pull", "--ff-only")
    if out.returncode != 0:
        print(f"[maj] git pull impossible : {out.stderr.strip()}", flush=True)
        return None
    if (config.ROOT / "requirements.txt").read_text(encoding="utf-8") != old_req:
        pip_install()
    _after_install()
    return f"{behind} commit(s) récupéré(s)"


def restart():
    """Relance Madex (en arrière-plan) puis quitte ce processus."""
    py = config.ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not py.exists():
        py = Path(sys.executable)
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0       # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(py), str(config.ROOT / "main.py"), "--fond", "--redemarrage"], cwd=config.ROOT,
                     creationflags=flags, close_fds=True)


class Updater:
    def __init__(self, is_idle, announce, on_restart, emit):
        self.is_idle, self.announce, self.on_restart, self.emit = is_idle, announce, on_restart, emit
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {}
        self.status = {"version": fmt(current_version()), "pending": "", "last": self.state.get("last", ""),
                       "error": ""}
        threading.Thread(target=self._loop, daemon=True).start()

    def _save(self):
        config.DATA.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(self.state), encoding="utf-8")

    def install_file(self, data: bytes, filename: str) -> str:
        """Mise à jour depuis un fichier choisi par Madou (bouton du Diagnostic ou glisser-déposer sur la fenêtre)."""
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            raise ValueError("Ce fichier n'est pas un zip.") from None
        if not any(n.endswith("main.py") for n in names) or not any(n.endswith("madex/core.py") for n in names):
            raise ValueError("Ce zip ne contient pas Madex.")
        folder = config.DATA / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (re.sub(r"[^\w.() -]", "_", filename) or "madex.zip")
        path.write_bytes(data)
        m = ZIP_RE.match(path.name)
        label = fmt(tuple(int(x) for x in m.groups() if x)) if m else path.name
        self.announce(f"J'installe la version {label} et je redémarre.")

        def go():
            try:
                summary = install_zip(path)
                self.state["last"] = f"{label} installée depuis un fichier le {time.strftime('%d/%m à %Hh%M')} ({summary})"
                self._save()
                time.sleep(5)
                restart()
                self.on_restart()
            except Exception as exc:  # noqa: BLE001
                self.status["error"] = str(exc)
                self.announce(f"La mise à jour a échoué, monsieur : {exc}.")
        threading.Thread(target=go, daemon=True).start()
        return f"installation de {label} lancée"

    def check_now(self) -> str:
        found = find_zip()
        if found:
            v, path = found
            if self.state.get("failed") == str(path) + str(path.stat().st_mtime):
                return f"la mise à jour {fmt(v)} a déjà échoué"
            self.status["pending"] = fmt(v)
            return f"mise à jour {fmt(v)} trouvée"
        return "Madex est à jour"

    def _loop(self):
        time.sleep(30)
        last_git = 0.0
        while True:
            try:
                found = find_zip() if AUTO_ZIP else None
                if found:
                    v, path = found
                    key = str(path) + str(path.stat().st_mtime)
                    if self.state.get("failed") != key:
                        self.status["pending"] = fmt(v)
                        while not self.is_idle():
                            time.sleep(20)
                        time.sleep(2)                                   # le téléchargement est bien fini
                        self.announce(f"Une nouvelle version, la {fmt(v)}, est arrivée. Je l'installe et je redémarre.")
                        try:
                            summary = install_zip(path)
                            self.state["last"] = f"{fmt(v)} installée le {time.strftime('%d/%m à %Hh%M')} ({summary})"
                            self._save()
                            time.sleep(6)                               # laisse finir la phrase
                            restart()
                            self.on_restart()
                            return
                        except Exception as exc:  # noqa: BLE001
                            self.state["failed"] = key
                            self._save()
                            self.status["error"] = str(exc)
                            self.announce(f"La mise à jour a échoué, monsieur : {exc}. Je reste sur la version actuelle.")
                if AUTO_GIT and time.time() - last_git > 6 * 3600:
                    last_git = time.time()
                    if self.is_idle() and (summary := git_update()):
                        self.state["last"] = f"git : {summary}, le {time.strftime('%d/%m à %Hh%M')}"
                        self._save()
                        self.announce("J'ai récupéré une mise à jour depuis votre dépôt. Je redémarre.")
                        time.sleep(5)
                        restart()
                        self.on_restart()
                        return
            except Exception as exc:  # noqa: BLE001
                self.status["error"] = str(exc)
                print(f"[maj] {exc}", flush=True)
            time.sleep(60)
