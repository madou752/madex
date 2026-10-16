"""La pastille de Madex : un mini réacteur toujours prêt, discret.

- MASQUÉE quand la fenêtre Madex est devant vous ; AFFICHÉE quand elle est fermée, réduite ou en arrière-plan ;
- disparaît quand Madex est éteint (ou injoignable plus de 15 s) ;
- clic : Madex écoute · double-clic : micro coupé / rallumé · clic droit : menu · glisser : la déplacer ;
- aimantée aux bords et aux coins de l'écran, ramenée à l'écran si la résolution ou l'écran change ;
- s'estompe après quelques minutes sans activité, revient quand Madex écoute ou parle, ou au survol.

Processus à part (lancé par Madex) : si la pastille plante, Madex continue. Bibliothèque standard (Tkinter).
"""
from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from madex.config import DATA  # noqa: E402

POS = DATA / "orbe.json"
KEY = "#010203"                      # couleur rendue transparente par Windows
SIZE = 68
C = SIZE // 2
SNAP = 28                            # distance d'aimantation aux bords (px)
MARGIN = 10
IDLE_FADE_S = 180                    # s sans activité avant de s'estomper
GONE_AFTER_S = 30                    # s sans nouvelles de Madex avant de disparaître
OFFLINE_GONE_S = 6                   # Madex éteint (connexion refusée) : disparition rapide

BLUE, ICE, WHITE, ORANGE, DIM, AMBER = "#2f7bff", "#9fd8ff", "#e8f4ff", "#ff8a3d", "#3a4a6a", "#ffb547"


def runtime():
    rt = json.loads((DATA / ".runtime.json").read_text(encoding="utf-8"))
    return rt["port"], rt["token"]


def post(path: str, data: dict | None = None):
    try:
        port, token = runtime()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(data or {}).encode(),
                                     method="POST", headers={"Content-Type": "application/json", "X-Madex-Token": token})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:  # noqa: BLE001
        print(f"[pastille] {exc}", flush=True)


def listen_events(q: queue.Queue):
    """Lit le flux d'événements de Madex. Coupure : on le signale, et on réessaie."""
    while True:
        try:
            port, _ = runtime()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/events?client=orb", timeout=30) as r:
                q.put({"kind": "_online"})
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:"):
                        try:
                            q.put(json.loads(line[5:]))
                        except ValueError:
                            pass
                    elif line.startswith(":"):
                        q.put({"kind": "_online"})
        except Exception:  # noqa: BLE001
            q.put({"kind": "_offline"})
            time.sleep(2)


