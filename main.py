"""MADEX — Madou's Automated Development EXecutive.

    python main.py            mode complet : réveil vocal + voix + interface
    python main.py --texte    sans micro : tu écris (interface ou terminal), il répond à l'oral
    python main.py --muet     ni micro ni voix : idéal pour tester
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from madex import config
from madex.core import Madex
from madex.projects import list_projects
from madex.server import hub, start_server


def show_running_instance():
    """Madex tourne déjà : on lui demande d'afficher sa fenêtre."""
    try:
        rt = json.loads((config.DATA / ".runtime.json").read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{rt['port']}/api/show", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Madex-Token": rt["token"]})
        urllib.request.urlopen(req, timeout=5)
    except Exception:  # noqa: BLE001
        webbrowser.open(f"http://127.0.0.1:{config.PORT}")


ORB_PID = config.DATA / "orbe.pid"


def start_orb() -> subprocess.Popen | None:
    """L'orbe toujours visible : un petit processus à part (s'il plante, Madex continue)."""
    stop_orb()
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    try:
        flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
        proc = subprocess.Popen([exe, "-m", "madex.orb"], cwd=config.ROOT, creationflags=flags)
        ORB_PID.write_text(str(proc.pid))
        return proc
    except Exception as exc:  # noqa: BLE001
        print(f"[MADEX] orbe indisponible : {exc}")
        return None


def stop_orb():
    try:
        pid = int(ORB_PID.read_text())
        import psutil
        p = psutil.Process(pid)
        if "madex.orb" in " ".join(p.cmdline()):
            p.terminate()
    except Exception:  # noqa: BLE001
        pass
    ORB_PID.unlink(missing_ok=True)


def text_loop(m: Madex):
    print("[MADEX] Mode texte. Écrivez votre demande (« quitter » pour arrêter).")
    while not m.stop.is_set():
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quitter", "exit", "quit"):
            break
        m.handle(text)
    m.stop.set()


def test_voice():
    """Diagnostic : liste les micros/haut-parleurs, fait parler Madex, affiche le niveau du micro 5 secondes."""
    import time

    import numpy as np
    import sounddevice as sd

    from madex.tts import Mouth
    print("Périphériques audio (> = par défaut) :\n", sd.query_devices())
    mouth = Mouth(on_level=lambda *_: None, on_notice=print)
    time.sleep(2)
    print("Voix ElevenLabs :" if mouth.key else "Pas de clé ElevenLabs : voix de secours.", mouth.state.get("voice_name", ""))
    mouth.say("Bonjour monsieur. Ceci est un test de ma voix.")
    print("Parlez pendant 5 secondes…")
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1600) as s:
        for _ in range(50):
            f, _ = s.read(1600)
            lvl = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
            print("micro " + "█" * min(60, int(lvl / 100)), flush=True)
    print("Si les barres bougent quand vous parlez, le micro fonctionne.")
    return 0


def main():
    p = argparse.ArgumentParser(description="MADEX")
    p.add_argument("--texte", action="store_true", help="sans micro")
    p.add_argument("--muet", action="store_true", help="ni micro ni voix")
    p.add_argument("--sans-ui", action="store_true", help="ne pas ouvrir l'interface")
    p.add_argument("--fond", action="store_true", help="démarrer en arrière-plan : la fenêtre apparaît quand on l'appelle")
    p.add_argument("--test-voix", action="store_true", help="vérifier le micro et la voix, puis quitter")
    p.add_argument("--sans-orbe", action="store_true", help="ne pas afficher l'orbe toujours visible")
    p.add_argument("--redemarrage", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.test_voix:
        return test_voice()

    use_mic = not (a.texte or a.muet)
    from madex import log                  # journal : fichiers tournants + fenêtre JOURNAL (même sans console)
    log.setup()
    log.get("madex").info(f"démarrage de Madex {open(config.ROOT / 'VERSION').read().strip() if (config.ROOT / 'VERSION').exists() else ''}"
                          f" ({'micro' if use_mic else 'sans micro'}, {'voix' if not a.muet else 'muet'})")
    for attempt in range(40 if a.redemarrage else 1):        # après une mise à jour : on attend que l'ancien parte
        try:
            start_server()
            break
        except OSError:
            if a.redemarrage and attempt < 39:
                time.sleep(0.5)
                continue
            print("[MADEX] Madex tourne déjà : j'affiche simplement sa fenêtre.")
            show_running_instance()
            return 0
    m = Madex(hub.emit, use_mic=use_mic, use_voice=not a.muet)
    hub.madex = m
    projects = list_projects()
    print(f"[MADEX] Interface : http://127.0.0.1:{config.PORT}")
    print(f"[MADEX] {len(projects)} projet(s) : {', '.join(p.name for p in projects[:15]) or 'aucun'}")
    if m.version_warning:
        print(f"[MADEX] ⚠ {m.version_warning}")
    if config.OPEN_UI_ON_START and not a.sans_ui and not a.fond:
        m.ensure_ui()

    if not a.sans_orbe and config.SHOW_ORB:
        start_orb()
    if a.redemarrage:
        m.speaker.say(f"Mise à jour terminée. Version {m.updater.status['version']} en ligne, monsieur.")
    elif not a.fond:
        m.speaker.say("Systèmes en ligne. À votre service, monsieur.")
    if m.ears:
        m.ears.start()
        print("[MADEX] À l'écoute : dites « Madex » ou claquez deux fois des mains.")
    else:
        threading.Thread(target=text_loop, args=(m,), daemon=True).start()
    try:
        m.stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_orb()
        m.shutdown()
        print("\n[MADEX] Extinction.")


if __name__ == "__main__":
    sys.exit(main())
