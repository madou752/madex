"""Sessions Claude Code en arrière-plan, lancées et suivies par Madex.

Une tâche = un `claude -p` qui travaille dans le dossier d'un projet. Chacune de ses actions passe
par le garde-fou (règles vivantes + accord de Madou). Elle ne commit jamais : elle propose un message,
Madex affiche le diff, et c'est Madou qui valide.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, git_ops
from .claude_cli import base_command, events, kill_tree, spawn, tool_uses
from .projects import resolve

WORKER_PROMPT = """Tu es une session de développement de MADEX, l'assistant de Madou (développeur fullstack).
Personne ne regarde ton terminal : travaille en autonomie jusqu'au bout de la tâche.

Règles absolues :
- Tu ne fais JAMAIS de commit, de push, de merge ni de rebase : c'est Madex qui commit après validation de Madou.
- Chacune de tes actions passe par Madou. Si une action est refusée, ne la contourne pas : adapte-toi ou termine
  en expliquant ce dont tu as besoin.
- Branches : pour une NOUVELLE feature, crée une branche dédiée (`feat/<sujet-court>`, `fix/<sujet>`…).
  Pour CONTINUER une feature existante, reste sur sa branche ou bascule dessus. Ne travaille jamais sur main/master.
- Respecte les conventions officielles du langage et celles du projet (CLAUDE.md, config de lint, style existant).
- Écris le moins de code possible : réutilise l'existant, la bibliothèque standard, les dépendances déjà installées.
- Tu pars du principe que tu as tort : avant de dire « c'est fini », relis ton travail et lance les tests,
  le lint ou le build s'ils existent. Ne déclare jamais un succès que tu n'as pas vérifié.