def work_areas() -> list[tuple[int, int, int, int]]:
    """Zones utiles de chaque écran (sans la barre des tâches), en coordonnées bureau."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    areas = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    def cb(hmon, _hdc, _rect, _data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcWork
        areas.append((r.left, r.top, r.right, r.bottom))
        return True
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_double)
    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
    except Exception:  # noqa: BLE001
        return []
    return areas


class Orb:
    def __init__(self):
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(2)       # coordonnées justes sur écrans 4K / multiples
            except Exception:  # noqa: BLE001
                pass
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.root.configure(bg=KEY)
        self.cv = tk.Canvas(self.root, width=SIZE, height=SIZE, bg=KEY, highlightthickness=0, cursor="hand2")
        self.cv.pack()
        self.state, self.level, self.who, self.level_at = "offline", 0.0, "madex", 0.0
        self.mic_on, self.sessions, self.mentor = True, 0, False
        self.pending_ids: set = set()
        self.tasks: dict = {}
        self.ui_visible = False
        self.shown = True
        self.hover = False
        self.last_activity = time.time()
        self.last_online = time.time()
        self.offline_since: float | None = None
        self.alpha = 1.0
        self.t, self.energy = 0.0, 0.05
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=listen_events, args=(self.q,), daemon=True).start()
        self._drag = None
        self._click_job = None
        x, y = self._load_pos()
        self.root.geometry(f"{SIZE}x{SIZE}+{x}+{y}")
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Double-Button-1>", self._double)
        self.cv.bind("<Button-3>", self._menu)
        self.cv.bind("<Enter>", lambda e: self._set_hover(True))
        self.cv.bind("<Leave>", lambda e: self._set_hover(False))
        self.menu = tk.Menu(self.root, tearoff=0, bg="#0a1530", fg=WHITE, activebackground=BLUE, bd=0)
        self.menu.add_command(label="Ouvrir Madex", command=lambda: post("/api/show"))
        self.menu.add_command(label="Parler", command=lambda: post("/api/listen"))
        self.menu.add_command(label="Micro on / off", command=self._toggle_mic)
        self.menu.add_separator()
        self.menu.add_command(label="Cacher la pastille", command=self.root.destroy)
        self.root.after(5000, self._keep_on_screen)
        self._frame()

    # ----- position : aimantée, toujours sur un écran
    def _areas(self):
        areas = work_areas()
        return areas or [(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 40)]

    def _default_pos(self):
        _l, _t, r, b = self._areas()[0]
        return r - SIZE - MARGIN * 2, b - SIZE - MARGIN * 2

    def _load_pos(self):
        try:
            p = json.loads(POS.read_text())
            return self._clamp(int(p["x"]), int(p["y"]))
        except Exception:  # noqa: BLE001
            return self._default_pos()

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        """Ramène la pastille sur l'écran le plus proche (écran débranché, résolution changée…)."""
        cx, cy = x + C, y + C
        best = min(self._areas(), key=lambda a: (max(a[0] - cx, 0, cx - a[2]) ** 2 + max(a[1] - cy, 0, cy - a[3]) ** 2))
        l, t, r, b = best
        return min(max(x, l + MARGIN), r - SIZE - MARGIN), min(max(y, t + MARGIN), b - SIZE - MARGIN)

    def _snap(self, x: int, y: int) -> tuple[int, int]:
        x, y = self._clamp(x, y)
        cx, cy = x + C, y + C
        l, t, r, b = min(self._areas(), key=lambda a: 0 if a[0] <= cx <= a[2] and a[1] <= cy <= a[3] else 1)
        if x - l < SNAP:
            x = l + MARGIN
        elif r - (x + SIZE) < SNAP:
            x = r - SIZE - MARGIN
        if y - t < SNAP:
            y = t + MARGIN
        elif b - (y + SIZE) < SNAP:
            y = b - SIZE - MARGIN
        return x, y

    def _save_pos(self):
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            POS.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}))
        except OSError:
            pass

    def _keep_on_screen(self):
        try:
            if self.shown:
                x, y = self.root.winfo_x(), self.root.winfo_y()
                nx, ny = self._clamp(x, y)
                if (nx, ny) != (x, y):
                    self.root.geometry(f"+{nx}+{ny}")
            self.root.after(5000, self._keep_on_screen)
        except tk.TclError:
            pass

    # ----- souris : clic = écouter, double-clic = micro, glisser = déplacer
    def _set_hover(self, on: bool):
        self.hover = on
        if on:
            self.last_activity = time.time()

    def _press(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

    def _move(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy, _ = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 4:
            self._drag = (x0, y0, wx, wy, True)
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def _release(self, _e):
        moved = self._drag and self._drag[4]
        self._drag = None
        if moved:
            x, y = self._snap(self.root.winfo_x(), self.root.winfo_y())
            self.root.geometry(f"+{x}+{y}")
            self._save_pos()
            return
        if self._click_job:
            self.root.after_cancel(self._click_job)
        self._click_job = self.root.after(280, self._single)     # attend un éventuel double-clic

    def _single(self):
        self._click_job = None
        post("/api/listen")

    def _double(self, _e):
        if self._click_job:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        self._toggle_mic()

    def _toggle_mic(self):
        post("/api/mic", {"on": not self.mic_on})

    def _menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    # ----- événements de Madex
    def _drain(self):
        while True:
            try:
                m = self.q.get_nowait()
            except queue.Empty:
                return
            k = m.get("kind")
            if k != "_offline":
                self.offline_since = None
            if k == "_online":
                self.last_online = time.time()
            elif k == "_offline":
                self.state = "offline"
                self.offline_since = self.offline_since or time.time()
            elif k == "snapshot":
                self.last_online = time.time()
                self.state = m.get("state", "idle")
                self.mic_on = m.get("mic_on", True)
                self.ui_visible = bool(m.get("ui_visible"))
                self.mentor = bool((m.get("mentor") or {}).get("active"))
                self.pending_ids = {a["id"] for a in m.get("approvals", []) if a.get("status") == "pending"}
                self.tasks = {t["id"]: t.get("status") for t in m.get("tasks", [])}
                self.sessions = sum(1 for st in self.tasks.values() if st == "running")
            elif k == "state":
                self.last_online = time.time()
                self.state = m.get("value", "idle")
                if self.state == "mic_off":
                    self.mic_on = False
                elif self.state in ("listening", "followup"):
                    self.mic_on = True
                if self.state not in ("idle", "mic_off"):
                    self.last_activity = time.time()
            elif k == "ui_visible":
                self.ui_visible = bool(m.get("visible"))
            elif k == "mic":
                self.mic_on = bool(m.get("on"))
            elif k == "mentor":
                self.mentor = bool(m.get("active"))
            elif k == "level":
                self.last_online = time.time()
                self.level, self.who, self.level_at = float(m.get("v", 0)), m.get("who", "madex"), time.time()
            elif k == "approval":
                a = m.get("approval", {})
                (self.pending_ids.add if a.get("status") == "pending" else self.pending_ids.discard)(a.get("id"))
                self.last_activity = time.time()
            elif k == "task":
                t = m.get("task", {})
                self.tasks[t.get("id")] = t.get("status")
                self.sessions = sum(1 for st in self.tasks.values() if st == "running")

    def _visibility(self) -> bool:
        """Cachée quand la fenêtre Madex est devant Madou ; s'estompe au repos ; disparaît si Madex s'éteint."""
        now = time.time()
        if now - self.last_online > GONE_AFTER_S or (self.offline_since and now - self.offline_since > OFFLINE_GONE_S):
            self.root.destroy()
            return False
        want = not self.ui_visible
        if want != self.shown:
            (self.root.deiconify if want else self.root.withdraw)()
            if want:
                self.root.attributes("-topmost", True)
            self.shown = want
        busy = self.state not in ("idle", "offline", "mic_off") or self.pending_ids or self.hover
        target = 1.0 if busy or time.time() - self.last_activity < IDLE_FADE_S else 0.35
        if abs(target - self.alpha) > 0.01:
            self.alpha += (target - self.alpha) * 0.15
            try:
                self.root.attributes("-alpha", round(self.alpha, 2))
            except tk.TclError:
                pass
        return True

    # ----- dessin minimaliste (~30 i/s ; 10 i/s au repos)
    def _frame(self):
        self._drain()
        if not self._visibility():
            return
        if not self.shown:
            self.root.after(250, self._frame)
            return
        self.t += 1 / 30
        live = self.level if time.time() - self.level_at < 0.35 else 0.0
        base = {"idle": .03, "listening": .12, "followup": .07, "thinking": .1, "transcribing": .12,
                "speaking": .06}.get(self.state, .02)
        target = max(base, live ** .7)
        self.energy += (target - self.energy) * (.4 if target > self.energy else .08)
        madou = self.state in ("listening", "followup") or (self.who == "madou" and live > .02)
        off = not self.mic_on or self.state == "offline"
        accent = AMBER if self.mentor else BLUE
        col = DIM if off else (WHITE if madou else accent)
        cv = self.cv
        cv.delete("all")
        # octogone fin (le logo) : orange si Madex attend votre accord
        pts = []
        for k in range(8):
            a = self.t * .1 + math.radians(22.5 + 45 * k)
            pts += [C + 31 * math.cos(a), C + 31 * math.sin(a)]
        cv.create_polygon(pts, outline=ORANGE if self.pending_ids else (DIM if off else accent), fill="", width=1)
        # une couronne de points qui ondule avec la voix
        n = 28
        for i in range(n):
            th = i / n * 2 * math.pi + self.t * .4
            wav = .6 * math.sin(3 * th + self.t * 1.3) + .4 * math.sin(5 * th - self.t * .9)
            r = 23 + wav * (1 + 5 * self.energy)
            x, y = C + r * math.cos(th), C + r * math.sin(th)
            cv.create_oval(x - 1.1, y - 1.1, x + 1.1, y + 1.1, fill=col, outline="")
        # noyau qui respire + M
        breath = .5 + .5 * math.sin(self.t * 2 * math.pi / 4.6)
        rc = 11 * (.92 + .12 * breath + .3 * self.energy)
        cv.create_oval(C - rc - 3, C - rc - 3, C + rc + 3, C + rc + 3, fill="#0b1a3d", outline="")
        cv.create_oval(C - rc, C - rc, C + rc, C + rc, fill=col if not off else "#26364f", outline="")
        cv.create_line(C - 5, C + 4, C - 5, C - 4, C, C + 2, C + 5, C - 4, C + 5, C + 4, fill="#04060d", width=2)
        if off and self.state != "offline":                     # micro coupé : petite barre
            cv.create_line(C - 17, C + 17, C + 17, C - 17, fill=ORANGE, width=2)
        # indicateur discret : accord en attente (orange) ou session en cours (bleu glacier)
        if self.pending_ids or self.sessions:
            dot = ORANGE if self.pending_ids else ICE
            cv.create_oval(SIZE - 13, 5, SIZE - 5, 13, fill=dot, outline="#04060d", width=2)
        calm = self.state in ("idle", "offline", "mic_off") and not self.hover
        self.root.after(100 if calm else 33, self._frame)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if sys.stdout is None:
        DATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA / "orbe.log", "a", encoding="utf-8", buffering=1)
    Orb().run()
