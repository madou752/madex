"""Hook PreToolUse de Claude Code : chaque action d'une session passe par ici avant d'être exécutée.

Il demande la décision à MADEX (règles vivantes + accord de Madou si besoin) et attend la réponse.
En cas de problème (Madex éteint, erreur…), l'action est REFUSÉE : le garde-fou échoue toujours fermé.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from madex.config import DATA  # noqa: E402  (dossier de données hors du programme)
RUNTIME = DATA / ".runtime.json"


def answer(decision: str, reason: str):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        answer("deny", "MADEX : requête illisible, action refusée par sécurité.")
    try:
        rt = json.loads(RUNTIME.read_text(encoding="utf-8"))
        body = json.dumps({
            "source": os.environ.get("MADEX_SOURCE", "inconnu"),
            "tool_name": event.get("tool_name", ""),
            "tool_input": event.get("tool_input") or {},
            "cwd": event.get("cwd", ""),
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{rt['port']}/api/guard", data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Madex-Token": rt["token"]})
        with urllib.request.urlopen(req, timeout=590) as r:
            res = json.loads(r.read())
        answer("allow" if res.get("decision") == "allow" else "deny", res.get("reason", ""))
    except Exception as exc:  # noqa: BLE001
        answer("deny", f"MADEX injoignable ({exc.__class__.__name__}) : action refusée par sécurité.")


if __name__ == "__main__":
    main()
