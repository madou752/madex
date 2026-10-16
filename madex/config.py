"""Réglages de MADEX — Madou's Automated Development EXecutive.

Tout ce que tu peux vouloir changer est ici. Le reste se règle à la voix.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()


def _data_dir() -> Path:
    """Mémoire de Madex (règles, clés, connexions, tâches…) : HORS du dossier du programme,
    pour qu'une mise à jour — même en remplaçant tout le dossier — ne l'efface jamais."""
    if os.environ.get("MADEX_DATA"):
        return Path(os.environ["MADEX_DATA"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local") / "MADEX"
    return HOME / ".madex"


DATA = _data_dir()
LEGACY_DATA = ROOT / "data"             # emplacement des versions ≤ 3.4


def _migrate():
    """Première fois en 3.5 : on recopie l'ancien data/ et l'ancien .env vers le nouvel emplacement."""
    marker = DATA / ".migration-3.5"
    if marker.exists():
        return
    import shutil
    DATA.mkdir(parents=True, exist_ok=True)
    if LEGACY_DATA.is_dir():
        for item in LEGACY_DATA.rglob("*"):
            dest = DATA / item.relative_to(LEGACY_DATA)
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            elif not dest.exists() and item.name not in (".runtime.json", "orbe.pid"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
    old_env = ROOT / ".env"
    if old_env.exists() and not (DATA / ".env").exists():
        shutil.copy2(old_env, DATA / ".env")
    marker.write_text("ok", encoding="utf-8")


try:
    _migrate()
except OSError as _exc:  # noqa: F841  (jamais bloquant)
    pass

# --- Projets ---------------------------------------------------------------
# Chaque sous-dossier est un projet (« Madex, sur le portfolio… »).
PROJECTS_DIRS = [Path(p) for p in os.environ["MADEX_PROJECTS"].split(os.pathsep)] \
    if os.environ.get("MADEX_PROJECTS") else [HOME / "Documents"]
EDITOR_COMMAND = "code"                 # VS Code ; "cursor" si besoin

# --- Modèles (aiguilleur) ----------------------------------------------------
MODEL_LIGHT = "haiku"                   # petites questions, mails, première relecture
MODEL_MAIN = "sonnet"                   # code, revue, conception
MODEL_CODE_SESSIONS = "sonnet"          # sessions de code en arrière-plan ("opus" = plus solide, plus de quota)
SESSION_TIMEOUT_MIN = 30                # conversation oubliée après 30 min de silence
BRAIN_TIMEOUT_S = 240

# --- Sessions de code --------------------------------------------------------
MAX_PARALLEL_TASKS = 2
TASK_TIMEOUT_MIN = 60
APPROVAL_TIMEOUT_S = 540                # une demande sans réponse est refusée (le hook Claude Code coupe à 600 s)

# --- Assistant perso -----------------------------------------------------------
MAIL_CHECK_MINUTES = 3                  # vérification des mails importants
FOCUS_PLAYLIST = "Deep Focus"           # playlist lancée en mode concentration (une des tiennes ou une publique)

# --- Interface ---------------------------------------------------------------
PORT = 8765
OPEN_UI_ON_START = True
UI_BROWSER = "chrome"                   # fenêtre Madex : "chrome" (Edge si Chrome absent) ou "edge"

# --- Voix ---------------------------------------------------------------------
# Clé ElevenLabs : dans le fichier .env (ELEVENLABS_API_KEY=...), jamais ici.
ELEVENLABS_MODEL = "eleven_flash_v2_5"  # parle français, latence ~75 ms, 2x moins de crédits ; "eleven_v4_turbo" = plus expressif
ELEVENLABS_VOICE_ID = ""                # vide = Madex choisit une voix masculine posée (Daniel, George…) au 1er lancement
TTS_VOICE = "fr-FR-HenriNeural"         # voix de secours gratuite (Edge)
TTS_RATE = "+5%"

# --- Oreilles ------------------------------------------------------------------
LANGUAGE = "fr"
WHISPER_SPOT_MODEL = "base"             # petit modèle qui guette « Madex » (rapide)
WHISPER_MODEL = "small"                 # modèle qui transcrit ta demande ("medium" = plus précis, plus lent)
WHISPER_HINT = ("Madex, React, Vue, TypeScript, Spring Boot, Kotlin, Flutter, PHP, Python, C#, SCSS, "
                "API, endpoint, commit, branche, merge, pull request, npm, Docker, spec, refacto")
SILENCE_SECONDS = 1.3                   # silence qui marque la fin de ta demande
MAX_RECORD_SECONDS = 30
WAKE_THRESHOLD = 0.5                    # seulement si tu ajoutes un modèle entraîné models/madex.onnx

# Orbe toujours visible (petite bulle par-dessus les fenêtres). False pour la désactiver.
SHOW_ORB = True
