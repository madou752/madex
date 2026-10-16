"""Madex <-> Claude dans Chrome (l'extension officielle « Claude » du Chrome Web Store).

Comment ça marche :
- une session Claude Code À PART, lancée avec `--chrome`, pilote votre vrai Chrome (vos comptes déjà connectés) ;
  le cerveau principal reste rapide (les outils du navigateur alourdissent chaque réponse), il passe la main ici ;
- cette session reste ouverte en continu, comme le cerveau (pas de démarrage à chaque demande) ;
- chaque action passe par le garde-fou de Madex :
    lire, naviguer, capturer           -> libre (catégorie « navigateur »)
    cliquer, taper, formulaire, fichier, JavaScript -> votre accord (catégorie « navigateur_action »)
    sites interdits (pro, Discord, banques, paiement) -> refusé, même avec votre accord
    Gmail                               -> lecture seule : aucune action (Madex n'envoie jamais de mail)
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import config, log, secrets_store

PREFIX = "mcp__claude-in-chrome__"
EXTENSION_ID = "fcoeoabgfenejglbffodgkkbkcdhcgfn"
STORE_URL = f"https://chromewebstore.google.com/detail/claude/{EXTENSION_ID}"
PROMPT = config.ROOT / "prompts" / "navigateur.md"

# Outils qui ne font que regarder (aucun effet sur les sites)
READ_TOOLS = {"tabs_context_mcp", "tabs_context", "tabs_create_mcp", "tabs_create", "tabs_close_mcp", "tabs_close",
              "navigate", "read_page", "get_page_text", "find", "read_console_messages", "read_network_requests",
              "resize_window", "list_connected_browsers", "select_browser", "switch_browser", "gif_creator",
              "shortcuts_list", "tabs_select"}
READ_COMPUTER = {"screenshot", "scroll", "scroll_to", "zoom", "wait", "hover", "mouse_move", "cursor_position"}

# ---------------------------------------------------------------- sites interdits
# Interdits complètement (même lire) : Discord (script sur compte perso = contraire à leurs règles),
# plus vos sites pro, à lister dans le .env : MADEX_BLOCKED_HOSTS=monentreprise,intranet (séparés par des virgules).
BLOCKED_HOSTS = [r"(^|\.)discord(app)?\.(com|gg)$"] + [
    rf"(^|[.-]){re.escape(d.strip().lower())}([.-]|$)"
    for d in secrets_store.get("MADEX_BLOCKED_HOSTS").split(",") if d.strip()]
# Banques et paiement : interdits complètement.
BANK_HOSTS = [r"bank", r"banque", r"paypal", r"boursorama", r"credit-?agricole", r"societegenerale", r"sg\.fr$",
              r"bnpparibas", r"mabanque", r"(^|\.)lcl\.fr$", r"caisse-?epargne", r"labanquepostale", r"revolut",
              r"lydia-app", r"(^|\.)n26\.com$", r"fortuneo", r"hellobank", r"monabanq", r"(^|\.)cic\.fr$",
              r"creditmutuel", r"qonto", r"wise\.com$", r"stripe\.com$", r"klarna", r"alma\.eu$", r"(^|\.)oney\.fr$"]
PAYMENT_PATH = re.compile(r"(checkout|paiement|payment|/pay\b|/cart/pay|/billing|commande/valid)", re.I)
READ_ONLY_HOSTS = [r"(^|\.)mail\.google\.com$", r"(^|\.)gmail\.com$"]


def host_of(url: str) -> str:
    try:
        u = url if "://" in url else "https://" + url
        return (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""


def site_verdict(url: str) -> tuple[str, str]:
    """-> ("ok" | "lecture" | "interdit", raison)."""
    h = host_of(url)
    if not h:
        return "ok", ""
    if any(re.search(p, h) for p in BLOCKED_HOSTS):
        return "interdit", f"{h} est interdit à Madex (site pro ou Discord)."
    if any(re.search(p, h) for p in BANK_HOSTS):
        return "interdit", f"{h} est un site bancaire ou de paiement : interdit à Madex."
    if PAYMENT_PATH.search(url):
        return "lecture", "page de paiement : Madex ne peut que la lire."
    if any(re.search(p, h) for p in READ_ONLY_HOSTS):
        return "lecture", "Gmail est en lecture seule : Madex n'envoie, ne supprime et ne modifie aucun mail."
    return "ok", ""


def short(tool_name: str) -> str:
    return tool_name[len(PREFIX):] if tool_name.startswith(PREFIX) else tool_name


def is_browser_tool(tool_name: str) -> bool:
    return tool_name.startswith(PREFIX)


def _batch_actions(tool_input: dict) -> list[tuple[str, dict]]:
    out = []
    for step in tool_input.get("actions") or tool_input.get("steps") or []:
        if isinstance(step, dict):
            name = step.get("tool") or step.get("name") or step.get("action") or ""
            out.append((short(str(name)), step.get("input") or step.get("args") or step))
    return out


def _is_read(name: str, tool_input: dict) -> bool:
    if name == "computer":
        return str(tool_input.get("action", "")).lower() in READ_COMPUTER
    if name in ("browser_batch",):
        steps = _batch_actions(tool_input)
        return bool(steps) and all(_is_read(n, i) for n, i in steps)
    return name in READ_TOOLS


class TabTracker:
    """Retient l'adresse de chaque onglet (d'après les navigations demandées) pour juger les clics et la saisie."""

    def __init__(self):
        self.urls: dict[str, str] = {}
        self.last = ""

    def note(self, name: str, tool_input: dict):
        url = str(tool_input.get("url") or "")
        if url and name in ("navigate", "tabs_create_mcp", "tabs_create"):
            tab = str(tool_input.get("tabId") or tool_input.get("tab_id") or "")
            if tab:
                self.urls[tab] = url
            self.last = url

    def url_for(self, tool_input: dict) -> str:
        tab = str(tool_input.get("tabId") or tool_input.get("tab_id") or "")
        return self.urls.get(tab, self.last)


def classify(tool_name: str, tool_input: dict, tracker: TabTracker) -> tuple[str, str, str]:
    """-> (catégorie, url concernée, refus éventuel). Catégories : navigateur | navigateur_action."""
    name = short(tool_name)
    target = str(tool_input.get("url") or "") if name in ("navigate", "tabs_create_mcp", "tabs_create") else ""
    url = target or tracker.url_for(tool_input)
    verdict, why = site_verdict(url) if url else ("ok", "")
    if name == "browser_batch":
        for n, i in _batch_actions(tool_input):
            if n == "navigate" and i.get("url"):
                v, w = site_verdict(str(i["url"]))
                if v == "interdit":
                    return "navigateur", str(i["url"]), w
    if verdict == "interdit":
        return "navigateur", url, why
    read = _is_read(name, tool_input)
    if not read and verdict == "lecture":
        return "navigateur_action", url, why
    return ("navigateur" if read else "navigateur_action"), url, ""


def describe(tool_name: str, tool_input: dict, url: str) -> str:
    """Phrase courte et prononçable de ce que le navigateur veut faire."""
    name = short(tool_name)
    where = host_of(url).removeprefix("www.") or "la page"
    act = str(tool_input.get("action", "")).lower()
    text = str(tool_input.get("text") or tool_input.get("value") or "")[:40]
    if name in ("tabs_context_mcp", "tabs_context", "list_connected_browsers"):
        return "voir les onglets ouverts"
    if name in ("tabs_create_mcp", "tabs_create"):
        return f"ouvrir un onglet" + (f" sur {where}" if tool_input.get("url") else "")
    if name == "navigate":
        return f"aller sur {where}"
    if name in ("get_page_text", "read_page", "find"):
        return f"lire la page sur {where}"
    if name in ("read_console_messages", "read_network_requests"):
        return f"lire la console sur {where}"
    if name == "computer":
        if "click" in act:
            return f"cliquer sur {where}"
        if act == "type":
            return f"taper « {text} » sur {where}"
        if act == "key":
            return f"appuyer sur {text or 'une touche'} sur {where}"
        if "drag" in act:
            return f"glisser un élément sur {where}"
        if act == "screenshot":
            return f"faire une capture sur {where}"
        return f"{act or 'agir'} sur {where}"
    if name == "form_input":
        return f"remplir un champ sur {where}" + (f" avec « {text} »" if text else "")
    if name in ("file_upload", "upload_image"):
        return f"envoyer un fichier sur {where}"
    if name == "javascript_tool":
        return f"exécuter du JavaScript sur {where}"
    if name == "shortcuts_execute":
        return f"lancer un raccourci de l'extension sur {where}"
    if name == "browser_batch":
        return f"enchaîner {len(_batch_actions(tool_input))} actions sur {where}"
    return f"{name} sur {where}"


# ---------------------------------------------------------------- la session navigateur
def make_agent(claude: str, on_activity, on_rate_limit):
    from .brain import Brain
    return Brain(claude, on_activity, on_rate_limit, name="navigateur", source="navigateur",
                 extra_args=("--chrome",), prompt_src=PROMPT, prompt_name="_prompt_navigateur.md",
                 workspace_name="navigateur")


# ---------------------------------------------------------------- diagnostic
def _chrome_exe() -> str:
    env = os.environ
    for base in (env.get("PROGRAMFILES", ""), env.get("PROGRAMFILES(X86)", ""), env.get("LOCALAPPDATA", "")):
        p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and p.exists():
            return str(p)
    for name in ("google-chrome", "google-chrome-stable", "chromium"):
        from shutil import which
        if which(name):
            return which(name)
    return ""


def extension_installed() -> str:
    """Nom du profil Chrome où l'extension est installée ('' si introuvable)."""
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    if not root.exists():
        root = Path.home() / ".config" / "google-chrome"
    try:
        for prof in root.iterdir():
            if (prof / "Extensions" / EXTENSION_ID).exists():
                return prof.name
    except OSError:
        pass
    return ""


