"""Les oreilles de MADEX : un seul flux micro, en continu, qui fait tout.

- mesure le niveau de ta voix en direct (pour l'onde de l'interface) ;
- repère « Madex » / « Hey Madex » au début de ce que tu dis (mini-transcription locale) ;
- repère deux claquements de mains ;
- puis enregistre ta demande jusqu'au silence et la transcrit avec Whisper.

Tout reste sur ton PC : aucun son n'est envoyé sur internet.
"""
from __future__ import annotations

import difflib
import json
import queue
import re
import threading
import time
import unicodedata
from pathlib import Path

import numpy as np

from . import config
from .spectrum import bands

RATE = 16000
FRAME = 320                     # 20 ms
FRAME_S = FRAME / RATE


# ---------------------------------------------------------------- détection du nom
def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9' ]", " ", t).split()


WAKE_FORMS = ("madex", "madexe", "madeks", "madecks", "madecs", "madix", "medex", "matex", "madek", "madexx",
              "maddex", "madax", "madeix", "madaix", "mattex")
FILLERS = {"hey", "he", "hé", "eh", "ok", "okay", "dis", "allo", "salut", "bonjour", "yo", "et", "hep"}
SURE, MAYBE = 0.86, 0.68                            # au-dessus de SURE : c'est moi ; entre les deux : on vérifie


def wake_score(text: str) -> tuple[float, str]:
    """Meilleure ressemblance avec « Madex » au début de la phrase, et ce qui suit."""
    words = _norm(text)
    original = re.findall(r"\S+", text)
    i = 0
    while i < len(words) and i < 3 and words[i] in FILLERS:
        i += 1
    best, rest = 0.0, ""
    for span in (1, 2, 3):                          # « mad x », « ma dex »…
        if i + span > len(words):
            break
        joined = "".join(words[i:i + span])
        score = max(difflib.SequenceMatcher(None, joined, w).ratio() for w in WAKE_FORMS)
        if score > best:
            tail = " ".join(original[i + span:]) if len(original) == len(words) else " ".join(words[i + span:])
            best, rest = score, tail.lstrip(" ,.!?:;-").strip()
    return best, rest


def split_wake(text: str) -> tuple[bool, str]:
    """« Hey Madex, ouvre Spotify » -> (True, "ouvre Spotify"). Tolère les erreurs de transcription."""
    score, rest = wake_score(text)
    return (True, rest) if score >= SURE else (False, "")


# Phrases que Whisper invente sur du silence ou un bruit (sous-titres de vidéos)
HALLUCINATIONS = ("sous-titres", "sous titres", "sous-titrage", "amara.org", "merci d'avoir regardé",
                  "abonnez-vous", "radio-canada", "merci de votre attention")


def _clean(text: str) -> str:
    low = text.lower()
    return "" if any(h in low for h in HALLUCINATIONS) else text


# ---------------------------------------------------------------- claquements de mains
class ClapDetector:
    """Deux claquements secs espacés de 0,2 à 0,8 s. Une voix ou une musique continue ne déclenchent pas."""

    def __init__(self):
        self.candidate: tuple[float, float] | None = None   # (instant, énergie du pic)
        self.frames_after = 0
        self.claps: list[float] = []
        self.cooldown_until = 0.0
        self.recent: list[float] = []                    # énergie des 4 dernières trames (80 ms)

    def feed(self, frame: np.ndarray, rms: float, noise: float, now: float) -> bool:
        if now < self.cooldown_until:
            return False
        peak = float(np.abs(frame).max())
        quiet_before = bool(self.recent) and max(self.recent) < max(noise * 3, 600)
        self.recent = (self.recent + [rms])[-4:]
        if self.candidate is None:
            if peak > max(9000, noise * 12) and rms > noise * 6 and quiet_before:   # surgit du silence
                self.candidate, self.frames_after = (now, rms), 0
            return False
        self.frames_after += 1
        if self.frames_after == 5:                         # 100 ms après : un claquement est déjà retombé
            t0, e0 = self.candidate
            self.candidate = None
            if rms < e0 * 0.25:
                self.claps = [c for c in self.claps if now - c < 1.2] + [t0]
                if len(self.claps) >= 2 and 0.2 <= self.claps[-1] - self.claps[-2] <= 0.8:
                    self.claps.clear()
                    self.cooldown_until = now + 2.0
                    return True
        return False


