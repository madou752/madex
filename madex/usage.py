"""Consommation Claude de Madou, à la demande.

Deux sources, toutes deux locales :
1. Les limites de l'abonnement (fenêtre de 5 h et de 7 jours, en %) : Claude Code les renvoie dans chaque
   réponse (`rate_limit_event`). Madex garde la dernière valeur reçue ; si elle est trop vieille, il fait
   un tout petit appel Haiku pour la rafraîchir.
2. Les tokens consommés : lus dans les journaux de Claude Code (~/.claude/projects/**/*.jsonl), comme ccusage.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import config

STORE = config.DATA / "conso.json"
LOGS = Path.home() / ".claude" / "projects"


class Usage:
    def __init__(self, claude: str):
        self.claude = claude
        self.lock = threading.Lock()
        self.limits: dict = json.loads(STORE.read_text()) if STORE.exists() else {}

    # ------------------------------------------------------------ limites de l'abonnement
    def record(self, event: dict):
        """Appelé pour chaque `rate_limit_event` vu dans un flux Claude Code (cerveau, sessions)."""
        info = event.get("rate_limit_info") or {}
        windows = info.get("unifiedWindows") or {}
        if not windows and not info:
            return
        with self.lock:
            self.limits = {"at": time.time(), "status": info.get("status", ""),
                           "five_hour": windows.get("five_hour") or {}, "seven_day": windows.get("seven_day") or {}}
            config.DATA.mkdir(parents=True, exist_ok=True)
            STORE.write_text(json.dumps(self.limits), encoding="utf-8")

    def refresh_limits(self, max_age_s: int = 600):
        if self.limits and time.time() - self.limits.get("at", 0) < max_age_s:
            return
        from .claude_cli import NO_WINDOW
        config.DATA.mkdir(parents=True, exist_ok=True)
        try:
            out = subprocess.run([self.claude, "-p", "--output-format", "stream-json", "--verbose", "--model", "haiku",
                                  "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
                                 input="Réponds juste: ok", capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60, cwd=config.DATA, creationflags=NO_WINDOW)
            for line in out.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "rate_limit_event":
                    self.record(ev)
        except Exception as exc:  # noqa: BLE001
            print(f"[conso] {exc}", flush=True)

    # ------------------------------------------------------------ tokens (journaux locaux)
    @staticmethod
    def tokens() -> dict:
        now = datetime.now().astimezone()
        today = now.date()
        week_start = today - timedelta(days=6)
        five_h = now - timedelta(hours=5)
        by_day: dict = defaultdict(int)
        by_model: dict = defaultdict(int)
        last5 = 0
        seen = set()
        if LOGS.exists():
            cutoff = time.time() - 8 * 86400
            for f in LOGS.rglob("*.jsonl"):
                try:
                    if f.stat().st_mtime < cutoff:
                        continue
                    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for line in lines:
                    if '"usage"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    msg = e.get("message") or {}
                    u = msg.get("usage")
                    ts = e.get("timestamp")
                    if not u or not ts:
                        continue
                    key = (msg.get("id"), e.get("requestId"))
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        when = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
                    except ValueError:
                        continue
                    if when.date() < week_start:
                        continue
                    n = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                                          "cache_creation_input_tokens", "cache_read_input_tokens"))
                    by_day[when.date().isoformat()] += n
                    by_model[(msg.get("model") or "?").replace("claude-", "")] += n if when.date() == today else 0
                    if when >= five_h:
                        last5 += n
        days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        return {"today": by_day.get(today.isoformat(), 0), "last5h": last5, "week": sum(by_day.values()),
                "days": [{"day": d, "tokens": by_day.get(d, 0)} for d in days],
                "models_today": {k: v for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]) if v}}

    def snapshot(self, refresh: bool = True) -> dict:
        if refresh:
            self.refresh_limits()
        return {"limits": self.limits, "tokens": self.tokens()}

    @staticmethod
    def _when(ts) -> str:
        if not ts:
            return ""
        d = datetime.fromtimestamp(ts)
        days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        return d.strftime("%Hh%M") if d.date() == datetime.now().date() else f"{days[d.weekday()]} {d.strftime('%Hh%M')}"

    def spoken(self) -> str:
        s = self.snapshot()
        lim, tok = s["limits"], s["tokens"]
        parts = []
        if lim.get("five_hour"):
            p = round(100 * float(lim["five_hour"].get("utilization", 0)))
            parts.append(f"Vous avez utilisé {p} pour cent de votre fenêtre de cinq heures"
                         + (f", remise à zéro à {self._when(lim['five_hour'].get('resetsAt'))}" if lim["five_hour"].get("resetsAt") else ""))
        if lim.get("seven_day"):
            parts.append(f"{round(100 * float(lim['seven_day'].get('utilization', 0)))} pour cent sur la semaine")
        parts.append(f"{_human(tok['today'])} tokens aujourd'hui")
        return ", ".join(parts) + ". Le détail est à l'écran, monsieur."


def _human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} millions de".replace(".", ",")
    if n >= 1000:
        return f"{round(n / 1000)} mille"
    return str(n)
