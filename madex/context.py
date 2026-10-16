"""Contexte automatique : sur quel projet Madou travaille-t-il en ce moment ?

Toutes les 3 s, Madex regarde la fenêtre au premier plan. Si c'est VS Code (ou Cursor, IntelliJ…),
le titre donne le fichier et le dossier ouverts : « theme.ts - portfolio - Visual Studio Code ».
On le relie à un projet de Documents et on lit sa branche git. Plus besoin de dire « sur le portfolio ».
Rien n'est envoyé nulle part : c'est juste lu localement et donné au cerveau avec la demande.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

from .projects import list_projects

EDITORS = re.compile(r"\s[-—]\s(Visual Studio Code|VS Code|Cursor|Windsurf|IntelliJ IDEA|WebStorm|PhpStorm|"
                     r"PyCharm|Android Studio|Rider)(\s.*)?$")


def foreground_title() -> str:
    if os.name != "nt":
        return ""
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def parse_title(title: str) -> tuple[str, str, str] | None:
    """« ● theme.ts - portfolio - Visual Studio Code » -> ("Visual Studio Code", "portfolio", "theme.ts")."""
    m = EDITORS.search(title)
    if not m:
        return None
    parts = [p.strip(" ●•*") for p in re.split(r"\s[-—]\s", title[:m.start()]) if p.strip(" ●•*")]
    if not parts:
        return None
    folder = parts[-1]
    folder = re.sub(r"\s*\[.*?\]$", "", folder)          # « portfolio [WSL] »
    file = parts[0] if len(parts) > 1 else ""
    return m.group(1), folder, file


def git_branch(path) -> str:
    try:
        from .claude_cli import NO_WINDOW
        out = subprocess.run(["git", "-C", str(path), "branch", "--show-current"], capture_output=True, text=True,
                             timeout=3, creationflags=NO_WINDOW)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


class Context:
    def __init__(self, on_change=lambda ctx: None):
        self.on_change = on_change
        self.current: dict = {}
        self._last_title = ""
        threading.Thread(target=self._watch, daemon=True).start()

    def _resolve(self, folder: str):
        f = folder.lower()
        for p in list_projects():
            if p.name.lower() == f:
                return p
        return None

    def update_from_title(self, title: str):
        parsed = parse_title(title)
        if not parsed and " – " in title:                       # IntelliJ / Android Studio : « projet – Fichier.kt »
            head, _, tail = title.partition(" – ")
            if self._resolve(head.strip()):
                parsed = ("IntelliJ", head.strip(), tail.split(" [")[0].strip())
        if not parsed:
            return
        editor, folder, file = parsed
        project = self._resolve(folder)
        ctx = {"editor": editor, "project": project.name if project else folder, "file": file,
               "path": str(project) if project else "", "known": bool(project),
               "branch": git_branch(project) if project else "", "at": time.time()}
        if (ctx["project"], ctx["file"], ctx["branch"]) != (self.current.get("project"), self.current.get("file"),
                                                           self.current.get("branch")):
            self.current = ctx
            self.on_change(ctx)
        else:
            self.current["at"] = ctx["at"]

    def _watch(self):
        while True:
            try:
                title = foreground_title()
                if title and title != self._last_title:
                    self._last_title = title
                    self.update_from_title(title)
                elif self.current.get("path") and time.time() - self.current.get("branch_at", 0) > 30:
                    b = git_branch(self.current["path"])            # la branche peut changer sans changer de fenêtre
                    self.current["branch_at"] = time.time()
                    if b != self.current.get("branch"):
                        self.current["branch"] = b
                        self.on_change(self.current)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)

    def for_prompt(self) -> str:
        c = self.current
        if not c or time.time() - c.get("at", 0) > 4 * 3600:
            return ""
        s = f"Contexte : Madou travaille en ce moment dans {c['editor']} sur le projet « {c['project']} »"
        if c.get("branch"):
            s += f" (branche {c['branch']})"
        if c.get("file"):
            s += f", fichier ouvert : {c['file']}"
        return s + ". Si sa demande ne nomme pas de projet, c'est de celui-ci qu'il parle."
