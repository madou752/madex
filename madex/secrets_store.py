"""Clés secrètes (ElevenLabs, Spotify…) lues depuis le fichier `.env` du dossier de données de MADEX
(%LOCALAPPDATA%\\MADEX\\.env), à l'abri des mises à jour.

Ce fichier ne quitte jamais ton PC, n'est jamais affiché et n'est jamais envoyé à Claude.
"""
from __future__ import annotations

import os

from . import config

ENV_FILE = config.DATA / ".env"
LEGACY_ENV = config.ROOT / ".env"


def get(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name].strip()
    for f in (ENV_FILE, LEGACY_ENV):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == name and value.strip():
                    return value.strip().strip('"').strip("'")
    return ""


def set_value(name: str, value: str):
    config.DATA.mkdir(parents=True, exist_ok=True)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if l.partition("=")[0].strip() != name] + [f"{name}={value}"]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
