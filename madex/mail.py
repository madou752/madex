"""Gmail en LECTURE SEULE : résumés à la demande et alerte quand un mail important arrive.

- Autorisation Google limitée à `gmail.readonly` : Madex ne peut ni envoyer, ni supprimer, ni modifier un mail.
- Seuls l'expéditeur, l'objet et l'aperçu (quelques lignes) sont lus ; jamais les pièces jointes.
- Promotions, réseaux sociaux, notifications et forums sont ignorés.
- Le résumé est fait par Haiku SANS aucun outil : un mail piégé ne peut rien déclencher.
"""
from __future__ import annotations

import email.utils
import html
import json
import threading
import time

from . import config

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
QUERY = "in:inbox category:primary -category:promotions -category:social -category:updates -category:forums"


class Mail:
    def __init__(self, announce, emit):
        self.announce, self.emit = announce, emit
        self.client_file = config.DATA / "google_client.json"
        self.token_file = config.DATA / "google_token.json"
        self.seen_file = config.DATA / "mails_vus.json"
        self._svc = None
        self.connecting = False
        self.seen: set[str] = set(json.loads(self.seen_file.read_text())) if self.seen_file.exists() else set()
        self.paused = threading.Event()               # mode focus : pas d'alertes vocales
        threading.Thread(target=self._watch, daemon=True).start()

    # ------------------------------------------------------------ connexion
    def has_client(self) -> bool:
        return self.client_file.exists()

    def connected(self) -> bool:
        return self.token_file.exists()

    def save_client(self, content: str):
        data = json.loads(content)
        if "installed" not in data:
            raise ValueError("Ce fichier n'est pas un client OAuth « Application de bureau ». Voir le guide.")
        config.DATA.mkdir(parents=True, exist_ok=True)
        self.client_file.write_text(json.dumps(data), encoding="utf-8")

    def connect(self):
        """Ouvre la page Google dans ton navigateur ; tu acceptes ; c'est fini."""
        if not self.has_client():
            raise ValueError("Ajoute d'abord le fichier client Google (onglet CONNEXIONS).")
        if self.connecting:
            return

        def run():
            self.connecting = True
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_file), SCOPES)
                creds = flow.run_local_server(port=0, open_browser=True,
                                              success_message="MADEX est connecté à Gmail (lecture seule). Vous pouvez fermer cet onglet.")
                self.token_file.write_text(creds.to_json(), encoding="utf-8")
                self._svc = None
                self._mark_current_as_seen()
                self.emit("connections", changed="gmail")
                self.announce("Gmail est connecté, monsieur. En lecture seule, bien entendu.")
            except Exception as exc:  # noqa: BLE001
                self.emit("toast", text=f"Connexion Gmail échouée : {exc}")
            finally:
                self.connecting = False
        threading.Thread(target=run, daemon=True).start()

    @property
    def svc(self):
        if self._svc is None:
            if not self.connected():
                raise ValueError("Gmail n'est pas encore connecté, monsieur : tout se passe dans l'onglet Connexions.")
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from google.auth.exceptions import RefreshError
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            if not creds.valid and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError as exc:                 # appli Google en mode Test : accès expiré après 7 jours
                    self.token_file.unlink(missing_ok=True)
                    self.emit("connections", changed="gmail")
                    raise ValueError("L'accès à Gmail a expiré, monsieur. Reconnectez-le dans l'onglet Connexions ; "
                                     "pour que ça n'arrive plus, publiez l'application dans Google Cloud.") from exc
                self.token_file.write_text(creds.to_json(), encoding="utf-8")
            self._svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._svc

    # ------------------------------------------------------------ lecture
    def _list(self, query: str, n: int) -> list[dict]:
        ids = self.svc.users().messages().list(userId="me", q=query, maxResults=n).execute().get("messages", [])
        out = []
        for m in ids:
            msg = self.svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                  metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
            name, addr = email.utils.parseaddr(h.get("from", ""))
            out.append({"id": m["id"], "from": name or addr, "subject": h.get("subject", "(sans objet)"),
                        "snippet": html.unescape(msg.get("snippet", ""))[:300], "date": h.get("date", ""),
                        "important": "IMPORTANT" in msg.get("labelIds", []),
                        "unread": "UNREAD" in msg.get("labelIds", [])})
        return out

    def recent(self, unread_only: bool = True, days: int = 3, n: int = 15) -> list[dict]:
        q = QUERY + f" newer_than:{days}d" + (" is:unread" if unread_only else "")
        return self._list(q, n)

    # ------------------------------------------------------------ alertes
    def _mark_current_as_seen(self):
        try:
            self.seen |= {m["id"] for m in self._list(QUERY + " is:unread newer_than:2d", 30)}
            self._save_seen()
        except Exception:  # noqa: BLE001
            pass

    def _save_seen(self):
        self.seen_file.write_text(json.dumps(sorted(self.seen)[-500:]), encoding="utf-8")

    def _watch(self):
        """Toutes les 3 minutes : un mail important et non lu est arrivé ? Annonce (sans appel à Claude)."""
        first = True
        while True:
            time.sleep(20 if first else config.MAIL_CHECK_MINUTES * 60)
            first = False
            if not self.connected():
                continue
            try:
                if not self.seen:
                    self._mark_current_as_seen()
                    continue
                new = [m for m in self._list(QUERY + " is:unread is:important newer_than:1d", 10)
                       if m["id"] not in self.seen]
                for m in new:
                    self.seen.add(m["id"])
                self._save_seen()
                if new and not self.paused.is_set():
                    if len(new) == 1:
                        self.announce(f"Nouveau mail important de {new[0]['from']} : {new[0]['subject']}.")
                    else:
                        who = ", ".join(sorted({m['from'] for m in new})[:3])
                        self.announce(f"{len(new)} nouveaux mails importants, notamment de {who}.")
                    self.emit("mails", count=len(new))
            except Exception as exc:  # noqa: BLE001
                print(f"[gmail] {exc}", flush=True)
