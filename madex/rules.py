"""Règles vivantes : ce que Madex et ses sessions peuvent faire seuls, demander, ou jamais.

Chaque action est rangée dans une catégorie. Chaque catégorie a un niveau :
  "autorise"  -> exécuté sans demander
  "demander"  -> Madex demande l'accord de Madou (une fois / pour la session / toujours)
  "interdit"  -> refusé
Les catégories verrouillées ne peuvent jamais changer (commit, push, destructions).
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass

from . import config

LEVELS = ("autorise", "demander", "interdit")


@dataclass(frozen=True)
class Category:
    key: str
    label: str            # lu à voix haute
    default: str
    locked: bool = False


CATEGORIES = [
    Category("lecture", "lire le code et l'état git", "autorise"),
    Category("web", "chercher sur le web", "autorise"),
    Category("madex", "piloter Madex lui-même", "autorise"),
    Category("fichiers", "créer ou modifier des fichiers", "demander"),
    Category("branches", "créer ou changer de branche", "demander"),
    Category("tests", "lancer les tests", "demander"),
    Category("build", "lancer le build ou le lint", "demander"),
    Category("installation", "installer des dépendances", "demander"),
    Category("execution", "exécuter un programme ou un script", "demander"),
    Category("suppression", "supprimer des fichiers", "demander"),
    Category("systeme", "agir sur Windows (applis, réglages)", "demander"),
    Category("navigateur", "lire et naviguer dans Chrome", "autorise"),
    Category("navigateur_action", "agir sur une page dans Chrome (cliquer, taper, envoyer)", "demander"),
    Category("autre", "toute autre commande", "demander"),
    # Verrouillées : les sessions ne peuvent JAMAIS les faire. Madex commit/push lui-même, après ton clic.
    Category("commit", "faire un commit", "interdit", locked=True),
    Category("push", "pousser sur le dépôt distant", "interdit", locked=True),
    Category("destruction", "détruire (rm -rf, reset --hard, clean, format)", "interdit", locked=True),
]
BY_KEY = {c.key: c for c in CATEGORIES}

# Ordre de sévérité pour une commande composée (« cd x && npm test ») : on garde la plus stricte.
SEVERITY = {"lecture": 0, "web": 0, "madex": 0, "navigateur": 0, "navigateur_action": 4, "tests": 2, "build": 2, "branches": 2, "fichiers": 3,
            "execution": 3, "installation": 4, "systeme": 4, "autre": 4, "suppression": 5,
            "commit": 9, "push": 9, "destruction": 10}

_B = r"(?:^|\s)"
PATTERNS: list[tuple[str, str]] = [
    # verrouillées d'abord
    ("destruction", r"\brm\s+(-\w*r\w*f|-\w*f\w*r)\b|\brm\s+-rf\b|git\s+reset\s+--hard|git\s+clean\b|\bformat\s+[a-z]:|"
                    r"remove-item\b.*-recurse|\bdel\s+/s\b|\brd\s+/s\b|\bshutdown\b|\bmkfs\b"),
    ("push", r"\bgit\s+push\b"),
    ("commit", r"\bgit\s+(commit|merge|rebase|cherry-pick|revert|tag|am)\b"),
    ("madex", r'^"?[^"\s]*(python3?|py|pythonw)(\.exe)?"?\s+"?[^"\s]*madexctl\.py"?(\s|$)'),
    ("lecture", r"^(git\s+(status|diff|log|show|branch\s*$|branch\s+(-a|-r|--list|--show-current|-v|-vv)|remote\s+-v|rev-parse|ls-files|blame)|"
                r"ls|dir|cat|type|head|tail|pwd|echo|wc|find|grep|rg|tree|which|where|get-childitem|get-content|cd)\b"),
    ("branches", r"^git\s+(switch|checkout|branch|stash|fetch|pull)\b"),
    ("suppression", r"^(rm|del|rmdir|rd|remove-item|git\s+rm)\b"),
    ("installation", r"((npm|pnpm|yarn|bun)\s+(i|install|add|ci)\b|pip3?\s+install|composer\s+(require|install|update)|"
                     r"flutter\s+pub\s+(get|add)|dotnet\s+add|winget\s+install|choco\s+install|npx\s+.*init)"),
    ("tests", r"(\btest\b|\btests\b|jest|vitest|pytest|phpunit|mocha|cypress|playwright\s+test|go\s+test)"),
    ("build", r"(\bbuild\b|\blint\b|\btsc\b|eslint|prettier|\bformat\b|mvn\s+(compile|package|verify)|"
              r"gradlew?\s+(build|assemble|check)|dotnet\s+build|flutter\s+analyze|stylelint|sass\b)"),
    ("systeme", r"(start-process|stop-process|taskkill|reg\s+add|set-itemproperty|powershell|explorer\.exe|start\s)"),
    ("execution", r"^(node|python3?|py|php|java|dotnet\s+run|npm\s+(run|start)|npx|pnpm|yarn|bun|deno|go\s+run|"
                  r"flutter\s+run|mvn|gradlew?|\./)"),
    ("fichiers", r"^(mkdir|touch|cp|copy|mv|move|new-item|ni)\b"),
]
_COMPILED = [(k, re.compile(p, re.I)) for k, p in PATTERNS]


def _strip_heredocs(command: str) -> str:
    """Le contenu d'un « cat > f <<'EOF' … EOF » n'est pas une commande : on le retire avant d'analyser."""
    return re.sub(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", "<<HEREDOC", command, flags=re.S)


def _split(command: str) -> list[str]:
    command = _strip_heredocs(command)
    parts = re.split(r"&&|\|\||;|\||\n", command)
    return [p.strip() for p in parts if p.strip()]


def classify_command(command: str) -> str:
    """Catégorie la plus stricte parmi les morceaux d'une commande shell."""
    whole = command.strip()
    for key in ("destruction", "push", "commit"):           # dangereux où qu'ils soient
        if dict(_COMPILED)[key].search(whole):
            return key
    worst, worst_sev = "lecture", -1
    for part in _split(whole) or [whole]:
        part = re.sub(r"^(\w+=(\"[^\"]*\"|'[^']*'|\S*)\s*)+", "", part).strip()   # VAR=val en tête
        if not part:
            continue
        cat = "autre"
        for key, rx in _COMPILED:
            if key in ("destruction", "push", "commit"):
                continue
            if rx.search(part):
                cat = key
                break
        # « echo x > fichier » ou « find -delete » ne sont pas de simples lectures
        if re.search(r"(?<![0-9&])>(?!&)", part) and SEVERITY[cat] < SEVERITY["fichiers"]:
            cat = "fichiers"
        if re.search(r"\s-delete\b", part):
            cat = "suppression"
        if SEVERITY[cat] > worst_sev:
            worst, worst_sev = cat, SEVERITY[cat]
    return worst


def classify(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Read", "Glob", "Grep", "LS", "TodoWrite", "TaskCreate", "TaskUpdate", "NotebookRead"):
        return "lecture"
    if tool_name in ("WebSearch", "WebFetch"):
        return "web"
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return "fichiers"
    if tool_name in ("Bash", "PowerShell"):
        return classify_command(str(tool_input.get("command", "")))
    return "autre"


def describe(tool_name: str, tool_input: dict) -> str:
    """Phrase courte, lisible et prononçable, de ce que la session veut faire."""
    if tool_name in ("Bash", "PowerShell"):
        return str(tool_input.get("command", ""))[:200]
    if tool_name in ("Write", "Edit", "MultiEdit"):
        verb = "créer" if tool_name == "Write" else "modifier"
        return f"{verb} {tool_input.get('file_path', '?')}"
    return f"{tool_name} {json.dumps(tool_input, ensure_ascii=False)[:150]}"


class RuleStore:
    """Niveaux par catégorie, persistés, modifiables à la voix, avec historique et annulation."""

    def __init__(self):
        self.file = config.DATA / "regles.json"
        self.lock = threading.Lock()
        self.levels = {c.key: c.default for c in CATEGORIES}
        self.history: list[dict] = []
        if self.file.exists():
            saved = json.loads(self.file.read_text(encoding="utf-8"))
            for k, v in saved.get("levels", {}).items():
                if k in BY_KEY and not BY_KEY[k].locked and v in LEVELS:
                    self.levels[k] = v
            self.history = saved.get("history", [])[-100:]

    def _save(self):
        config.DATA.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"levels": self.levels, "history": self.history[-100:]},
                                        ensure_ascii=False, indent=1), encoding="utf-8")

    def level(self, cat: str) -> str:
        return self.levels.get(cat, "demander")

    def set(self, cat: str, level: str, why: str = "") -> str:
        if cat not in BY_KEY:
            raise ValueError(f"Catégorie inconnue : {cat}. Choix : {', '.join(BY_KEY)}")
        if level not in LEVELS:
            raise ValueError(f"Niveau inconnu : {level}. Choix : {', '.join(LEVELS)}")
        if BY_KEY[cat].locked:
            raise ValueError(f"La règle « {BY_KEY[cat].label} » est verrouillée à vie.")
        with self.lock:
            old = self.levels[cat]
            self.levels[cat] = level
            self.history.append({"t": time.time(), "cat": cat, "old": old, "new": level, "why": why})
            self._save()
        return old

    def undo(self) -> dict | None:
        with self.lock:
            if not self.history:
                return None
            last = self.history.pop()
            self.levels[last["cat"]] = last["old"]
            self._save()
            return last

    def public(self) -> list[dict]:
        return [{"key": c.key, "label": c.label, "level": self.levels[c.key], "locked": c.locked,
                 "default": c.default} for c in CATEGORIES]
