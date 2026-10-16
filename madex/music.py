"""Spotify (Premium) : lancer n'importe quel son, artiste, album ou playlist, et contrôler la lecture.

Connexion une seule fois depuis l'onglet CONNEXIONS (OAuth PKCE : aucun mot de passe ne passe par Madex).
Quand Madex parle, la musique baisse puis remonte toute seule.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import secrets
import threading
import time
import unicodedata

from . import config, secrets_store

# Scopes vérifiés sur developer.spotify.com/documentation/web-api/concepts/scopes (octobre 2026).
# NE PAS les modifier depuis Madex : un seul scope invalide et Spotify refuse toute la connexion (« invalid_scope »).
VALID_SCOPES = {"ugc-image-upload", "user-read-playback-state", "user-modify-playback-state",
                "user-read-currently-playing", "app-remote-control", "streaming", "playlist-read-private",
                "playlist-read-collaborative", "playlist-modify-private", "playlist-modify-public",
                "user-follow-modify", "user-follow-read", "user-read-playback-position", "user-top-read",
                "user-read-recently-played", "user-library-modify", "user-library-read", "user-read-email",
                "user-read-private", "user-personalized"}
WANTED = ("user-modify-playback-state user-read-playback-state user-read-currently-playing "
          "playlist-read-private playlist-read-collaborative user-library-read")
SCOPE_ISSUES = [x for x in WANTED.split() if x not in VALID_SCOPES]
SCOPES = " ".join(x for x in WANTED.split() if x in VALID_SCOPES)     # un scope invalide est retiré, pas envoyé
REDIRECT = f"http://127.0.0.1:{config.PORT}/callback/spotify"


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def _phon(s: str) -> str:
    """Clé « sonore » approximative (français) pour rapprocher ce que Whisper écrit du vrai nom."""
    t = _plain(s).replace(" ", "")
    for a, b in (("eau", "o"), ("au", "o"), ("ph", "f"), ("qu", "k"), ("ck", "k"), ("ch", "§"), ("h", ""),
                 ("§", "ch"), ("x", "ks"), ("z", "s"), ("ai", "e"), ("ei", "e"), ("y", "i"), ("w", "ou")):
        t = t.replace(a, b)
    t = re.sub(r"c(?=[eiy])", "s", t).replace("c", "k")
    t = re.sub(r"(.)\1+", r"\1", t)
    return t


def _media_key(vk: int):
    """Touche multimédia de Windows (lecture/pause 0xB3, suivant 0xB0, précédent 0xB1) : marche sans l'API."""
    if os.name != "nt":
        return
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def _fallback_open(uri: str) -> bool:
    """Plan B : ouvre le titre / l'artiste / la playlist dans l'application Spotify puis lance la lecture."""
    if os.name != "nt" or not uri.startswith("spotify:"):
        return False
    try:
        os.startfile(uri)  # noqa: S606
        time.sleep(2.5)                                         # le temps que Spotify affiche la page
        _media_key(0xB3)
        return True
    except OSError:
        return False


def _explain(exc) -> str:
    st, msg = getattr(exc, "http_status", 0), str(getattr(exc, "msg", exc)).lower()
    if st == 401:
        return "Spotify m'a déconnecté, monsieur. Reconnectez-le dans la fenêtre Connexions."
    if st == 403 and "scope" in msg:
        return ("La connexion Spotify n'a pas les autorisations nécessaires. Dans Réglages › Connexions, "
                "cliquez sur RÉINITIALISER puis CONNECTER.")
    if st == 403 and "premium" in msg:
        return "Spotify exige un compte Premium pour que je contrôle la lecture."
    if st == 403:
        return ("Spotify refuse cette commande. Vérifiez que votre compte est bien ajouté dans "
                "User Management de votre app sur developer.spotify.com.")
    if st == 404:
        return "Spotify ne trouve aucun appareil actif. Lancez un titre une fois dans l'application, puis redemandez-moi."
    if st == 429:
        return "Spotify me demande de ralentir, réessayez dans un instant."
    return f"Spotify a refusé ({st}). Je réessaierai si vous me le redemandez."


