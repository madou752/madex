"""Vocabulaire personnel et corrections apprises : Madex comprend de mieux en mieux Madou.

- vocabulaire : mots que Whisper doit savoir écrire (projets, artistes, termes dev, mots corrigés) ;
- corrections : « Nino » -> « Ninho », apprises quand Madou dit « non, j'ai dit Ninho ».
Les deux restent sur le PC (dossier de données de Madex).
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import unicodedata

from . import config

FILE = config.DATA / "vocabulaire.json"


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def _key(s: str) -> str:
    """Clé « sonore » grossière : Whisper confond souvent les lettres muettes et les doubles."""
    t = _plain(s).replace(" ", "")
    for a, b in (("eau", "o"), ("au", "o"), ("ph", "f"), ("qu", "k"), ("ck", "k"), ("h", ""), ("x", "ks"),
                 ("z", "s"), ("ai", "e"), ("ei", "e"), ("y", "i")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t.replace("c", "k"))


class Vocab:
    def __init__(self):
        self.lock = threading.Lock()
        d = {}
        if FILE.exists():
            try:
                d = json.loads(FILE.read_text(encoding="utf-8"))
            except ValueError:
                d = {}
        self.words: list[str] = d.get("words", [])
        self.fixes: dict[str, str] = d.get("fixes", {})

    def _save(self):
        config.DATA.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps({"words": self.words[-300:], "fixes": self.fixes}, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    def add_words(self, words):
        with self.lock:
            changed = False
            for w in words:
                w = str(w).strip()
                if w and w.lower() not in (x.lower() for x in self.words):
                    self.words.append(w)
                    changed = True
            if changed:
                self._save()

    def apply(self, text: str) -> str:
        """Applique les corrections apprises à une transcription."""
        for wrong, right in sorted(self.fixes.items(), key=lambda kv: -len(kv[0])):
            text = re.sub(rf"(?<!\w){re.escape(wrong)}(?!\w)", right, text, flags=re.I)
        return text

    def learn(self, last_text: str, right: str) -> str | None:
        """« non, j'ai dit Gazo » : trouve dans la dernière phrase le passage mal compris, retient la correction,
        et renvoie la phrase corrigée (None si rien de ressemblant)."""
        words = re.findall(r"[\w'-]+", last_text)
        if not words or not right:
            return None
        target = _key(right)
        n_right = max(1, len(right.split()))
        best, score = None, 0.0
        for size in {n_right, n_right + 1, max(1, n_right - 1)}:
            for i in range(0, len(words) - size + 1):
                span = " ".join(words[i:i + size])
                sc = difflib.SequenceMatcher(None, _key(span), target).ratio()
                if sc > score:
                    best, score = span, sc
        if not best or score < 0.45 or best.lower() == right.lower():
            return None
        with self.lock:
            self.fixes[best.lower()] = right
            if right.lower() not in (x.lower() for x in self.words):
                self.words.append(right)
            self._save()
        return re.sub(rf"(?<!\w){re.escape(best)}(?!\w)", right, last_text, count=1, flags=re.I)

    def hint(self, limit: int = 60) -> str:
        return ", ".join(self.words[-limit:])

    def status(self) -> dict:
        return {"words": len(self.words), "fixes": len(self.fixes),
                "last_fixes": [f"{k} → {v}" for k, v in list(self.fixes.items())[-5:]]}
