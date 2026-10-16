"""Demandes d'accord en attente : permissions des sessions, commits, push, changements de règles.

Une demande bloque celui qui l'a émise (une session Claude Code via son hook, par exemple)
jusqu'à ce que Madou réponde, à la voix ou d'un clic.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import asdict, dataclass, field

_ids = itertools.count(1)

CHOICES = {
    "tool": ["une_fois", "session", "toujours", "refuser"],
    "commit": ["valider", "refuser"],
    "push": ["valider", "refuser"],
    "rule": ["valider", "refuser"],
}


@dataclass
class Approval:
    id: str
    kind: str                     # tool | commit | push | rule
    title: str                    # une phrase lisible
    detail: dict = field(default_factory=dict)
    source: str = ""              # id de tâche, « madex »…
    created: float = field(default_factory=time.time)
    status: str = "pending"       # pending | done | expired
    decision: str = ""
    note: str = ""                # message de commit modifié, raison du refus…

    def public(self) -> dict:
        d = asdict(self)
        d["choices"] = CHOICES[self.kind]
        return d


class Approvals:
    def __init__(self, emit):
        self.emit = emit
        self.items: dict[str, Approval] = {}
        self.events: dict[str, threading.Event] = {}
        self.callbacks: dict[str, callable] = {}
        self.lock = threading.Lock()

    def create(self, kind: str, title: str, detail: dict | None = None, source: str = "",
               on_decide=None) -> Approval:
        a = Approval(id=f"a{next(_ids)}", kind=kind, title=title, detail=detail or {}, source=source)
        with self.lock:
            self.items[a.id] = a
            self.events[a.id] = threading.Event()
            if on_decide:
                self.callbacks[a.id] = on_decide
        self.emit("approval", approval=a.public())
        return a

    def wait(self, a: Approval, timeout: float) -> Approval:
        if not self.events[a.id].wait(timeout):
            self.decide(a.id, "refuser", "Pas de réponse de Madou à temps.", expired=True)
        return a

    def decide(self, approval_id: str, decision: str, note: str = "", expired: bool = False) -> Approval:
        with self.lock:
            a = self.items.get(approval_id)
            if not a:
                raise ValueError("Demande introuvable.")
            if a.status != "pending":
                raise ValueError("Cette demande a déjà reçu une réponse.")
            if decision not in CHOICES[a.kind]:
                raise ValueError(f"Réponse invalide : {decision}")
            a.status, a.decision, a.note = ("expired" if expired else "done"), decision, note
            cb = self.callbacks.pop(a.id, None)
        self.emit("approval", approval=a.public())
        if cb:
            threading.Thread(target=cb, args=(a,), daemon=True).start()
        self.events[a.id].set()
        return a

    def pending(self) -> list[Approval]:
        return [a for a in self.items.values() if a.status == "pending"]

    def oldest_pending(self) -> Approval | None:
        p = self.pending()
        return min(p, key=lambda a: a.created) if p else None

    def recent(self, n: int = 30) -> list[dict]:
        return [a.public() for a in sorted(self.items.values(), key=lambda a: a.created)[-n:]]
