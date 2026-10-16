"""Découverte de tes projets et reconnaissance de leur nom, même mal transcrit par Whisper."""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import config


def _is_madex(p: Path) -> bool:
    """Le dossier de Madex lui-même n'est pas un projet : il ne doit pas modifier son propre code."""
    try:
        return p.resolve() == config.ROOT.resolve()
    except OSError:
        return False


def list_projects() -> list[Path]:
    found = {}
    for base in config.PROJECTS_DIRS:
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "node_modules" and not _is_madex(p):
                found.setdefault(p.name.lower(), p)
    return list(found.values())


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve(name: str) -> Path | None:
    """« porte folio », « Portfolio-Website » ou un chemin complet -> dossier du projet."""
    if not name:
        return None
    direct = Path(name).expanduser()
    if direct.is_absolute() and direct.is_dir():
        return direct
    projects = list_projects()
    by_norm = {_norm(p.name): p for p in projects}
    key = _norm(name)
    if key in by_norm:
        return by_norm[key]
    for k, p in by_norm.items():          # « portfolio » -> « portfolio-website »
        if key and (key in k or k in key):
            return p
    close = difflib.get_close_matches(key, list(by_norm), n=1, cutoff=0.6)
    return by_norm[close[0]] if close else None