Ton message final DOIT se terminer par ces lignes (la ligne vocale est lue à voix haute, en français oral, sans markdown) :
COMMIT PROPOSÉ: <message en anglais au format Conventional Commits, ex. feat(navbar): add dark mode toggle>
RÉSUMÉ VOCAL: <une ou deux phrases sur ce que tu as fait et vérifié>
Si tu n'as rien modifié, omets COMMIT PROPOSÉ. Si tu as besoin d'une décision de Madou, termine plutôt par :
QUESTION: <la question précise>
"""

STATUS_FR = {
    "queued": "en attente", "running": "en cours", "done": "terminée", "question": "attend ta réponse",
    "error": "en erreur", "stopped": "arrêtée",
}


@dataclass
class Task:
    id: str
    project: str
    path: str
    title: str
    status: str = "queued"
    activity: str = ""
    session_id: str | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    summary: str = ""
    result: str = ""
    commit_message: str = ""
    grants: list = field(default_factory=list)       # catégories autorisées « pour cette session »
    turns: int = 0

    def public(self) -> dict:
        d = asdict(self)
        d["status_fr"] = STATUS_FR.get(self.status, self.status)
        return d


def _describe(name: str, data: dict) -> str:
    if name in ("Edit", "Write", "Read", "MultiEdit"):
        verb = {"Edit": "Modifie", "MultiEdit": "Modifie", "Write": "Écrit", "Read": "Lit"}[name]
        return f"{verb} {Path(str(data.get('file_path', ''))).name}"
    if name == "Bash":
        return "$ " + str(data.get("command", ""))[:80]
    if name in ("Grep", "Glob"):
        return f"Cherche {data.get('pattern', '')}"[:80]
    if name.startswith("Web"):
        return "Recherche sur le web"
    return name


class TaskManager:
    def __init__(self, claude: str, emit, announce, on_commit_ready):
        self.claude = claude
        self.emit = emit
        self.announce = announce
        self.on_commit_ready = on_commit_ready      # (task) -> crée la demande de commit
        self.tasks: dict[str, Task] = {}
        self.procs: dict = {}
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(config.MAX_PARALLEL_TASKS)
        self.dir = config.DATA / "taches"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---------- persistance ----------
    def _load(self):
        f = self.dir / "taches.json"
        if f.exists():
            for d in json.loads(f.read_text(encoding="utf-8")):
                t = Task(**{k: v for k, v in d.items() if k in Task.__dataclass_fields__})
                if t.status in ("running", "queued"):
                    t.status = "stopped"
                t.grants = []
                self.tasks[t.id] = t

    def _save(self):
        recent = sorted(self.tasks.values(), key=lambda t: t.updated)[-60:]
        (self.dir / "taches.json").write_text(
            json.dumps([asdict(t) for t in recent], ensure_ascii=False, indent=1), encoding="utf-8")

    def changed(self, task: Task):
        task.updated = time.time()
        with self.lock:
            self._save()
        self.emit("task", task=task.public())

    # ---------- API ----------
    def list(self) -> list[dict]:
        return [t.public() for t in sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)]

    def get(self, ref: str) -> Task | None:
        ref = ref.strip().lower()
        if ref in self.tasks:
            return self.tasks[ref]
        matches = [t for t in self.tasks.values() if ref and ref in t.project.lower()]
        return max(matches, key=lambda t: t.updated) if matches else None

    def start(self, project: str, prompt: str, title: str = "") -> Task:
        path = resolve(project)
        if not path:
            raise ValueError(f"Projet introuvable : {project}")
        task = Task(id=uuid.uuid4().hex[:6], project=path.name, path=str(path),
                    title=title or prompt.strip().splitlines()[0][:80])
        self.tasks[task.id] = task
        self.changed(task)
        threading.Thread(target=self._run, args=(task, prompt), daemon=True).start()
        return task

    def reply(self, ref: str, text: str) -> Task:
        task = self.get(ref)
        if not task:
            raise ValueError(f"Tâche introuvable : {ref}")
        if task.status in ("running", "queued"):
            raise ValueError("Cette tâche est encore en cours.")
        task.status = "queued"
        self.changed(task)
        threading.Thread(target=self._run, args=(task, text), daemon=True).start()
        return task

    def stop(self, ref: str) -> Task:
        task = self.get(ref)
        if not task:
            raise ValueError(f"Tâche introuvable : {ref}")
        proc = self.procs.get(task.id)
        if proc:
            kill_tree(proc)
        task.status, task.activity = "stopped", ""
        self.changed(task)
        return task

    def stop_all(self):
        for tid in list(self.procs):
            self.stop(tid)

    # ---------- exécution ----------
    def _command(self, task: Task) -> list[str]:
        cmd = base_command(self.claude, config.MODEL_CODE_SESSIONS)
        cmd += ["--append-system-prompt", WORKER_PROMPT]
        if task.session_id:
            cmd += ["--resume", task.session_id]
        return cmd

    def _run(self, task: Task, prompt: str):
        with self.slots:
            if task.status == "stopped":
                return
            task.status, task.activity = "running", "Démarrage…"
            task.turns += 1
            self.changed(task)
            log = (self.dir / f"{task.id}.log").open("a", encoding="utf-8")
            log.write(f"\n===== Tour {task.turns} — {time.strftime('%H:%M:%S')} =====\n> {prompt}\n")
            result = None
            try:
                proc = spawn(self._command(task), Path(task.path), prompt, source=f"task:{task.id}")
                self.procs[task.id] = proc
                deadline = time.time() + config.TASK_TIMEOUT_MIN * 60
                for event in events(proc):
                    log.write(json.dumps(event, ensure_ascii=False)[:3000] + "\n")
                    if time.time() > deadline:
                        kill_tree(proc)
                        break
                    if event.get("type") == "system" and event.get("session_id"):
                        task.session_id = event["session_id"]
                    for name, data in tool_uses(event):
                        task.activity = _describe(name, data)
                        self.changed(task)
                    if event.get("type") == "result":
                        result = event
                proc.wait()
                if not result and task.status != "stopped":
                    log.write("[stderr] " + proc.stderr.read() + "\n")
            except Exception as exc:  # noqa: BLE001
                log.write(f"[exception] {exc}\n")
            finally:
                log.close()
                self.procs.pop(task.id, None)
            self._finish(task, result)

    def _finish(self, task: Task, ev: dict | None):
        if task.status == "stopped":
            return
        task.activity = ""
        name = f"la tâche sur {task.project}"
        if not ev:
            task.status = "error"
            task.summary = "La session s'est arrêtée sans résultat (délai dépassé ou connexion à Claude perdue)."
            self.changed(task)
            self.announce(f"Attention chef, {name} a échoué. Le journal est dans data, taches.")
            return

        task.session_id = ev.get("session_id") or task.session_id
        text = ev.get("result") or ""
        task.result = text
        question = re.search(r"QUESTION\s*:\s*(.+)", text, re.S)
        vocal = re.search(r"R[ÉE]SUM[ÉE] VOCAL\s*:\s*(.+?)(?:\n[A-ZÉ ]+:|\Z)", text, re.S)
        commit = re.search(r"COMMIT PROPOS[ÉE]\s*:\s*(.+)", text)
        task.commit_message = commit.group(1).strip().strip("`") if commit else ""

        if ev.get("is_error"):
            task.status = "error"
            task.summary = text[-300:] or "Erreur inconnue."
            self.changed(task)
            self.announce(f"{name.capitalize()} s'est terminée en erreur, chef.")
            return
        if question:
            task.status = "question"
            task.summary = question.group(1).strip()
            self.changed(task)
            self.announce(f"{name.capitalize()} a une question : {task.summary}")
            return

        task.status = "done"
        task.summary = (vocal.group(1).strip() if vocal else text[-300:]).strip()
        self.changed(task)
        has_changes = git_ops.is_repo(task.path) and bool(git_ops.git(task.path, "status", "--porcelain").strip())
        if has_changes:
            self.on_commit_ready(task)
            self.announce(f"{name.capitalize()} est terminée. {task.summary} "
                          "Le commit vous attend dans l'interface, monsieur.")
        else:
            self.announce(f"{name.capitalize()} est terminée. {task.summary}")
