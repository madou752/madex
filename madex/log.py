"""Journal de MADEX : tout ce qui se passe, lisible par Madou (fenêtre JOURNAL) et par Claude (copier-coller).

- fichiers tournants dans %LOCALAPPDATA%\\MADEX\\logs (5 × 2 Mo, jamais plus) ;
- une ligne = heure, niveau, module, message, et le numéro de l'échange en cours (« tour ») ;
- les 800 dernières lignes restent en mémoire et partent en direct vers l'interface ;
- chronométrage de chaque échange : fin de la phrase de Madou → transcription → aiguillage → premier mot de
  Claude → premier son. C'est ce qui permet de trouver ce qui rend Madex lent.
- les anciens `print("[module] …")` sont aussi capturés (rien ne se perd).
"""
from __future__ import annotations

import collections
import io
import itertools
import logging
import logging.handlers
import re
import sys
import threading
import time

from . import config

LOG_DIR = config.DATA / "logs"
_ring: collections.deque = collections.deque(maxlen=800)
_listeners: list = []
_seq = itertools.count(1)
_local = threading.local()
_current_turn = {"id": 0, "t0": 0.0, "marks": {}}
_lock = threading.Lock()


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            item = {"n": next(_seq), "t": record.created, "lvl": record.levelname, "mod": record.name.replace("madex.", ""),
                    "msg": record.getMessage(), "turn": getattr(record, "turn", 0)}
            if record.exc_info:
                item["msg"] += " | " + logging.Formatter().formatException(record.exc_info).splitlines()[-1]
            _ring.append(item)
            for cb in list(_listeners):
                try:
                    cb(item)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass


class _TurnFilter(logging.Filter):
    def filter(self, record):
        record.turn = _current_turn["id"]
        return True


class _PrintCatcher(io.TextIOBase):
    """Redirige les print() historiques vers le journal (« [spotify] … » -> module spotify)."""

    def __init__(self, original, level):
        self.original, self.level, self.buf = original, level, ""

    def write(self, s):
        if self.original is not None:
            try:
                self.original.write(s)
            except Exception:  # noqa: BLE001
                pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.strip()
            if line and not line.startswith("> "):
                m = re.match(r"^\[([^\]]{1,20})\]\s*(.*)$", line)
                name, msg = (m.group(1).lower(), m.group(2)) if m else ("madex", line)
                lvl = logging.WARNING if re.search(r"erreur|error|échou|impossible|refus|indisponible|⚠", msg, re.I) else self.level
                logging.getLogger(name).log(lvl, msg)
        return len(s)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:  # noqa: BLE001
                pass


def setup():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_madex", False):
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-10s [t%(turn)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "madex.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    for h in (fh, _RingHandler()):
        h.addFilter(_TurnFilter())
        root.addHandler(h)
    root._madex = True
    for noisy in ("urllib3", "spotipy", "googleapiclient", "faster_whisper", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.stdout = _PrintCatcher(sys.stdout, logging.INFO)
    sys.stderr = _PrintCatcher(sys.stderr, logging.WARNING)


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)


def subscribe(cb):
    _listeners.append(cb)


def recent(since: int = 0, limit: int = 400) -> list[dict]:
    return [x for x in list(_ring) if x["n"] > since][-limit:]


# ------------------------------------------------------------ chronométrage des échanges
def new_turn(source: str, text: str = "") -> int:
    """Un nouvel échange commence (Madou a parlé ou écrit)."""
    with _lock:
        _current_turn["id"] += 1
        _current_turn["t0"] = time.monotonic()
        _current_turn["marks"] = {}
        tid = _current_turn["id"]
    get("tour").info(f"nouvel échange ({source}) : « {text[:120]} »")
    return tid


def mark(step: str, detail: str = ""):
    """Jalon de l'échange en cours : on note le temps écoulé depuis le début (une seule fois par jalon)."""
    with _lock:
        if not _current_turn["t0"] or step in _current_turn["marks"]:
            return
        dt = time.monotonic() - _current_turn["t0"]
        _current_turn["marks"][step] = dt
    get("chrono").info(f"{step} : {dt:.2f} s" + (f" ({detail})" if detail else ""))


def turn_id() -> int:
    return _current_turn["id"]
