"""La voix de MADEX, avec trois niveaux pour ne jamais rester muet :

1. ElevenLabs (voix choisie dans l'onglet CONNEXIONS, en streaming) ;
2. voix Edge gratuite (internet requis) ;
3. voix Windows intégrée (hors ligne, toujours disponible).

Pendant qu'il parle, le niveau et le spectre réels partent vers l'interface : le réacteur suit sa voix.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave

import numpy as np

from . import config, secrets_store
from .spectrum import bands

RATE = 22050
API = "https://api.elevenlabs.io/v1"
PREFERRED_VOICES = ("Daniel", "George", "Brian", "Adam", "Bill")   # masculines et posées
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class QuotaExceeded(Exception):
    pass


class KeyRefused(Exception):
    pass


def _to_rate(pcm: np.ndarray, src: int) -> np.ndarray:
    if src == RATE or not len(pcm):
        return pcm
    x = np.linspace(0, len(pcm) - 1, int(len(pcm) * RATE / src))
    return np.interp(x, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)


class Mouth:
    def __init__(self, on_level, on_notice, on_status=lambda s: None):
        import sounddevice  # noqa: F401  (vérifie que l'audio est disponible)
        self.on_level = on_level              # ("madex", 0..1, bandes)
        self.on_notice = on_notice            # phrase à dire une fois (ex. quota épuisé)
        self.on_status = on_status            # état de la voix pour l'interface
        self.state_file = config.DATA / "voix.json"
        self.state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        self.key = secrets_store.get("ELEVENLABS_API_KEY")
        self.voice_id = config.ELEVENLABS_VOICE_ID or self.state.get("voice_id", "")
        self.engine = "aucune"
        self.last_error = ""
        self.key_refused = False
        self.interrupted = threading.Event()  # Madou m'interrompt
        self.gain = 1.0                       # baissé à 0,3 le temps de vérifier une possible interruption
        self.on_speak_start = lambda: None    # Spotify baisse la musique
        self.on_speak_end = lambda: None
        self.usage = {}
        if self.key:
            threading.Thread(target=self._prepare, daemon=True).start()

    # ------------------------------------------------------------ état
    def _save(self):
        config.DATA.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def status(self) -> dict:
        return {"engine": self.engine, "has_key": bool(self.key), "key_refused": self.key_refused,
                "voice_id": self.voice_id, "voice_name": self.state.get("voice_name", ""),
                "disabled_until": self.state.get("disabled_until", ""), "error": self.last_error, **self.usage}

    def _error(self, msg: str):
        self.last_error = msg
        print(f"[voix] {msg}", flush=True)
        self.on_status(self.status())

    def _premium_available(self) -> bool:
        until = self.state.get("disabled_until")
        return bool(self.key) and not self.key_refused and not (until and dt.date.today().isoformat() < until)

    def _request(self, method: str, path: str, body: dict | None = None, timeout=15):
        req = urllib.request.Request(API + path, method=method,
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"xi-api-key": self.key, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)

    # ------------------------------------------------------------ réglages (onglet CONNEXIONS)
    def set_key(self, key: str):
        secrets_store.set_value("ELEVENLABS_API_KEY", key.strip())
        self.key, self.key_refused, self.last_error = key.strip(), False, ""
        self.state.pop("disabled_until", None)
        self._save()
        self._prepare()

    def list_voices(self) -> list[dict]:
        """Les voix disponibles sur ton compte (les voix « premade » sont gratuites)."""
        if not self.key:
            raise ValueError("Ajoute d'abord ta clé ElevenLabs.")
        try:
            with self._request("GET", "/voices") as r:
                voices = json.loads(r.read()).get("voices", [])
        except urllib.error.HTTPError as e:
            raise ValueError(self._explain(e.code, e.read().decode("utf-8", "replace"))) from e
        out = []
        for v in voices:
            labels = v.get("labels") or {}
            out.append({"id": v["voice_id"], "name": v.get("name", ""), "category": v.get("category", ""),
                        "gender": labels.get("gender", ""), "accent": labels.get("accent", ""),
                        "age": labels.get("age", ""), "description": labels.get("description", "") or labels.get("descriptive", ""),
                        "preview": v.get("preview_url", "")})
        out.sort(key=lambda v: (v["gender"] != "male", v["category"] != "premade", v["name"]))
        return out

    def set_voice(self, voice_id: str, name: str):
        self.voice_id = voice_id
        self.state.update(voice_id=voice_id, voice_name=name)
        self._save()
        self.on_status(self.status())

    @staticmethod
    def _explain(code: int, detail: str) -> str:
        d = detail.lower()
        if "missing_permissions" in d or "permission" in d:
            return ("Clé ElevenLabs sans les bonnes permissions : recrée-la en activant « Text to Speech » "
                    "(et « Voices : read », « User : read »).")
        if code == 401:
            return "Clé ElevenLabs refusée (invalide ou révoquée)."
        if "quota" in d or "credits" in d:
            return "Quota ElevenLabs épuisé pour ce mois."
        if "unusual" in d or "abuse" in d or "free tier" in d:
            return "ElevenLabs bloque l'accès gratuit depuis ce réseau (VPN ?). Voix de secours utilisée."
        return f"ElevenLabs a répondu {code}."

    def _prepare(self):
        """Au premier lancement : choisit une voix masculine posée, et lit le quota restant."""
        try:
            if not self.voice_id:
                voices = self.list_voices()
                by_name = {v["name"].split(" ")[0]: v for v in voices}
                pick = next((by_name[n] for n in PREFERRED_VOICES if n in by_name), None) or (voices[0] if voices else None)
                if pick:
                    self.set_voice(pick["id"], pick["name"])
            self.refresh_usage()
        except Exception as exc:  # noqa: BLE001
            self._error(str(exc))

    def refresh_usage(self):
        try:
            with self._request("GET", "/user/subscription") as r:
                sub = json.loads(r.read())
            self.usage = {"used": sub.get("character_count", 0), "limit": sub.get("character_limit", 0)}
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ moteurs
    def _eleven_stream(self, text: str):
        """PCM 22 050 Hz au fil de l'eau ; si ce format est refusé par l'offre, MP3 décodé."""
        body = {"text": text, "model_id": config.ELEVENLABS_MODEL,
                "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "style": 0.1, "speed": 1.0}}
        fmt = self.state.get("format", "pcm_22050")
        try:
            r = self._request("POST", f"/text-to-speech/{self.voice_id}/stream?output_format={fmt}", body, 30)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            low = detail.lower()
            if "quota" in low or "credits" in low:
                raise QuotaExceeded() from e
            if e.code in (401, 403) and ("permission" in low or e.code == 401):
                raise KeyRefused(self._explain(e.code, detail)) from e
            if fmt.startswith("pcm") and e.code in (400, 403, 422):
                self.state["format"] = "mp3_44100_128"
                self._save()
                yield from self._eleven_stream(text)
                return
            raise RuntimeError(self._explain(e.code, detail)) from e
        with r:
            if fmt.startswith("pcm"):
                leftover = b""
                while True:
                    chunk = r.read(4410)
                    if not chunk:
                        break
                    chunk = leftover + chunk
                    cut = len(chunk) - (len(chunk) % 2)
                    leftover = chunk[cut:]
                    yield np.frombuffer(chunk[:cut], dtype=np.int16)
            else:
                yield self._decode_mp3(r.read())

    @staticmethod
    def _decode_mp3(data: bytes) -> np.ndarray:
        import miniaudio
        dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=RATE)
        return np.frombuffer(dec.samples.tobytes(), dtype=np.int16)

    def _edge_pcm(self, text: str) -> np.ndarray:
        import edge_tts

        async def collect():
            data = bytearray()
            async for part in edge_tts.Communicate(text, config.TTS_VOICE, rate=config.TTS_RATE).stream():
                if part["type"] == "audio":
                    data.extend(part["data"])
            return bytes(data)

        mp3 = asyncio.run(collect())
        if not mp3:
            raise RuntimeError("la voix Edge n'a rien renvoyé")
        return self._decode_mp3(mp3)

    @staticmethod
    def _windows_pcm(text: str) -> np.ndarray:
        """Voix intégrée à Windows : marche hors ligne, sans compte."""
        if os.name != "nt":
            raise RuntimeError("voix Windows indisponible hors de Windows")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        script = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'fr*' } | Select-Object -First 1; "
                  "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $s.Rate = 1; "
                  f"$s.SetOutputToWaveFile('{path}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", script], input=text, text=True,
                           encoding="utf-8", capture_output=True, timeout=60, creationflags=NO_WINDOW)
            with wave.open(path, "rb") as w:
                src, ch = w.getframerate(), w.getnchannels()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if ch == 2:
                pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
            return _to_rate(pcm, src)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _chunks(self, text: str):
        if self._premium_available() and self.voice_id:
            try:
                first = True
                for chunk in self._eleven_stream(text):
                    if first:
                        self.engine, first = "ElevenLabs", False
                    yield chunk
                if not first:
                    return
            except QuotaExceeded:
                first_day = dt.date.today().replace(day=1)
                self.state["disabled_until"] = (first_day + dt.timedelta(days=32)).replace(day=1).isoformat()
                self._save()
                self.on_notice("Mon quota de voix premium est épuisé pour ce mois, monsieur. Je passe sur ma voix de secours.")
            except KeyRefused as exc:
                self.key_refused = True
                self._error(str(exc))
            except Exception as exc:  # noqa: BLE001
                self._error(f"ElevenLabs indisponible ({exc}) : voix de secours pour cette phrase.")
        for name, engine in (("Edge", self._edge_pcm), ("Windows", self._windows_pcm)):
            try:
                pcm = engine(text)
                self.engine = name
                for i in range(0, len(pcm), 4410):
                    yield pcm[i:i + 4410]
                return
            except Exception as exc:  # noqa: BLE001
                self._error(f"Voix {name} indisponible ({exc}).")
        self._error("Aucune voix n'a fonctionné : vérifie la sortie audio de Windows.")

    # ------------------------------------------------------------ lecture
    def _play(self, chunks):
        import sounddevice as sd
        first_block = True
        self.gain = 1.0
        with sd.OutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=1024) as out:
            for chunk in chunks:
                if self.interrupted.is_set():
                    break
                for i in range(0, len(chunk), 1102):          # ~50 ms : le réacteur suit la voix
                    if self.interrupted.is_set():                # Madou m'a interrompu : je me tais net
                        break
                    block = chunk[i:i + 1102]
                    if not len(block):
                        continue
                    if first_block:
                        from . import log as _log
                        _log.mark("premier son", self.engine)
                        first_block = False
                    if self.gain != 1.0:
                        block = (block.astype(np.float32) * self.gain).astype(np.int16)
                    rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
                    self.on_level("madex", min(1.0, rms / 5000), bands(block, RATE))
                    out.write(block.reshape(-1, 1))
            time.sleep(0.05)
        self.on_level("madex", 0.0, None)

    def interrupt(self):
        self.interrupted.set()

    def say(self, text: str):
        if not text:
            return
        self.interrupted.clear()
        self.on_speak_start()
        try:
            self._play(self._chunks(text))
            self.on_status(self.status())
        except Exception as exc:  # noqa: BLE001
            self._error(f"Lecture audio impossible ({exc}) : vérifie la sortie audio de Windows.")
        finally:
            self.on_speak_end()

    def beep(self):
        import sounddevice as sd
        t = np.linspace(0, 0.14, int(RATE * 0.14), endpoint=False)
        tone = 0.18 * (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t)) * np.hanning(len(t))
        try:                                             # flux dédié : ne coupe jamais la voix en cours
            with sd.OutputStream(samplerate=RATE, channels=1, dtype="int16") as out:
                out.write((tone * 32767).astype(np.int16).reshape(-1, 1))
        except Exception:  # noqa: BLE001
            pass
