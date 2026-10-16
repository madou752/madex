"""L'aiguilleur : décide qui traite chaque phrase, du moins coûteux au plus coûteux.

1. Réponse à une demande d'accord en attente (« oui », « pour la session », « non »)       -> immédiat
2. Commandes locales : fenêtre, musique, applis, sites, projets, volume                    -> 0 token
3. Mails (résumé fait par Haiku, sans outils)                                               -> Haiku
4. Petite question                                                                          -> Haiku
5. Code, projets, revue, conception                                                         -> Sonnet
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import config
from .apps import KNOWN, SITES, norm
from .projects import list_projects


@dataclass
class Route:
    kind: str
    arg: str = ""
    model: str = ""
    extra: dict = field(default_factory=dict)


def plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"^((hey|he|eh|ok|dis) )?madex[ ,.!?]*", "", t.strip())
    return re.sub(r"\s+", " ", re.sub(r"[^\w' -]", " ", t)).strip()


R = lambda p: re.compile(p)  # noqa: E731

YES_SESSION = R(r"\b(pour (toute )?(la|cette) session|toute la session)\b")
YES_ALWAYS = R(r"\b(toujours|tout le temps|a chaque fois|plus besoin de demander)\b")
YES = R(r"^(oui|ouais|vas[ -]?y|ok|d'accord|autorise|j'autorise|je confirme|confirme|valide|go|c'est bon|fais[ -]le|accorde)\b")
NO = R(r"^(non|refuse|je refuse|surtout pas|annule|pas question|negatif)\b")

QUIT = R(r"^(eteins[ -]toi|arrete[ -]toi|quitte|ferme[ -]toi|desactive[ -]toi|bonne nuit|extinction|eteins[ -]toi completement|"
         r"arrete[ -]toi completement|quitte completement|extinction complete|tu peux t'eteindre|tu peux te fermer)\b")
RESTART = R(r"^(redemarre[ -]toi|redemarre|relance[ -]toi|reboot)\b")
SLEEP = R(r"^(va dormir|dors|cache[ -]toi|reduis[ -]toi|ferme ta fenetre|cache ta fenetre|mets[ -]toi en veille|repos|au revoir)\b")
MENTOR_ON = R(r"\b(mode mentor|active le mentor|passe en mentor|sois mon mentor|mentor on)\b")
MENTOR_OFF = R(r"\b(fin du mode mentor|quitte le mode mentor|desactive le mentor|arrete le mode mentor|stop mentor|mentor off)\b")
REVIEW = R(r"\b(regarde|revois|review|relis|analyse|check|fais la revue de|revue de)( moi)? (la |ma |cette )?(pr|pull request|merge request)"
           r"(?: (?:numero |n |#)?(\d+))?\b")
NOTIFS = R(r"\b(notif|notifs|notification|notifications)\b")
NEWS = R(r"^(quoi de neuf|quoi de beau|resume de la journee|fais[ -]moi le point|le point|des nouvelles)\b")
CORRECTION = R(r"^(?:non )?(?:j'ai pas dit ca |j ai pas dit ca )?(?:j'ai dit|j ai dit|je voulais dire|je disais) (?P<word>[\w' -]{2,40})$")
WINDOW = R(r"^(?:montre|affiche|ouvre|va dans|voir|donne)(?:[ -]moi)? (?:les |la |le |l'|mes |mon |ma |l onglet |le panneau |la fenetre |les fenetres )?"
           r"(conso|consommation|tokens?|musique|lecteur|systeme|stats?|pc|diagnostic|diag|reglages?|parametres|journal|logs?)\b")
CONSO = R(r"\b(ma conso|ma consommation|combien de tokens|consommation de tokens|mes tokens|mon quota|ma limite|il me reste combien)\b")
CLOSE_WINDOWS = R(r"^(?:ferme|cache|masque|range) (?:toutes )?(?:les |le |ce |cette )?(?:panneau|panneaux|onglet|onglets|fenetres?|tout)\b")
HUSH = R(r"^(tais[ -]toi|chut|silence|c'est bon( merci)?|merci c'est tout|laisse tomber|ok merci|non rien|rien)$")
MIC_OFF = R(r"^(coupe|desactive|eteins) (le |ton )?micro\b")
# Claude dans Chrome : demandes explicites de navigation (le reste reste au cerveau, qui peut aussi passer la main)
BROWSER = R(r"(\b(dans|sur|avec|via) (google )?chrome\b|\b(dans|avec|sur) (mon|le) navigateur\b|^(va|vas|aller) sur (?!la musique)\S|"
            r"\bsur (le site|la page|mon site|ce site)\b|\blocal ?host\b|\b(remplis|complete) (le|ce|un|mon) formulaire\b)")
WINDOWS = {"conso": "us", "consommation": "us", "token": "us", "tokens": "us", "musique": "mu", "lecteur": "mu",
           "systeme": "sy", "stat": "sy", "stats": "sy", "pc": "sy", "diagnostic": "dg", "diag": "dg",
           "reglage": "rg", "reglages": "rg", "parametres": "rg", "journal": "lg", "log": "lg", "logs": "lg"}
PANEL = R(r"^(?:montre|affiche|ouvre|va dans|voir)(?:[ -]moi)? (?:les |la |le |l'|mes |mon |ma |l onglet |le panneau )?(sessions?|taches?|regles?|connexions?|historique|validations?|a valider|accords?|demandes?)\b")
PANEL_CLOSE = R(r"^(?:ferme|cache|masque) (?:le |les |ce )?(?:panneau|panneaux|onglet|onglets)\b")
PANES = {"session": "ss", "tache": "ss", "regle": "rg", "connexion": "cx", "historique": "hx",
         "validation": "ap", "a valider": "ap", "accord": "ap", "demande": "ap"}
SHOW = R(r"^(affiche[ -]toi|montre[ -]toi|ouvre[ -]toi|ouvre ton interface|ouvre ta fenetre|reveille[ -]toi|apparais)\b")
RESET = R(r"\b(nouvelle conversation|on repart de zero|oublie tout ca)\b")

PAUSE = R(r"^((mets )?(en )?pause|stop( la musique| le son)?|coupe la musique|arrete la musique)$")
RESUME = R(r"^((reprends|relance|remets)( la musique| le son| la lecture)?|play|lecture)$")
NEXT = R(r"^(suivant|suivante|(chanson|morceau|musique|son|titre) suivant(e)?|next|passe|skip|zappe)( la)?$")
PREV = R(r"^(precedent(e)?|(chanson|morceau|musique|son|titre) precedent(e)?|reviens en arriere)$")
NOW = R(r"(c'est quoi|quel est|qu'est[ -]ce que c'est|c'est qui)( le titre de)? (cette musique|ce son|ce morceau|cette chanson|le titre)")
VOL_UP = R(r"^(monte|augmente)( un peu)? (le son|le volume|la musique)|^plus fort")
VOL_DOWN = R(r"^(baisse|diminue)( un peu)? (le son|le volume|la musique)|^moins fort")
VOL_SET = R(r"^(mets )?(le )?(son|volume) (a )?(\d{1,3})( ?%| pour ?cent)?$")
MUTE = R(r"^(coupe le son|mute|silence)$")
LIKED = R(r"(mes titres likes|mes coups de coeur|mes favoris|mes titres aimes|ma musique likee)")
PLAY = R(r"^(?:(?:tu peux|peux[ -]tu|est-ce que tu peux|je veux|j'ai envie d'|on|vas[ -]y)[ ,]+)?"
         r"(?P<verb>mets|mettre|joue|jouer|ecoute|ecouter|fais(?:[ -](?:moi|nous))? ecouter|lance|lancer|passe|"
         r"passer|balance|envoie)(?:[ -](?:moi|nous|nous un peu|moi un peu|un peu))? (?P<target>.+)$")
SMART_MUSIC = R(r"\b(dernier|derniere|nouveau|nouvelle|recent|recente|sorti|sortie|style|genre|ambiance|chill|rap|rnb|"
                r"jazz|lofi|lo-fi|classique|pop|rock|electro|afro|drill|pour (dormir|bosser|travailler|courir|danser|"
                r"me detendre|la route)|similaire|comme|ressemble|feat|featuring|avec)\b")

MAILS = R(r"\b(mail|mails|e-mail|e-mails|email|emails|courriel|courriels|boite mail|boite de reception)\b")
MAIL_VERB = R(r"\b(resume|lis|quoi de neuf|j'ai|nouveau|nouveaux|check|verifie|regarde|des mails|mes mails)\b")

LAUNCH = R(r"^(?P<verb>ouvre|lance|demarre|run|execute|allume)(?:[ -]moi)? (?:le |mon |l')?(?P<projet>projet |appli |application |app )?"
           r"(?P<name>.+?)(?P<editor> (?:dans|sur|avec) (?:vs code|vscode|l'editeur|code|cursor))?$")
OPEN_PROJECT = R(r"^(ouvre|lance)( moi)? (le |mon )?projet (.+?)( dans (vs code|l'editeur|code|cursor))?$")
OPEN_IN_EDITOR = R(r"^ouvre( moi)? (.+) dans (vs code|l'editeur|code|cursor)$")
OPEN = R(r"^(ouvre|lance|demarre|va sur|affiche)( moi)? (l'|le |la |les |le site )?(.+)$")
CLOSE = R(r"^(ferme|quitte|coupe)( moi)? (l'|le |la |les )?(.+)$")

CODE_WORDS = R(
    r"\b(code|coder|projet|bug|feature|fonction|fichier|commit|branche|merge|pull request|pr|test|tests|spec|"
    r"refacto|refactor|erreur|exception|api|endpoint|composant|classe|methode|variable|build|deploy|"
    r"react|vue|typescript|javascript|java|spring|kotlin|flutter|php|python|scss|css|html|sql|base de donnees|"
    r"le dev|une session|la tache|session|tache|taches|regle|regles|push|revue|review|architecture)\b")
NOT_MUSIC = R(r"\b(alarme|minuteur|rappel|reveil|timer|chrono|heure|rendez-vous|note|mail|message|ecran|fenetre|en veille)\b")
MUSIC_MARK = R(r"^((la |ma )?playlist|l'album|(du|de la|des|de l') |de la musique|du son|(la )?musique|une chanson|un son)")


def _music_target(target: str) -> Route | None:
    t = re.sub(r"^(plutot |plus tot |a la place |un peu |un son |une chanson |un morceau |le son |la chanson |le morceau |le titre )(?=\S)", "", target.strip())
    t = re.sub(r"^(plutot |un peu )", "", t)
    t = re.sub(r" (sur|dans|avec) spotify$", "", t)
    if LIKED.search(t):
        return Route("music", "liked")
    if re.match(r"^(de la musique|du son|(la |de la )?musique|un son)( de concentration| pour (bosser|travailler|me concentrer))?$", t):
        return Route("music", "focus" if "concentr" in t or "bosser" in t or "travailler" in t else "resume")
    if m := re.match(r"^ma playlist (.+)$", t):
        return Route("music", "play", extra={"kind": "myplaylist", "q": m.group(1)})
    if m := re.match(r"^(la )?playlist (.+)$", t):
        return Route("music", "play", extra={"kind": "myplaylist", "q": m.group(2)})
    if m := re.match(r"^l'album (.+)$", t):
        return Route("music", "play", extra={"kind": "album", "q": m.group(1)})
    if SMART_MUSIC.search(t) or len(t.split()) > 6:          # « la dernière de SDM », « du rap pour bosser »
        return Route("music", "smart", extra={"q": t})
    if m := re.match(r"^(du|de la|des|de l') ?(.+)$", t):
        return Route("music", "play", extra={"kind": "artist", "q": m.group(2)})
    if m := re.match(r"^(.+) (de|par) (.+)$", t):
        return Route("music", "play", extra={"kind": "track", "q": f"{m.group(1)} {m.group(3)}"})
    return Route("music", "play", extra={"kind": "auto", "q": t})


FILLER_START = re.compile(r"^((attends|attend|en fait|euh|bon|alors|plutot|non mais|non|ah|oh)\b[ ,]*)+")


def route(text: str, has_pending_approval: bool, music_on: bool = False) -> Route:
    t = plain(text)
    if (m := CORRECTION.match(t)) and not has_pending_approval:
        return Route("correction", m.group("word").strip())
    if not has_pending_approval:
        t = FILLER_START.sub("", t).strip() or t          # « attends, mets plutôt du Jul » -> « mets plutôt du Jul »
    words = t.split()
    if has_pending_approval:
        if NO.search(t):
            return Route("approval", "refuser")
        if YES_SESSION.search(t):
            return Route("approval", "session")
        if YES_ALWAYS.search(t) and (YES.search(t) or len(words) <= 4):
            return Route("approval", "toujours")
        if YES.search(t):
            return Route("approval", "une_fois")
    if not t:
        return Route("show")
    if MENTOR_OFF.search(t):
        return Route("mentor", "off")
    if MENTOR_ON.search(t):
        return Route("mentor", "on")
    if RESTART.search(t):
        return Route("restart")
    if QUIT.search(t):
        return Route("quit")
    if SLEEP.search(t):
        return Route("sleep")
    if BROWSER.search(t) and not re.match(r"^(ouvre|lance|ferme|demarre) (google )?chrome$", t):
        return Route("browser", text)
    if m := REVIEW.search(t):
        return Route("review", m.group(5) or "")
    if NOTIFS.search(t) and len(words) <= 10:
        return Route("notifs")
    if NEWS.search(t):
        return Route("news")
    if HUSH.match(t):
        return Route("hush")
    if MIC_OFF.match(t):
        return Route("mic_off")
    if CONSO.search(t):
        return Route("conso")
    if m := WINDOW.search(t):
        w = WINDOWS.get(m.group(1), "dg")
        return Route("conso") if w == "us" else Route("panel", w)
    if CLOSE_WINDOWS.search(t):
        return Route("panel", "")
    if m := PANEL.search(t):
        word = m.group(1).rstrip("s") if m.group(1) != "a valider" else "a valider"
        return Route("panel", PANES.get(word, "ap"))
    if SHOW.search(t):
        return Route("show")
    if RESET.search(t):
        return Route("reset")

    # musique
    if PAUSE.match(t):
        return Route("music", "pause")
    if RESUME.match(t):
        return Route("music", "resume")
    if NEXT.match(t):
        return Route("music", "next")
    if PREV.match(t):
        return Route("music", "previous")
    if NOW.search(t):
        return Route("music", "now")
    if m := VOL_SET.match(t):
        return Route("volume", extra={"value": int(m.group(5))})
    if VOL_UP.search(t):
        return Route("volume", extra={"delta": 15})
    if VOL_DOWN.search(t):
        return Route("volume", extra={"delta": -15})
    if MUTE.match(t):
        return Route("volume", extra={"mute": True})

    # mails
    if MAILS.search(t) and (MAIL_VERB.search(t) or len(words) <= 4):
        return Route("mails")

    # fenêtres, projets, sites, applis
    project_names = {p.name.lower() for p in list_projects()}
    if m := LAUNCH.match(t):                       # « lance todo app » : projet ? appli ? ouvrir ou démarrer ?
        from .resolver import score
        name = m.group("name")
        said_project = bool(m.group("projet") and m.group("projet").startswith("projet"))
        best = max((score(name, p) for p in project_names), default=0.0)
        if said_project or best >= 0.72 or m.group("editor"):
            return Route("launch", name, extra={"verb": m.group("verb"), "projet": said_project,
                                                "editor": bool(m.group("editor"))})
    if m := OPEN_PROJECT.match(t):
        return Route("open_project", m.group(4))
    if m := OPEN_IN_EDITOR.match(t):
        return Route("open_project", m.group(2))
    if (m := CLOSE.match(t)) and len(words) <= 5 and not CODE_WORDS.search(m.group(4)):
        return Route("close_app", m.group(4))
    if (m := PLAY.match(t)) and not CODE_WORDS.search(m.group("target")) and not NOT_MUSIC.search(m.group("target")):
        target = m.group("target")
        verb = m.group("verb")
        known_app = norm(target) in KNOWN or norm(target) in SITES
        if known_app:
            pass                                              # « lance VS Code » : c'est une appli
        elif verb.startswith("lance") and not MUSIC_MARK.match(target) and not LIKED.search(target):
            if music_on:                                      # appli installée ? sinon musique (décidé par le cœur)
                return Route("open_or_play", target, extra={"music": _music_target(target)})
        elif music_on or MUSIC_MARK.match(target):
            return _music_target(target)
    if (m := OPEN.match(t)) and (norm(m.group(4)) in KNOWN or norm(m.group(4)) in SITES):
        return Route("open", m.group(4))
    if (m := OPEN.match(t)) and len(words) <= 6 and not CODE_WORDS.search(m.group(4)) and m.group(4) not in project_names:
        return Route("open", m.group(4))

    is_code = bool(CODE_WORDS.search(t)) or any(n in t for n in project_names) or len(words) > 40
    return Route("brain", model=config.MODEL_MAIN if is_code else config.MODEL_LIGHT)
