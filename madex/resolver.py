"""Comprendre « lance todo app » : un projet ? une appli ? l'ouvrir ou le démarrer ?

1. Retrouver le nom proprement : on compare ce qui a été entendu (souvent mal transcrit : « agro app ») aux
   projets, aux applis installées et aux sites, avec un score de ressemblance « sonore ».
2. Conventions : « ouvre X » = l'ouvrir dans l'éditeur ; « lance / démarre X » = le démarrer.
3. En cas de vrai doute seulement : UNE question courte (« Je l'ouvre dans VS Code ou je le démarre ? »).
4. La réponse est retenue pour ce nom : la prochaine fois, Madex fait directement ce que Madou avait choisi.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from pathlib import Path

from . import apps, config
from .projects import list_projects

PREFS = config.DATA / "preferences.json"
ACTIONS = {"editor": "l'ouvrir dans l'éditeur", "run": "le démarrer", "app": "lancer l'application", "site": "ouvrir le site"}


def _plain(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))


def _key(s: str) -> str:
    t = _plain(s)
    for a, b in (("eau", "o"), ("au", "o"), ("ph", "f"), ("qu", "k"), ("ck", "k"), ("h", ""), ("x", "ks"),
                 ("z", "s"), ("ai", "e"), ("ei", "e"), ("y", "i"), ("c", "k")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


def score(heard: str, name: str) -> float:
    a, b = _key(heard), _key(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if len(a) >= 4 and (a in b or b in a):
        r = max(r, 0.85)
    return r


def load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_pref(name: str, action: str):
    prefs = load_prefs()
    prefs[_plain(name)] = action
    config.DATA.mkdir(parents=True, exist_ok=True)
    PREFS.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")


def candidates(heard: str) -> dict:
    """Meilleur projet, meilleure appli, meilleur site pour ce qui a été entendu, avec leur score."""
    best_proj = max(((score(heard, p.name), p) for p in list_projects()), default=(0.0, None), key=lambda x: x[0])
    app = None
    try:
        found = apps.find_app(heard)
        if found:
            app = (max(score(heard, found[0]), 0.75 if apps.norm(heard) == found[0] else 0.0), found[0])
    except Exception:  # noqa: BLE001  (hors Windows)
        app = None
    site = next(((1.0, k) for k in apps.SITES if apps.norm(heard) == k), None)
    return {"project": best_proj if best_proj[1] else None, "app": app, "site": site}


def decide(verb: str, heard: str, said_project: bool = False, said_editor: bool = False) -> dict:
    """-> {"action": editor|run|app|site|ask|none, "project": Path|None, "app": str|None, "question": str, "options": {...}}"""
    c = candidates(heard)
    proj = c["project"][1] if c["project"] and c["project"][0] >= (0.6 if said_project else 0.72) else None
    app = c["app"][1] if c["app"] and c["app"][0] >= 0.75 else None
    site = c["site"][1] if c["site"] else None
    pref = load_prefs().get(_plain(proj.name if proj else heard))
    verb = verb.lower()
    if said_editor and proj:
        return {"action": "editor", "project": proj}
    if pref and (proj or app):
        return {"action": pref, "project": proj, "app": app}
    if proj and not app and not site:
        if verb.startswith(("ouvr", "affiche")):
            return {"action": "editor", "project": proj}
        if verb.startswith(("demarr", "run", "execut")):
            return {"action": "run", "project": proj}
        return {"action": "ask", "project": proj, "question": f"{proj.name} : je l'ouvre dans VS Code, ou je le démarre ?",
                "options": {"editor": proj, "run": proj}}
    if proj and (app or site):
        return {"action": "ask", "project": proj, "app": app,
                "question": f"Pour {proj.name} : le projet dans VS Code, ou l'application {app or site} ?",
                "options": {"editor": proj, "app": app or site}}
    if app:
        return {"action": "app", "app": app}
    if site:
        return {"action": "site", "app": site}
    return {"action": "none"}


ANSWERS = [
    ("editor", re.compile(r"\b(vs ?code|code|editeur|éditeur|ouvr|projet|le code|cursor)\b", re.I)),
    ("run", re.compile(r"\b(demarr|démarr|lance|run|execut|exécut|serveur|dev)\w*", re.I)),
    ("app", re.compile(r"\b(appli|application|app|programme|logiciel)\b", re.I)),
]


def parse_answer(text: str, options: dict) -> str | None:
    for action, rx in ANSWERS:
        if action in options and rx.search(text):
            return action
    if re.search(r"\b(premier|la premiere|le premier|1)\b", text, re.I):
        return list(options)[0]
    if re.search(r"\b(deuxieme|second|la seconde|le second|2)\b", text, re.I):
        return list(options)[1] if len(options) > 1 else None
    return None


# ------------------------------------------------------------ démarrer un projet
def run_command(project: Path) -> str | None:
    """Devine comment démarrer le projet (npm, Flutter, Spring, Django…)."""
    p = project
    pkg = p / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except ValueError:
            scripts = {}
        for s in ("dev", "start", "serve"):
            if s in scripts:
                runner = "pnpm" if (p / "pnpm-lock.yaml").exists() else "yarn" if (p / "yarn.lock").exists() else "npm"
                return f"{runner} run {s}" if runner == "npm" or s != "start" else f"{runner} {s}"
    if (p / "pubspec.yaml").exists():
        return "flutter run"
    if (p / "mvnw.cmd").exists() or (p / "pom.xml").exists():
        return ("mvnw.cmd" if (p / "mvnw.cmd").exists() else "mvn") + " spring-boot:run"
    if (p / "gradlew.bat").exists():
        return "gradlew.bat bootRun" if "spring" in _read(p / "build.gradle") + _read(p / "build.gradle.kts") else "gradlew.bat run"
    if (p / "manage.py").exists():
        return "python manage.py runserver"
    if (p / "artisan").exists():
        return "php artisan serve"
    for f in ("main.py", "app.py"):
        if (p / f).exists():
            return f"python {f}"
    return None


def _read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def start_in_terminal(project: Path, command: str):
    """Ouvre un terminal dans le projet et lance la commande (fenêtre visible, que Madou peut fermer)."""
    if os.name == "nt":
        subprocess.Popen(f'start "Madex · {project.name}" cmd /k "cd /d "{project}" && {command}"', shell=True)
    else:
        subprocess.Popen(command, cwd=project, shell=True)