# ---------------------------------------------------------------- transcription
PRECISION = {                    # réglable dans Diagnostic (ou « auto » selon le PC)
    "rapide": "small",
    "precis": "medium",
    "max": "large-v3-turbo",
}
SETTINGS = config.DATA / "ecoute.json"


def auto_precision() -> tuple[str, str, str]:
    """Choisit le modèle le plus précis que le PC encaisse sans ralentir : (niveau, appareil, type de calcul)."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "max", "cuda", "float16"
    except Exception:  # noqa: BLE001
        pass
    try:
        import os

        import psutil
        if (os.cpu_count() or 4) >= 8 and psutil.virtual_memory().total >= 12 * 2**30:
            return "precis", "cpu", "int8"
    except Exception:  # noqa: BLE001
        pass
    return "rapide", "cpu", "int8"


class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel
        self._WM = WhisperModel
        self.extra_words: list[str] = []
        self.vocab = None                                  # branché par le cœur (mots appris + corrections)
        self.choice = "auto"
        try:
            self.choice = json.loads(SETTINGS.read_text()).get("precision", "auto")
        except (OSError, ValueError):
            pass
        print(f"[MADEX] Chargement de Whisper ({config.WHISPER_SPOT_MODEL})…", flush=True)
        self.spot_model = WhisperModel(config.WHISPER_SPOT_MODEL, device="cpu", compute_type="int8", num_workers=2)
        # démarrage immédiat avec « small » (installé) ; un modèle plus précis se charge ensuite en arrière-plan
        try:
            self.main_model, self.level = WhisperModel("small", device="cpu", compute_type="int8", num_workers=2), "small"
        except Exception:  # noqa: BLE001
            self.main_model, self.level = self.spot_model, config.WHISPER_SPOT_MODEL
        self.loading = ""
        self.set_precision(self.choice, wait=False)

    def set_precision(self, choice: str, wait: bool = False):
        """Change le modèle principal (téléchargé au premier usage). L'ancien sert pendant le chargement."""
        if choice not in ("auto", *PRECISION):
            raise ValueError("précision inconnue")
        auto = auto_precision()
        if choice == "auto":
            level, device, ctype = auto
        else:                                   # le GPU sert dès qu'il est là, quel que soit le niveau choisi
            level, device, ctype = choice, auto[1], auto[2]
        self.choice = choice
        config.DATA.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps({"precision": choice}), encoding="utf-8")

        def load():
            name = PRECISION[level]
            if name == self.level and device == "cpu":
                return
            self.loading = name
            try:
                print(f"[MADEX] Modèle de transcription : {name} ({device})…", flush=True)
                self.main_model = self._WM(name, device=device, compute_type=ctype, num_workers=2)
                self.level = name
            except Exception as exc:  # noqa: BLE001  (pas de réseau, mémoire…) : on garde l'ancien
                print(f"[MADEX] {name} indisponible ({exc}) : je garde {self.level}", flush=True)
                if self.main_model is self.spot_model and name != "small":
                    try:
                        self.main_model, self.level = self._WM("small", device="cpu", compute_type="int8"), "small"
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                self.loading = ""
        if wait:
            load()
        else:
            threading.Thread(target=load, daemon=True).start()

    def status(self) -> dict:
        return {"choice": self.choice, "model": self.level, "loading": self.loading,
                "options": {"auto": "auto", **PRECISION}}

    @staticmethod
    def _f32(audio: np.ndarray) -> np.ndarray:
        return audio.astype(np.float32) / 32768.0

    def _hot(self) -> str:
        words = ["Madex"] + (self.vocab.words[-30:] if self.vocab else [])
        return " ".join(dict.fromkeys(words))

    def _run(self, model, audio, **kw):
        try:                                                    # « hotwords » : faster-whisper >= 1.0
            segs, _ = model.transcribe(audio, hotwords=self._hot(), **kw)
        except TypeError:
            segs, _ = model.transcribe(audio, **kw)
        return list(segs)

    def spot(self, audio: np.ndarray, precise: bool = False) -> str:
        """Transcription rapide du début d'une phrase, juste pour savoir si on m'appelle.
        precise=True : seconde écoute avec le grand modèle quand le premier a « presque » entendu Madex."""
        model = self.main_model if precise else self.spot_model
        segs = self._run(model, self._f32(audio[: int(RATE * 3)]), language=config.LANGUAGE,
                         beam_size=2 if precise else 1, initial_prompt="Hey Madex,", condition_on_previous_text=False)
        return _clean(" ".join(s.text.strip() for s in segs if s.no_speech_prob < 0.6).strip())

    def partial(self, audio: np.ndarray) -> str:
        """Aperçu en direct pendant que Madou parle (petit modèle, rapide, sans filtre)."""
        segs = self._run(self.spot_model, self._f32(audio[-int(RATE * 12):]), language=config.LANGUAGE, beam_size=1,
                         condition_on_previous_text=False)
        return _clean(" ".join(s.text.strip() for s in segs).strip())

    def full(self, audio: np.ndarray) -> str:
        words = [config.WHISPER_HINT] + self.extra_words[:40] + (self.vocab.words[-60:] if self.vocab else [])
        hint = ", ".join(w for w in words if w)[:900]
        segs = self._run(self.main_model, self._f32(audio), language=config.LANGUAGE, beam_size=5,
                         vad_filter=True, initial_prompt=hint)
        keep = [s.text.strip() for s in segs if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = _clean(" ".join(keep).strip())
        return self.vocab.apply(text) if self.vocab and text else text


# ---------------------------------------------------------------- détection de la voix (Silero VAD)
class SileroVAD:
    """Petit réseau qui reconnaît la VOIX humaine (pas la musique, pas les bruits), 32 ms à la fois.
    Modèle fourni avec faster-whisper (silero_vad_v6.onnx) : rien à télécharger."""

    def __init__(self):
        import os

        import faster_whisper
        import onnxruntime as ort
        path = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
        self.reset()

    def reset(self):
        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.ctx = np.zeros(64, np.float32)
        self.buf = np.zeros(0, np.float32)
        self.prob = 0.0

    def feed(self, frame: np.ndarray) -> float:
        self.buf = np.concatenate([self.buf, frame.astype(np.float32) / 32768.0])
        while len(self.buf) >= 512:
            chunk, self.buf = self.buf[:512], self.buf[512:]
            out, self.h, self.c = self.sess.run(None, {"input": np.concatenate([self.ctx, chunk])[None],
                                                       "h": self.h, "c": self.c})
            self.ctx = chunk[-64:]
            self.prob = float(np.ravel(out)[-1])
        return self.prob


HESITATION = re.compile(r"(\b(euh|heu|hum|et|mais|donc|alors|avec|de|du|des|le|la|les|un|une|pour|que|qui|sur|dans|en|à|a)|,)\s*\W*$",
                        re.I)


# ---------------------------------------------------------------- le moteur
STOP_WORDS = re.compile(r"\b(stop|arrete|arrête|attends|attend|tais[ -]toi|chut|silence|pause|madex|non|ok|merci|c'est bon)\b", re.I)
ONLY_STOP = re.compile(r"^\W*(stop|arr[eê]te(-toi)?|attends?|tais[ -]toi|chut|silence|c'est bon|merci|ok)\W*$", re.I)


class Ears(threading.Thread):
    """Un Jarvis : toujours prêt, sans « mode » à annoncer.

    veille  -> j'attends « Madex » (ou un double clap, ou un clic)
    ecoute  -> j'enregistre ta demande jusqu'au silence
    suivi   -> juste après ma réponse, quelques secondes où tu peux enchaîner SANS redire « Madex »
    Pendant que je parle, je me tais si tu m'interromps avec un mot clair (« stop », « attends », « Madex »…).

    Toutes les décisions sont prises dans le fil audio : les autres fils déposent une demande dans `requests`."""

    BEEP_GUARD = 0.4             # s : le bip de Madex n'est jamais pris pour ta voix
    MIN_VOICE = 0.25             # s de voix avant de considérer que tu as commencé à parler
    FIRST_WORD_WAIT = 6.0        # s pour commencer à parler après l'ouverture du micro
    FOLLOW_WINDOW = 7.0          # s pour enchaîner après une réponse, sans redire « Madex »
    BARGE_VOICE = 0.45           # s de voix (VAD) pendant que je parle avant de vérifier si c'est toi
    PREROLL = 40                 # trames (0,8 s) gardées AVANT la détection : la première syllabe n'est jamais perdue
    END_FAST = 0.7               # s de silence pour finir une phrase complète
    END_SLOW = 1.5               # s si la phrase semble inachevée (« euh », « et », virgule…)
    STUCK_MUTE = 150.0           # s : filet de sécurité si le micro restait coupé

    def __init__(self, on_text, on_level, on_state, beep, muted: threading.Event, on_heard=None,
                 on_interrupt=None, on_partial=None, get_spoken=None, on_suspect=None):
        super().__init__(daemon=True)
        self.on_text, self.on_level, self.on_state, self.beep, self.muted = on_text, on_level, on_state, beep, muted
        self.on_heard = on_heard or (lambda text: None)
        self.on_interrupt = on_interrupt or (lambda: None)
        self.on_partial = on_partial or (lambda text: None)
        self.get_spoken = get_spoken or (lambda: "")        # ce que Madex est en train de dire (pour reconnaître l'écho)
        self.on_suspect = on_suspect or (lambda on: None)   # baisse le volume de Madex le temps de vérifier
        self.last_partial = ""
        self.preroll: list[np.ndarray] = []
        self.log = __import__("madex.log", fromlist=["x"]).get("ecoute")
        try:
            self.vad = SileroVAD()
            self.log.info("détection de la voix : Silero VAD")
        except Exception as exc:  # noqa: BLE001  (onnxruntime absent…) : on garde le seuil de volume
            self.vad = None
            self.log.warning(f"Silero VAD indisponible ({exc}) : détection au volume")
        self.partial_busy = False
        self.partial_at = 0.0
        self.stop_event = threading.Event()
        self.transcriber = Transcriber()
        self.custom_wake = None
        model = config.ROOT / "models" / "madex.onnx"
        if model.exists():                                   # modèle « Hey Madex » entraîné (optionnel)
            from openwakeword.model import Model
            self.custom_wake = Model(wakeword_models=[str(model)], inference_framework="onnx")
        self.jobs: queue.Queue = queue.Queue()
        self.requests: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._worker, daemon=True).start()
        self.state = "veille"
        self.enabled = True
        self.device_name = ""
        self.noise = 200.0
        self.calibrated = 0
        self.claps = ClapDetector()
        self._reset_segment()
        self.listen_buf: list[np.ndarray] = []
        self.listen_meta: dict = {}
        self.awaiting_spot = False
        self.post_buf: list[np.ndarray] = []
        self._last_level = 0.0
        self._ring: list[np.ndarray] = []
        self.ignore_until = 0.0
        self.muted_since: float | None = None
        self.echo = 300.0
        self._reset_barge()
        self.last_error = ""

    # ----- commandes externes (n'importe quel fil)
    def listen_now(self):
        """Clic sur le micro : j'écoute (et si j'écoutais déjà, j'envoie tout de suite)."""
        self.requests.put(("toggle", None))

    def set_enabled(self, on: bool):
        self.requests.put(("enable", bool(on)))

    def follow_up(self):
        """Après une réponse : courte fenêtre pour enchaîner sans redire « Madex »."""
        self.requests.put(("followup", None))

    def stop(self):
        self.stop_event.set()

    @property
    def threshold(self) -> float:
        return max(self.noise * 2.6, 220.0)

    # ----- boucle audio
    def _reset_segment(self):
        self.seg: list[np.ndarray] = []
        self.seg_voiced = 0
        self.seg_silence = 0.0

    def _reset_barge(self):
        self.barge_buf: list[np.ndarray] = []
        self.barge_voice = 0.0
        self.barge_gap = 0.0
        self.barge_pending = False

    def run(self):
        import sounddevice as sd
        while not self.stop_event.is_set():
            try:
                try:
                    self.device_name = sd.query_devices(kind="input")["name"]
                except Exception:  # noqa: BLE001
                    self.device_name = "?"
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
                    self.last_error = ""
                    while not self.stop_event.is_set():
                        frame, _ = stream.read(FRAME)
                        self.process(frame[:, 0], time.monotonic())
            except Exception as exc:  # noqa: BLE001  micro débranché, changé… on réessaie
                self.last_error = f"micro indisponible : {exc}"
                print(f"[audio] {self.last_error}, nouvel essai dans 2 s", flush=True)
                time.sleep(2)

    def _handle_requests(self, now: float):
        while True:
            try:
                kind, arg = self.requests.get_nowait()
            except queue.Empty:
                return
            if kind == "enable":
                self.enabled = arg
                self._reset_segment()
                self.awaiting_spot = False
                if not arg and self.state in ("ecoute", "suivi"):
                    self.state, self.listen_buf = "veille", []
                self.on_state("idle" if arg else "mic_off")
            elif kind == "toggle":
                if self.state == "ecoute":
                    self._finish_listening()
                else:
                    self.enabled = True                     # un clic rouvre toujours le micro
                    self.muted.clear()
                    self._enter_listening([], now, gate=False)
            elif kind == "listen" and self.state != "ecoute" and self.enabled:
                frames, meta = arg if isinstance(arg, tuple) else (arg or [], {})
                self._enter_listening(frames, now, **meta)
            elif kind == "followup" and self.state == "veille" and self.enabled and not self.awaiting_spot:
                self._reset_segment()
                self.state = "suivi"
                self.listen_buf = []
                self.listen_voice_s = 0.0
                self.follow_start = now
                self.ignore_until = now + 0.2
                self.on_state("followup")

    def process(self, frame: np.ndarray, now: float):
        """Traite 20 ms de son. Séparé de run() pour pouvoir être testé sans micro."""
        self._handle_requests(now)
        if not self.enabled:
            return
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        prob = self.vad.feed(frame) if self.vad else 0.0
        self.preroll = (self.preroll + [frame])[-self.PREROLL:]
        barge_listen = self.state == "ecoute" and self.listen_meta.get("barge")
        if self.muted.is_set() and not barge_listen:    # Madex parle (sauf : on vient de me couper, j'écoute la suite)
            self.muted_since = self.muted_since or now
            if now - self.muted_since > self.STUCK_MUTE:
                print("[audio] micro resté coupé trop longtemps : je le rouvre", flush=True)
                self.muted.clear()
            if self.state in ("ecoute", "suivi"):
                self.state, self.listen_buf = "veille", []
            self._reset_segment()
            self._barge_frame(frame, rms, prob)
            self.ignore_until = now + 0.15              # l'écho de sa voix dans la pièce
            return
        if self.muted_since is not None:
            self.muted_since = None
            self._reset_barge()
        self._ring = (self._ring + [frame])[-3:]               # ~60 ms pour un spectre lisible
        if now - self._last_level > 0.06:
            self.on_level("madou", min(1.0, rms / 6000), bands(np.concatenate(self._ring), RATE))
            self._last_level = now
        if now < self.ignore_until:                     # pendant le bip
            return
        if self.vad:                                    # voix humaine ET un minimum de volume (pas un murmure lointain)
            speaking = prob > 0.6 and rms > max(self.noise * 1.6, 120)
        else:
            speaking = rms > self.threshold
        if not speaking and self.state == "veille" and not self.seg:
            k = 0.1 if self.calibrated < 100 else 0.005  # les 2 premières secondes : calibrage rapide
            self.noise = (1 - k) * self.noise + k * rms
            self.calibrated += 1
        if self.claps.feed(frame, rms, self.noise, now) and self.state in ("veille", "suivi"):
            self._enter_listening([], now, gate=False)
            return
        if self.state == "ecoute":
            self._listen_frame(frame, speaking, now)
        elif self.state == "suivi":
            self._follow_frame(frame, speaking, now)
        elif self.awaiting_spot:
            self.post_buf.append(frame)
        elif self.custom_wake is not None:
            if max(self.custom_wake.predict(frame).values()) >= config.WAKE_THRESHOLD:
                self.custom_wake.reset()
                self._enter_listening([], now, gate=False)
        else:
            self._spot_frame(frame, speaking)

    # ----- pendant que Madex parle : m'interrompt-on ?
    def _barge_frame(self, frame: np.ndarray, rms: float, prob: float = 0.0):
        loud = rms > max(self.threshold * 1.4, self.echo * 1.8)
        voice = (prob > 0.7 and loud) if self.vad else rms > max(self.threshold * 1.8, self.echo * 2.2)
        k = 0.002 if loud else 0.03                             # l'écho de ma voix s'apprend sur les trames « normales »
        self.echo = (1 - k) * self.echo + k * rms               # (une voix forte par-dessus ne le fausse pas)
        if self.barge_pending:
            return
        if voice:
            if not self.barge_buf:
                self.barge_buf = list(self.preroll[-10:])       # 0,2 s avant : le début du mot
            self.barge_buf.append(frame)
            self.barge_voice += FRAME_S
            self.barge_gap = 0.0
        elif self.barge_buf:
            self.barge_buf.append(frame)
            self.barge_gap += FRAME_S
            if self.barge_gap > 0.3:                           # un bruit isolé : on oublie
                self._reset_barge()
        if self.barge_voice >= self.BARGE_VOICE:
            self.barge_pending = True
            self.on_suspect(True)                              # on baisse Madex tout de suite, le temps de vérifier
            self.jobs.put(("barge", np.concatenate(self.barge_buf), {}))

    # ----- veille : repérer « Madex »
    def _spot_frame(self, frame: np.ndarray, speaking: bool):
        if speaking:
            if not self.seg:
                self.seg = list(self.preroll[-25:-1])         # 0,5 s avant la détection
            self.seg.append(frame)
            self.seg_voiced += 1
            self.seg_silence = 0.0
        elif self.seg:
            self.seg.append(frame)
            self.seg_silence += FRAME_S
            if self.seg_silence >= 0.5 or len(self.seg) * FRAME_S > 30:
                if self.seg_voiced * FRAME_S >= 0.25:         # assez de voix pour être un mot
                    self.awaiting_spot, self.post_buf = True, []
                    self.jobs.put(("spot", np.concatenate(self.seg), {}))
                self._reset_segment()
        if len(self.seg) * FRAME_S > 30:
            self._reset_segment()

    # ----- suivi : enchaîner sans redire « Madex »
    def _follow_frame(self, frame: np.ndarray, speaking: bool, now: float):
        self.listen_buf = (self.listen_buf + [frame])[-25:]   # garde le début du mot (0,5 s)
        if speaking:
            self.listen_voice_s += FRAME_S
            if self.listen_voice_s >= self.MIN_VOICE:          # tu enchaînes : j'enregistre, sans bip
                frames = self.listen_buf
                self._enter_listening(frames, now, gate=True, beep=False)
                self.listen_voice_s = self.MIN_VOICE
                return
        elif now - self.follow_start > self.FOLLOW_WINDOW:
            self.state, self.listen_buf = "veille", []
            self.on_state("idle")

    # ----- écoute d'une demande
    def _enter_listening(self, initial: list[np.ndarray], now: float, gate: bool = False, beep: bool = True,
                         barge: bool = False):
        self._reset_segment()
        self.awaiting_spot = False
        self.state = "ecoute"
        self.listen_buf = list(initial)
        self.listen_meta = {"gate": gate, "barge": barge}
        self.listen_voice_s = sum(FRAME_S for f in initial
                                  if float(np.sqrt(np.mean(f.astype(np.float32) ** 2))) > self.threshold)
        self.listen_silence = 0.0
        self.listen_start = now
        self.on_state("listening")
        if beep:
            self.ignore_until = now + self.BEEP_GUARD
            threading.Thread(target=self.beep, daemon=True).start()

    def _listen_frame(self, frame: np.ndarray, speaking: bool, now: float):
        self.listen_buf.append(frame)
        if (self.listen_voice_s >= 0.6 and not self.partial_busy and now - self.partial_at > 1.0
                and len(self.listen_buf) > 40):          # aperçu en direct de ce que je comprends
            self.partial_busy, self.partial_at = True, now
            threading.Thread(target=self._partial, args=(np.concatenate(self.listen_buf),), daemon=True).start()
        if speaking:
            self.listen_voice_s += FRAME_S
            self.listen_silence = 0.0
        else:
            self.listen_silence += FRAME_S
        started = self.listen_voice_s >= self.MIN_VOICE
        if not started:
            limit = self.FIRST_WORD_WAIT
        else:                                            # phrase finie : on envoie vite ; « euh… », on attend
            limit = self.END_SLOW if HESITATION.search(self.last_partial or "") else self.END_FAST
            limit = max(limit, min(config.SILENCE_SECONDS, self.END_SLOW)) if config.SILENCE_SECONDS > 1.3 else limit
        if self.listen_silence >= limit or now - self.listen_start > config.MAX_RECORD_SECONDS:
            self._finish_listening()

    def _finish_listening(self):
        audio = np.concatenate(self.listen_buf) if self.listen_buf else np.zeros(0, np.int16)
        started = self.listen_voice_s >= self.MIN_VOICE
        meta = self.listen_meta
        self.state, self.listen_buf, self.last_partial = "veille", [], ""
        if started and len(audio):
            from . import log as _log
            _log.new_turn("voix", f"{len(audio) / RATE:.1f} s de parole")
            self.on_state("transcribing")
            self.jobs.put(("full", audio, meta))
        else:
            self.on_state("idle")

    def _partial(self, audio: np.ndarray):
        try:
            text = self.transcriber.partial(audio)
            if text and self.state == "ecoute":
                self.last_partial = text
                self.on_partial(text)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.partial_busy = False

    # ----- transcriptions (hors de la boucle audio pour ne perdre aucun son)
    def _worker(self):
        while True:
            kind, audio, meta = self.jobs.get()
            try:
                if kind == "spot":
                    self._job_spot(audio)
                elif kind == "barge":
                    self._job_barge(audio)
                else:
                    t0 = time.monotonic()
                    text = self.transcriber.full(audio)
                    from . import log as _log
                    _log.mark("transcription", f"{time.monotonic() - t0:.2f} s de calcul, modèle {getattr(self.transcriber, 'level', '?')}")
                    called, rest = split_wake(text)
                    if called:                                  # « Madex, … » dit pendant le suivi
                        text = rest
                    print(f"[écoute] demande « {text} »", flush=True)
                    self.on_state("idle")
                    if len(text) >= 2:
                        self.on_text(text)
                    elif not meta.get("gate"):
                        self.on_heard("")
            except Exception as exc:  # noqa: BLE001
                print(f"[audio] {exc}", flush=True)
                self.last_error = str(exc)
                self.awaiting_spot = False
                self.on_state("idle")

    def _job_spot(self, audio: np.ndarray):
        text = self.transcriber.spot(audio)
        score, rest = wake_score(text)
        if MAYBE <= score < SURE:                  # « presque » Madex : seconde écoute plus fine
            text = self.transcriber.spot(audio, precise=True)
            score, rest = wake_score(text)
        print(f"[écoute] entendu « {text} » (ressemblance {score:.2f})", flush=True)
        if score < SURE:
            self.awaiting_spot = False
            if text:
                self.on_heard(text)
            return
        if rest or len(audio) / RATE > 2.6:       # « Madex, ouvre Spotify » d'une traite :
            from . import log as _log
            _log.new_turn("voix", text)
            self.awaiting_spot = False            # on retranscrit tout avec le bon modèle
            self.on_state("transcribing")
            called_full, rest_full = split_wake(self.transcriber.full(audio))
            self.on_state("idle")
            command = rest_full if called_full else rest
            if len(command) >= 2:
                self.on_text(command)
            else:
                self.requests.put(("listen", ([], {"gate": False})))
        else:                                      # juste « Madex » : j'écoute la suite
            pending, self.post_buf = self.post_buf, []
            self.awaiting_spot = False
            self.requests.put(("listen", (pending, {"gate": False})))

    def _job_barge(self, audio: np.ndarray):
        """Pendant que je parle, une voix : Madou qui me coupe, ou l'écho de ma propre voix ?
        On transcrit le bout entendu et on le compare à ce que je suis en train de dire."""
        try:
            text = self.transcriber.spot(audio)
            mine = self.get_spoken() or ""
            words = _norm(text)
            echo = 0.0
            if words and mine:
                mw = set(_norm(mine))
                echo = sum(1 for w in words if w in mw) / len(words)
            ok = bool(words) and (STOP_WORDS.search(text) is not None or (len(words) >= 2 and echo < 0.5))
            self.log.info(f"interruption ? {'oui' if ok else 'non'} : « {text} » (ressemble à ma voix : {echo:.0%})")
            if ok:
                self.on_interrupt()
                if ONLY_STOP.match(text):               # juste « stop » : je me tais, rien d'autre (pas la musique)
                    self.on_state("idle")
                    return
                frames = [audio[i:i + FRAME] for i in range(0, len(audio) - FRAME + 1, FRAME)]
                self.requests.put(("listen", (frames, {"gate": False, "beep": False, "barge": True})))
            else:
                self.on_suspect(False)                  # fausse alerte : Madex reprend son volume
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"vérification d'interruption impossible : {exc}")
            self.on_suspect(False)
        finally:
            self._reset_barge()


def save_wav(path: Path, audio: np.ndarray):
    """Utile pour déboguer : enregistre ce que Madex a entendu."""
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.astype(np.int16).tobytes())
