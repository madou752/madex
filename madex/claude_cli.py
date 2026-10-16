"""Pilotage du CLI `claude` (Claude Code) en mode non-interactif, avec le garde-fou de Madex branché."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

MIN_VERSION = (2, 1, 259)
GUARD = (config.ROOT / "hooks" / "guard.py").as_posix()
# Actions interdites aux sessions, quoi qu'il arrive (le garde-fou les refuse aussi).
HARD_DENY = ("Bash(git push *),Bash(git commit *),Bash(git reset --hard *),Bash(git clean *),"
             "Bash(rm -rf *),Bash(git rebase *),Bash(git merge *),Read(**/.env),Read(**/.env.*),"
             # Jetons OAuth (Google, Spotify) et jeton de l'API locale : jamais lisibles par une session.
             "Read(**/*token*.json),Read(**/google_client.json),Read(**/client_secret*.json),Read(**/.runtime.json)")
# Seules les lectures passent sans le garde-fou ; tout le reste est décidé par Madex.
READ_ONLY = "Read,Glob,Grep,LS"
# Pas de fenêtre noire qui clignote à chaque appel quand Madex tourne en arrière-plan.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_claude() -> str:
    """Trouve Claude Code, même s'il n'est pas dans le PATH (installeur officiel : ~/.local/bin)."""
    path = shutil.which("claude")
    if path:
        return path
    home = Path.home()
    env = os.environ
    cands = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
             Path(env.get("APPDATA", "")) / "npm" / "claude.cmd", home / ".claude" / "local" / "claude.exe",
             home / ".claude" / "local" / "claude", Path(env.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
             home / ".npm-global" / "bin" / "claude", Path("/usr/local/bin/claude")]
    for c in cands:
        if str(c) not in ("npm/claude.cmd",) and c.is_file():
            os.environ["PATH"] = str(c.parent) + os.pathsep + env.get("PATH", "")   # pour les sous-processus aussi
            return str(c)
    return ""


def check_version(claude: str) -> str | None:
    try:
        out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20).stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        if m and tuple(map(int, m.groups())) < MIN_VERSION:
            return f"Claude Code {m.group(0)} est trop ancien : lance `claude update` (minimum 2.1.259)."
    except Exception:  # noqa: BLE001
        return None
    return None


def python_exe() -> str:
    """Le python AVEC console : pythonw (démarrage silencieux) n'a pas de sortie, le hook resterait muet."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        exe = exe.parent / "python.exe"
    return exe.as_posix()


# Mode mentor : les sessions et le cerveau adoptent le style officiel « Learning » de Claude Code.
OUTPUT_STYLE = {"value": ""}


def settings_json() -> str:
    """Réglages injectés à chaque appel : garde-fou sur toute action + aucune attribution IA dans git."""
    py = python_exe()
    extra = {"outputStyle": OUTPUT_STYLE["value"]} if OUTPUT_STYLE["value"] else {}
    return json.dumps({
        **extra,
        "attribution": {"commit": "", "pr": ""},
        "hooks": {"PreToolUse": [{
            "matcher": "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|WebFetch|WebSearch|mcp__claude-in-chrome__.*",
            "hooks": [{"type": "command", "command": f'"{py}" "{GUARD}"', "timeout": 600}],
        }]},
    })


def base_command(claude: str, model: str) -> list[str]:
    return [claude, "-p", "--output-format", "stream-json", "--verbose", "--model", model,
            "--settings", settings_json(), "--permission-mode", "default",
            "--allowedTools", READ_ONLY, "--disallowedTools", HARD_DENY]


def spawn(cmd: list[str], cwd: Path, prompt: str, source: str) -> subprocess.Popen:
    env = {**os.environ, "MADEX_SOURCE": source}
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
    return proc


def events(proc: subprocess.Popen):
    """Événements JSON du flux `--output-format stream-json`."""
    for line in proc.stdout:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def tool_uses(event: dict):
    if event.get("type") == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                yield block.get("name", "Outil"), block.get("input", {}) or {}


def kill_tree(proc: subprocess.Popen):
    """Arrête Claude et ses sous-processus (sous Windows, `claude` passe souvent par un .cmd)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
