"""Serveur local de MADEX : interface + API. N'écoute que sur 127.0.0.1, chaque ordre exige un jeton secret.

GET  /                         interface
GET  /events                   flux temps réel (SSE)
GET  /api/state                tâches, accords, règles, projets
POST /ask                      {text}                    -> une phrase de Madou
POST /api/guard                (hook des sessions)       -> décision, peut attendre Madou
POST /api/approvals/<id>       {decision, note?}         -> réponse de Madou (clic)
POST /api/tasks                {project, prompt, title?} -> lance une session
POST /api/tasks/<id>/reply     {text}
POST /api/tasks/<id>/stop
POST /api/commit               {project, message}        -> propose un commit (Madou valide dans l'interface)
POST /api/push                 {project}                 -> propose un push
POST /api/rules/propose        {category, level}         -> propose une règle (Madou confirme)
POST /api/rules/set            {category, level}         -> règle changée depuis l'interface
POST /api/rules/undo
"""
from __future__ import annotations

import html
import json
import queue
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .projects import list_projects

UI_FILE = config.ROOT / "ui" / "index.html"


class Hub:
    def __init__(self):
        self.clients: list[queue.Queue] = []    # fenêtres de l'interface
        self.orbs: list[queue.Queue] = []       # l'orbe (ne compte pas comme une fenêtre ouverte)
        self.ui_visible = False                 # la fenêtre Madex est-elle devant Madou ? (l'orbe se cache alors)
        self.lock = threading.Lock()
        self.madex = None
        self.state = "idle"
        self.token = secrets.token_urlsafe(24)
        self.history: list[dict] = []          # derniers échanges, pour l'interface qui s'ouvre en cours de route

    def emit(self, kind: str, **data):
        if kind == "state":
            self.state = data["value"]
        if kind == "level":                    # flux continu : pas d'historique
            pass
        elif kind in ("user", "answer", "answer_done"):
            self.history = (self.history + [{"who": "user" if kind == "user" else "madex", "text": data["text"]}])[-40:]
        payload = json.dumps({"kind": kind, **data}, ensure_ascii=False)
        with self.lock:
            for q in self.clients + self.orbs:
                q.put(payload)


hub = Hub()