def native_host_ok() -> bool:
    """Le « pont » entre Chrome et Claude Code (créé par Claude Code au premier lancement avec --chrome)."""
    if os.name == "nt":
        try:
            import winreg
            winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Google\Chrome\NativeMessagingHosts\com.anthropic.claude_code_browser_extension")
            return True
        except OSError:
            return False
    return (Path.home() / ".config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json").exists()


def cli_supports_chrome(claude: str) -> bool:
    from .claude_cli import NO_WINDOW
    try:
        out = subprocess.run([claude, "--help"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        return "--chrome" in (out.stdout + out.stderr)
    except Exception:  # noqa: BLE001
        return False


ERRORS = [
    (r"not connected|n'est pas connect", "L'extension ne répond pas : vérifiez que Chrome est ouvert, que l'extension Claude est "
     "activée et connectée au MÊME compte claude.ai que Claude Code. Redémarrez Chrome une fois si c'est la première fois."),
    (r"receiving end does not exist", "L'extension s'est mise en veille : désactivez-la puis réactivez-la dans chrome://extensions."),
    (r"no tab available", "Aucun onglet prêt : je réessaie avec un nouvel onglet."),
    (r"403|api key|setup-token", "Claude Code doit être connecté avec votre abonnement (/login), pas avec une clé d'API."),
]


def explain(text: str) -> str:
    for rx, msg in ERRORS:
        if re.search(rx, text or "", re.I):
            return msg
    return ""


def test_steps(claude: str, agent) -> list[dict]:
    """Test pas à pas de la liaison Madex <-> Chrome : montre exactement où ça coince."""
    steps = []
    lg = log.get("navigateur")

    def step(name, ok, detail):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})
        (lg.info if ok else lg.error)(f"test Chrome — {name} : {detail}")
        return ok
    if not step("Claude Code sait piloter Chrome", cli_supports_chrome(claude),
                "oui (--chrome)" if cli_supports_chrome(claude) else "non : lancez `claude update`"):
        return steps
    # indices locaux (n'empêchent pas le vrai test : seule la connexion réelle fait foi)
    exe = _chrome_exe()
    step("Google Chrome installé", exe, exe or "pas trouvé à l'emplacement habituel (le vrai test dira si ça marche)")
    prof = extension_installed()
    step("Extension Claude installée", prof, f"oui (profil {prof})" if prof else f"pas trouvée : installez-la depuis {STORE_URL}")
    step("Pont Chrome <-> Claude Code", native_host_ok(),
         "oui" if native_host_ok() else "pas encore : il se crée au premier test, puis redémarrez Chrome une fois")
    try:
        answer = agent.ask("TEST DE CONNEXION : appelle tabs_context_mcp (sans rien ouvrir), puis réponds UNIQUEMENT "
                           "« CONNECTÉ : <nombre> onglets » ou « ERREUR : <message d'erreur exact> ».",
                           config.MODEL_LIGHT)
    except Exception as exc:  # noqa: BLE001
        answer = f"ERREUR : {exc}"
    ok = "CONNECT" in answer.upper() and "ERREUR" not in answer.upper()
    if ok and not native_host_ok() and steps[-1]["step"].startswith("Pont"):
        steps[-1].update(ok=True, detail="oui (connexion établie)")
    step("Connexion réelle à Chrome", ok, answer.strip()[:200] + ("" if ok else " — " + (explain(answer) or
         "ouvrez Chrome, vérifiez l'extension (même compte claude.ai), puis réessayez")))
    return steps