class Music:
    def __init__(self):
        self.cache = config.DATA / "spotify_token.json"
        self._auth = None
        self._state = ""
        self._sp = None
        self.ducked: float | None = None
        self.log = __import__("madex.log", fromlist=["x"]).get("spotify")
        self.last_error = ""
        self.used_fallback = False
        self.lock = threading.Lock()

    # ------------------------------------------------------------ connexion
    @property
    def client_id(self) -> str:
        return secrets_store.get("SPOTIFY_CLIENT_ID")

    def _manager(self):
        if self._auth is None:
            from spotipy.cache_handler import CacheFileHandler
            from spotipy.oauth2 import SpotifyPKCE
            self._auth = SpotifyPKCE(client_id=self.client_id, redirect_uri=REDIRECT, scope=SCOPES,
                                     cache_handler=CacheFileHandler(cache_path=str(self.cache)), open_browser=False)
        return self._auth

    def connected(self) -> bool:
        if not self.client_id or not self.cache.exists():
            return False
        try:
            return bool(self._manager().validate_token(self._manager().cache_handler.get_cached_token()))
        except Exception:  # noqa: BLE001
            return False

    def auth_url(self) -> str:
        if not self.client_id:
            raise ValueError("Ajoute d'abord le Client ID de ton app Spotify.")
        self._auth = None
        self._state = secrets.token_urlsafe(16)
        return self._manager().get_authorize_url(state=self._state)

    def callback(self, code: str, state: str):
        if not self._state or state != self._state:
            raise ValueError("Réponse Spotify inattendue (state invalide).")
        self._manager().get_access_token(code=code, check_cache=False)
        self._state, self._sp = "", None

    @property
    def sp(self):
        if self._sp is None:
            if not self.connected():
                raise ValueError("Spotify n'est pas encore connecté, monsieur : tout se passe dans l'onglet Connexions.")
            import spotipy
            self._sp = spotipy.Spotify(auth_manager=self._manager(), requests_timeout=10, retries=1)
        return self._sp

    # ------------------------------------------------------------ appareil
    def _device(self) -> str:
        """Renvoie l'appareil où jouer. Spotify fermé : on l'ouvre et on attend qu'il apparaisse."""
        devices = self.sp.devices().get("devices", [])
        if not devices:
            if os.name == "nt":
                try:
                    os.startfile("spotify:")  # noqa: S606
                except OSError:
                    raise ValueError("Je ne trouve pas l'application Spotify sur ce PC, monsieur. "
                                     "Installez-la depuis spotify.com ou le Microsoft Store.") from None
            for _ in range(20):
                time.sleep(1)
                devices = self.sp.devices().get("devices", [])
                if devices:
                    time.sleep(1.5)                            # laisse Spotify finir de démarrer
                    break
        if not devices:
            raise ValueError("Spotify ne répond pas, monsieur. Ouvrez l'application et connectez-vous, puis redemandez-moi.")
        pc = os.environ.get("COMPUTERNAME", "").lower()
        active = next((d for d in devices if d.get("is_active")), None)
        mine = next((d for d in devices if d.get("type") == "Computer" and pc and d.get("name", "").lower() == pc), None)
        computer = next((d for d in devices if d.get("type") == "Computer"), None)
        return (active or mine or computer or devices[0])["id"]

    def _start(self, **kw):
        """Lance la lecture, en réveillant l'appareil si Spotify dit qu'aucun n'est actif.
        Si Spotify refuse la commande de lecture (API), plan B : on ouvre le titre dans l'application
        et on appuie sur la touche « lecture » de Windows."""
        try:
            self._start_api(**kw)
        except ValueError as exc:
            uri = kw.get("context_uri") or (kw.get("uris") or [None])[0]
            if uri and _fallback_open(uri):
                self.log.warning(f"lecture via l'API refusée ({exc}) : plan B, ouverture dans l'application")
                self.used_fallback = True
                return
            raise

    def _start_api(self, **kw):
        from spotipy.exceptions import SpotifyException
        dev = self._device()
        for attempt in range(3):
            try:
                self.sp.start_playback(device_id=dev, **kw)
                return
            except SpotifyException as exc:
                if exc.http_status in (404, 502, 503) and attempt < 2:      # appareil endormi
                    try:
                        self.sp.transfer_playback(dev, force_play=False)
                    except SpotifyException:
                        pass
                    time.sleep(1.0)
                    dev = self._device()
                    continue
                self.log.error(f"Spotify refuse la lecture : {exc.http_status} {getattr(exc, 'msg', exc)}")
                self.last_error = f"{exc.http_status} : {getattr(exc, 'msg', exc)}"
                raise ValueError(_explain(exc)) from None

    # ------------------------------------------------------------ noms d'artistes que Madou écoute
    def library_names(self) -> list[str]:
        """Artistes (titres likés) et playlists de Madou, mis en cache 24 h : aide Whisper et corrige les noms."""
        cache = config.DATA / "spotify_noms.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                pass
        names: dict[str, int] = {}
        try:
            for offset in (0, 50, 100, 150):
                page = self.sp.current_user_saved_tracks(limit=50, offset=offset)
                for it in page.get("items", []):
                    for a in (it.get("track") or {}).get("artists", []):
                        names[a["name"]] = names.get(a["name"], 0) + 1
                if not page.get("next"):
                    break
            for p in self.sp.current_user_playlists(limit=50).get("items", []):
                if p and p.get("name"):
                    names.setdefault(p["name"], 1)
        except Exception as exc:  # noqa: BLE001
            print(f"[spotify] noms : {exc}", flush=True)
            return []
        out = [n for n, _ in sorted(names.items(), key=lambda kv: -kv[1])][:150]
        config.DATA.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out

    def correct(self, query: str) -> str:
        """« nino » -> « Ninho », « gazeau » -> « Gazo » : d'après ce que Madou écoute vraiment."""
        names = self.library_names() if self.connected() else []
        if not names or not query:
            return query
        keyed = {_phon(n): n for n in names}
        q = _phon(query)
        if q in keyed:
            return keyed[q]
        hit = difflib.get_close_matches(q, list(keyed), n=1, cutoff=0.8)
        if hit:
            return keyed[hit[0]]
        words = query.split()                             # « bohemian rhapsody de queen » : on corrige l'artiste seul
        for i in range(len(words)):
            for j in range(len(words), i, -1):
                part = " ".join(words[i:j])
                k = _phon(part)
                if len(k) >= 3 and (k in keyed or difflib.get_close_matches(k, list(keyed), n=1, cutoff=0.86)):
                    real = keyed.get(k) or keyed[difflib.get_close_matches(k, list(keyed), n=1, cutoff=0.86)[0]]
                    return " ".join(words[:i] + [real] + words[j:])
        return query

    # ------------------------------------------------------------ lecture
    def search(self, query: str, types: str, limit: int = 5) -> dict:
        """Recherche Spotify (sans « market=from_token », qui exige un scope que Madex ne demande pas)."""
        try:
            return self.sp.search(q=query, type=types, limit=min(limit, 10))
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "http_status", None):
                self.last_error = f"recherche « {query} » : {getattr(exc, 'msg', exc)}"
                self.log.error(f"recherche refusée ({exc.http_status}) : {getattr(exc, 'msg', exc)}")
                raise ValueError(_explain(exc)) from None
            raise

    def play(self, query: str, kind: str = "auto") -> str:
        """kind : auto | track | artist | album | playlist | myplaylist | liked | latest | genre."""
        if kind == "liked":
            me = self.sp.me()["id"]
            self._start(context_uri=f"spotify:user:{me}:collection")
            return "vos titres likés"
        query = self.correct(query)
        if kind == "myplaylist":
            pl = self._my_playlist(query)
            if pl:
                self._start(context_uri=pl["uri"])
                return f"votre playlist {pl['name']}"
            kind = "playlist"
        if kind == "latest":
            res = self.search(query, "artist", 1)
            art = ((res.get("artists") or {}).get("items") or [None])[0]
            if not art:
                raise ValueError(f"Je ne trouve pas l'artiste « {query} » sur Spotify.")
            albums = self.sp.artist_albums(art["id"], include_groups="album,single", limit=10).get("items", [])
            if not albums:
                raise ValueError(f"Aucune sortie trouvée pour {art['name']}.")
            last = max(albums, key=lambda a: a.get("release_date", ""))
            self._start(context_uri=last["uri"])
            return f"la dernière sortie de {art['name']}, {last['name']}"
        if kind == "genre":
            res = self.search(query, "playlist", 5)
            items = [p for p in (res.get("playlists") or {}).get("items", []) if p]
            if not items:
                raise ValueError(f"Je ne trouve pas d'ambiance « {query} ».")
            self._start(context_uri=items[0]["uri"])
            return f"une playlist {query}"
        types = {"auto": "track,artist,album,playlist", "track": "track", "artist": "artist",
                 "album": "album", "playlist": "playlist"}[kind]
        res = self.search(query, types, 5)
        pick = self._best(query, res, kind)
        if not pick:
            raise ValueError(f"Je ne trouve rien pour « {query} » sur Spotify.")
        t, item = pick
        if t == "track":
            self._start(uris=[item["uri"]])
            return f"{item['name']} de {item['artists'][0]['name']}"
        self._start(context_uri=item["uri"])
        if t == "artist":
            return item["name"]
        if t == "album":
            return f"l'album {item['name']} de {item['artists'][0]['name']}"
        return f"la playlist {item['name']}"

    @staticmethod
    def _best(query: str, res: dict, kind: str):
        q = _plain(query)
        order = {"artist": ["artist"], "album": ["album"], "playlist": ["playlist"], "track": ["track"],
                 "auto": ["artist", "track", "album", "playlist"]}[kind]
        best, score = None, -1.0
        for rank, t in enumerate(order):
            for item in [i for i in (res.get(t + "s") or {}).get("items", []) if i][:5]:
                name = _plain(item.get("name", ""))
                s = difflib.SequenceMatcher(None, q, name).ratio()
                if t == "track":                              # « bohemian rhapsody queen »
                    s = max(s, difflib.SequenceMatcher(None, q, _plain(item["name"] + " " + item["artists"][0]["name"])).ratio())
                s -= rank * 0.05                               # en cas d'égalité : artiste > titre > album > playlist
                if s > score:
                    best, score = (t, item), s
        return best

    def _my_playlist(self, name: str):
        items, offset = [], 0
        while True:
            page = self.sp.current_user_playlists(limit=50, offset=offset)
            items += [p for p in page.get("items", []) if p]
            if not page.get("next"):
                break
            offset += 50
        names = {_plain(p["name"]): p for p in items}
        hit = difflib.get_close_matches(_plain(name), list(names), n=1, cutoff=0.5)
        return names[hit[0]] if hit else None

    def focus(self) -> str:
        name = config.FOCUS_PLAYLIST
        try:
            return self.play(name, "myplaylist")
        except ValueError:
            return self.play(name, "playlist")

    # ------------------------------------------------------------ contrôle
    # ------------------------------------------------------------ diagnostic et remise à zéro
    def reset(self):
        """Efface l'ancienne connexion (jeton) : il suffit ensuite de recliquer sur CONNECTER."""
        self.cache.unlink(missing_ok=True)
        (config.DATA / "spotify_noms.json").unlink(missing_ok=True)
        self._auth, self._sp, self._state = None, None, ""
        self.log.info("connexion Spotify réinitialisée")

    def test_steps(self) -> list[dict]:
        """Test pas à pas : montre exactement où ça casse."""
        steps = []

        def step(name, fn):
            try:
                detail = fn()
                steps.append({"step": name, "ok": True, "detail": detail or "OK"})
                return True
            except Exception as exc:  # noqa: BLE001
                st = getattr(exc, "http_status", None)
                msg = _explain(exc) if st else str(exc)
                steps.append({"step": name, "ok": False, "detail": msg + (f" (code {st} : {getattr(exc, 'msg', '')})" if st else "")})
                self.log.error(f"test Spotify — {name} : {msg}")
                return False
        if not step("Client ID enregistré", lambda: "oui" if self.client_id else (_ for _ in ()).throw(ValueError("non : collez-le dans Réglages › Connexions"))):
            return steps
        if not step("Connexion valide", lambda: "oui" if self.connected() else (_ for _ in ()).throw(ValueError("non : cliquez sur CONNECTER (ou RÉINITIALISER puis CONNECTER)"))):
            return steps
        step("Compte", lambda: self.sp.me().get("display_name") or self.sp.me().get("id"))
        step("Appareils", lambda: ", ".join(d["name"] + (" (actif)" if d.get("is_active") else "") for d in self.sp.devices().get("devices", []))
             or "aucun : ouvrez l'application Spotify")
        step("Autorisations accordées", self._scope_check)
        step("Recherche", lambda: (self.search("Ninho", "track,artist,album,playlist", 5).get("artists") or {}).get("items", [{}])[0].get("name", "rien"))
        step("Lecture en cours", lambda: ((self.sp.current_playback() or {}).get("item") or {}).get("name") or "rien ne joue")
        return steps

    def _scope_check(self) -> str:
        tok = self._manager().cache_handler.get_cached_token() or {}
        have = set((tok.get("scope") or "").split())
        missing = [x for x in SCOPES.split() if x not in have]
        if missing:
            raise ValueError("il manque " + ", ".join(missing) + " : RÉINITIALISER puis CONNECTER")
        return f"{len(have)} autorisation(s), rien ne manque"

    def pause(self):
        try:
            self.sp.pause_playback()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"pause via l'API impossible ({exc}) : touche multimédia")
            _media_key(0xB3)

    def resume(self):
        self._start()

    def next(self):
        try:
            self.sp.next_track()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"suivant via l'API impossible ({exc}) : touche multimédia")
            _media_key(0xB0)

    def previous(self):
        try:
            self.sp.previous_track()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"précédent via l'API impossible ({exc}) : touche multimédia")
            _media_key(0xB1)

    def volume(self, value: int | None = None, delta: int = 0) -> int:
        pb = self.sp.current_playback() or {}
        cur = (pb.get("device") or {}).get("volume_percent", 50)
        new = max(0, min(100, value if value is not None else cur + delta))
        self.sp.volume(new)
        return new

    def now_playing(self) -> str:
        cur = self.sp.current_playback() or {}
        item = cur.get("item")
        if not item:
            return "Rien ne joue en ce moment, monsieur."
        return f"C'est {item['name']}, de {', '.join(a['name'] for a in item['artists'])}."

    def is_playing(self) -> bool:
        try:
            return bool((self.sp.current_playback() or {}).get("is_playing"))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ baisser quand Madex parle
    def duck(self):
        """Baisse Spotify instantanément via le mixeur Windows (aucun appel réseau)."""
        with self.lock:
            vol = _spotify_session_volume()
            if vol is not None and vol.GetMasterVolume() > 0.3:
                self.ducked = vol.GetMasterVolume()
                vol.SetMasterVolume(0.25, None)

    def unduck(self):
        with self.lock:
            if self.ducked is not None:
                vol = _spotify_session_volume()
                if vol is not None:
                    vol.SetMasterVolume(self.ducked, None)
                self.ducked = None


def _spotify_session_volume():
    if os.name != "nt":
        return None
    try:
        import comtypes
        try:
            comtypes.CoInitialize()                        # nécessaire dans chaque thread qui touche au mixeur
        except OSError:
            pass
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name().lower() == "spotify.exe":
                return s.SimpleAudioVolume
    except Exception:  # noqa: BLE001
        return None
    return None