def snapshot() -> dict:
    m = hub.madex
    return {
        "tasks": m.tasks.list() if m else [],
        "approvals": m.approvals.recent() if m else [],
        "rules": m.rules.public() if m else [],
        "projects": [p.name for p in list_projects()],
        "state": hub.state,
        "history": hub.history,
        "warning": m.version_warning if m else None,
        "voice": (m.mouth.usage if m and m.mouth else {}),
        "mic": bool(m and m.ears),
        "mic_on": bool(m and m.mic_on()),
        "context": (m.context.current if m else {}),
        "version": (m.updater.status.get("version") if m else ""),
        "mentor": ({"active": m.mentor.active, "project": m.mentor.project.name if m.mentor.project else "",
                    "frequent": m.mentor.frequent(), "review": m.mentor.last_review} if m else {}),
        "ui_visible": hub.ui_visible,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def _json(self, code: int, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if not self._host_ok():
            return self.send_error(403)
        if self.path == "/":
            body = UI_FILE.read_text(encoding="utf-8").replace("__MADEX_TOKEN__", hub.token).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/logo.svg", "/favicon.ico"):
            body = (config.ROOT / "assets" / "logo.svg").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.split("?")[0] == "/events":
            self._sse(orb="client=orb" in self.path)
        elif self.path.startswith("/api/logs"):
            from . import log
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._json(200, log.recent(int((q.get("since") or ["0"])[0] or 0)))
        elif self.path == "/api/usage":
            self._json(200, hub.madex.usage.snapshot())
        elif self.path == "/api/diagnostic":
            self._json(200, hub.madex.diagnostic())
        elif self.path == "/api/music/state":
            self._json(200, hub.madex.music_state())
        elif self.path == "/api/system":
            self._json(200, system_stats())
        elif self.path == "/api/state":
            self._json(200, snapshot())
        elif self.path == "/api/projects":
            self._json(200, [{"name": p.name, "path": str(p)} for p in list_projects()])
        elif self.path == "/api/tasks":
            self._json(200, hub.madex.tasks.list())
        elif self.path == "/api/rules":
            self._json(200, hub.madex.rules.public())
        elif self.path == "/api/connections":
            self._json(200, hub.madex.connections())
        elif self.path == "/api/connections/elevenlabs/voices":
            try:
                self._json(200, hub.madex.mouth.list_voices())
            except (ValueError, AttributeError) as exc:
                self._json(400, {"error": str(exc) or "Voix désactivée (mode muet)."})
        elif self.path.startswith("/callback/spotify"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                if "error" in q:
                    err = q["error"][0]
                    raise ValueError("Spotify a refusé les autorisations demandées (invalid_scope). Mettez Madex à jour, "
                                     "puis reconnectez Spotify." if "scope" in err else "Spotify a refusé la connexion.")
                hub.madex.music.callback(q.get("code", [""])[0], q.get("state", [""])[0])
                hub.emit("connections", changed="spotify")
                hub.madex.speaker.say("Spotify est connecté, monsieur. Quelle musique voulez-vous ?")
                msg = "Spotify est connecté à MADEX. Vous pouvez fermer cet onglet."
            except Exception as exc:  # noqa: BLE001
                msg = f"Connexion Spotify échouée : {exc}"
            body = f"<!doctype html><meta charset=utf-8><body style='background:#06060c;color:#00f0ff;font:20px sans-serif;padding:40px'>{html.escape(msg)}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Page de retour OAuth : aucun script ni ressource, même si un message passait l'échappement.
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def _sse(self, orb: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        group = hub.orbs if orb else hub.clients
        with hub.lock:
            group.append(q)
        q.put(json.dumps({"kind": "snapshot", **snapshot()}, ensure_ascii=False))
        try:
            while True:
                try:
                    self.wfile.write(f"data: {q.get(timeout=8)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with hub.lock:
                group.remove(q)
                none_left = not orb and not hub.clients
            if none_left:                                     # fenêtre fermée : l'orbe réapparaît
                hub.ui_visible = False
                hub.emit("ui_visible", visible=False)

    def do_POST(self):
        if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Madex-Token", ""), hub.token):
            return self.send_error(403)
        m, d = hub.madex, self._body()
        parts = [urllib.parse.unquote(p) for p in self.path.strip("/").split("/")]
        try:
            if parts == ["ask"]:
                threading.Thread(target=m.handle, args=(str(d.get("text", "")),), daemon=True).start()
                return self._json(202, {"ok": True})
            if parts == ["api", "music"]:
                what = m.music.play(str(d.get("query", "")), str(d.get("kind", "auto")))
                threading.Timer(0.3, m.music_started, args=(what,)).start()   # laisse le cerveau recevoir la réponse
                return self._json(200, {"playing": what, "note": "La musique joue : Madex se tait, n'ajoute rien."})
            if parts == ["api", "browser"]:
                task = str(d.get("task", "")).strip()
                if not task:
                    raise ValueError("tâche vide")
                threading.Thread(target=m.browse, args=(task, True), daemon=True).start()
                return self._json(200, {"ok": True, "note": "C'est lancé dans Chrome : Madex dira lui-même le résultat. "
                                        "Ne dis rien de plus, ne répète rien."})
            if parts == ["api", "mails"]:
                return self._json(200, m.mail.recent(unread_only=bool(d.get("unread", True)), days=int(d.get("days", 3))))
            if parts == ["api", "open"]:
                # liens cliqués dans la fenêtre Madex : ouverts dans le navigateur PAR DÉFAUT de Windows
                url = str(d.get("url", ""))
                if not url.startswith(("https://", "http://")):
                    raise ValueError("Lien refusé.")
                import webbrowser
                webbrowser.open(url)
                return self._json(200, {"ok": True})
            if parts == ["api", "show"]:
                m.ensure_ui()
                return self._json(200, {"ok": True})
            if parts == ["api", "listen"]:
                m.listen_now()
                return self._json(200, {"ok": True})
            if parts == ["api", "ui"]:
                hub.ui_visible = bool(d.get("visible"))
                hub.emit("ui_visible", visible=hub.ui_visible)
                return self._json(200, {"ok": True})
            if parts == ["api", "update", "upload"]:
                import base64
                data = base64.b64decode(str(d.get("content", "")))
                return self._json(200, {"result": m.updater.install_file(data, str(d.get("name", "madex.zip")))})
            if parts == ["api", "power"]:
                m.power(str(d.get("action", "")))
                return self._json(200, {"ok": True})
            if parts == ["api", "precision"]:
                if not m.ears:
                    raise ValueError("Le micro n'est pas activé (mode texte).")
                m.ears.transcriber.set_precision(str(d.get("choice", "auto")))
                return self._json(200, m.ears.transcriber.status())
            if parts == ["api", "mentor"]:
                threading.Thread(target=m.handle, args=("mode mentor" if d.get("on") else "fin du mode mentor",),
                                 daemon=True).start()
                return self._json(202, {"ok": True})
            if parts == ["api", "review"]:
                threading.Thread(target=m._review, args=(str(d.get("number", "")),), daemon=True).start()
                return self._json(202, {"ok": True})
            if parts == ["api", "notifs"]:
                threading.Thread(target=m._notifs, daemon=True).start()
                return self._json(202, {"ok": True})
            if parts == ["api", "mic"]:
                m.set_mic(bool(d.get("on", True)))
                return self._json(200, {"on": bool(d.get("on", True))})
            if parts == ["api", "window"]:
                m.ensure_ui()
                hub.emit("panel", pane=str(d.get("pane", "")))
                return self._json(200, {"ok": True})
            if len(parts) == 3 and parts[:2] == ["api", "test"]:
                return self._json(200, {"result": m.run_test(parts[2])})
            if parts == ["api", "music", "control"]:
                act = str(d.get("action", ""))
                if act not in ("pause", "resume", "next", "previous"):
                    raise ValueError("action inconnue")
                getattr(m.music, act)()
                return self._json(200, m.music_state())
            if parts == ["api", "guard"]:
                return self._json(200, m.guard(str(d.get("source", "")), str(d.get("tool_name", "")),
                                               d.get("tool_input") or {}))
            if len(parts) == 3 and parts[:2] == ["api", "approvals"]:
                a = m.decide(parts[2], str(d.get("decision", "")), str(d.get("note", "")))
                return self._json(200, a.public())
            if parts == ["api", "tasks"]:
                return self._json(201, m.tasks.start(d.get("project", ""), d.get("prompt", ""),
                                                     d.get("title", "")).public())
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "reply":
                return self._json(200, m.tasks.reply(parts[2], d.get("text", "Continue.")).public())
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "stop":
                return self._json(200, m.tasks.stop(parts[2]).public())
            if parts == ["api", "commit"]:
                return self._json(201, m.propose_commit(d.get("project", ""), d.get("message", "")).public())
            if parts == ["api", "push"]:
                return self._json(201, m.propose_push(d.get("project", "")).public())
            if parts == ["api", "rules", "propose"]:
                return self._json(201, m.propose_rule(d.get("category", ""), d.get("level", "")).public())
            if parts == ["api", "rules", "set"]:
                m.rules.set(d.get("category", ""), d.get("level", ""), why="Changé dans l'interface")
                hub.emit("rules", rules=m.rules.public())
                return self._json(200, m.rules.public())
            if parts[:2] == ["api", "connections"]:
                return self._connections(m, parts[2:], d)
            if parts == ["api", "rules", "undo"]:
                last = m.rules.undo()
                hub.emit("rules", rules=m.rules.public())
                return self._json(200, {"undone": last})
            self.send_error(404)
        except (ValueError, RuntimeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001  (jamais de trace brute : une ligne claire au journal)
            __import__("madex.log", fromlist=["x"]).get("serveur").error(f"{self.path} : {type(exc).__name__} : {exc}")
            self._json(500, {"error": str(exc)})


    def _connections(self, m, p: list[str], d: dict):
        from . import secrets_store
        if p == ["elevenlabs", "key"]:
            if not m.mouth:
                raise ValueError("Madex est lancé en mode muet.")
            m.mouth.set_key(str(d.get("key", "")))
        elif p == ["elevenlabs", "voice"]:
            m.mouth.set_voice(str(d.get("id", "")), str(d.get("name", "")))
            m.speaker.say("Voici ma nouvelle voix, monsieur. Elle vous convient ?")
        elif p == ["elevenlabs", "test"]:
            m.speaker.say("Bonjour monsieur. Tous mes systèmes vocaux sont opérationnels.")
        elif p == ["spotify", "client"]:
            secrets_store.set_value("SPOTIFY_CLIENT_ID", str(d.get("client_id", "")).strip())
            m.music._auth = None
        elif p == ["spotify", "reset"]:
            m.music.reset()
        elif p == ["spotify", "auth"]:
            return self._json(200, {"url": m.music.auth_url()})
        elif p == ["gmail", "client"]:
            m.mail.save_client(str(d.get("content", "")))
        elif p == ["gmail", "connect"]:
            m.mail.connect()
        else:
            return self.send_error(404)
        return self._json(200, m.connections())


def system_stats() -> dict:
    try:
        import psutil
    except ImportError:
        return {"error": "psutil manquant : relancez installer.bat"}
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(config.ROOT.anchor or "/"))
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    me = psutil.Process()
    return {"cpu": psutil.cpu_percent(interval=0.3), "ram": vm.percent, "ram_used": round(vm.used / 2**30, 1),
            "ram_total": round(vm.total / 2**30, 1), "disk": disk.percent, "disk_free": round(disk.free / 2**30),
            "battery": None if not bat else {"percent": round(bat.percent), "plugged": bat.power_plugged},
            "madex_ram": round(sum(p.memory_info().rss for p in [me] + me.children(recursive=True)) / 2**20)}


def _forward_logs():
    """Chaque ligne du journal part en direct vers la fenêtre JOURNAL."""
    from . import log
    log.subscribe(lambda item: hub.emit("log", entry=item))


def start_server():
    config.DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", config.PORT), Handler)
    _forward_logs()
    server.daemon_threads = True
    (config.DATA / ".runtime.json").write_text(json.dumps({"port": config.PORT, "token": hub.token}),
                                               encoding="utf-8")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
