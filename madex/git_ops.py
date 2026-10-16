"""Opérations git que SEUL Madex exécute, et seulement après l'accord de Madou.

Les sessions Claude Code n'ont jamais le droit de commit ou de push : elles proposent,
Madex montre le diff, Madou valide, et c'est ce module qui exécute.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w\-./ ]+\))?!?: \S.{0,99}$")
FORBIDDEN_TRAILERS = re.compile(r"^\s*(co-authored-by|generated with|🤖).*$", re.I | re.M)


def git(path: str | Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if check and out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip() or f"git {' '.join(args)} a échoué")
    return out.stdout


def is_repo(path: str | Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path,
                          capture_output=True, creationflags=NO_WINDOW).returncode == 0


def current_branch(path: str | Path) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()


def changes(path: str | Path, max_chars: int = 60000) -> dict:
    """Ce qui serait commité : fichiers modifiés, nouveaux fichiers, et le diff."""
    status = git(path, "status", "--porcelain", "-uall")
    files = []
    for line in status.splitlines():
        code, name = line[:2], line[3:]
        kind = {"??": "nouveau", " M": "modifié", "M ": "modifié", "MM": "modifié", " D": "supprimé",
                "D ": "supprimé", "A ": "ajouté", "R ": "renommé"}.get(code, code.strip() or "modifié")
        files.append({"file": name, "kind": kind})
    diff = git(path, "diff", "HEAD", check=False) if git(path, "rev-parse", "--verify", "HEAD",
                                                       check=False).strip() else ""
    for f in files:                       # montrer aussi le contenu des nouveaux fichiers
        if f["kind"] == "nouveau" and len(diff) < max_chars:
            p = Path(path) / f["file"]
            if p.is_file() and p.stat().st_size < 50_000:
                try:
                    body = p.read_text(encoding="utf-8")
                    diff += f"\n--- /dev/null\n+++ b/{f['file']}\n" + "".join(f"+{l}\n" for l in body.splitlines())
                except UnicodeDecodeError:
                    diff += f"\n(binaire) {f['file']}\n"
    truncated = len(diff) > max_chars
    return {"branch": current_branch(path), "files": files, "diff": diff[:max_chars], "truncated": truncated}


def clean_message(message: str) -> str:
    """Retire toute ligne d'attribution IA (règle absolue de Madou)."""
    msg = FORBIDDEN_TRAILERS.sub("", message)
    return re.sub(r"\n{3,}", "\n\n", msg).strip()


def check_message(message: str) -> str | None:
    first = message.strip().splitlines()[0] if message.strip() else ""
    if not CONVENTIONAL.match(first):
        return ("Le message doit suivre Conventional Commits, en anglais : "
                "« feat(scope): add dark mode », « fix: handle empty list »…")
    return None


def commit(path: str | Path, message: str) -> str:
    message = clean_message(message)
    problem = check_message(message)
    if problem:
        raise ValueError(problem)
    if not git(path, "status", "--porcelain").strip():
        raise ValueError("Rien à commiter.")
    git(path, "add", "-A")
    git(path, "commit", "-m", message)
    return git(path, "log", "-1", "--pretty=%h %s").strip()


def push(path: str | Path) -> str:
    branch = current_branch(path)
    has_upstream = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=path,
                                  capture_output=True, creationflags=NO_WINDOW).returncode == 0
    args = ["push"] if has_upstream else ["push", "-u", "origin", branch]
    out = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", creationflags=NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip())
    return f"Branche {branch} poussée."
