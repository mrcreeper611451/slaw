#!/usr/bin/env python3
"""
SLAW — chat IA scritta solo in Python + HTML + CSS (nessun JavaScript).

Usa solo la libreria standard di Python: non c'è nulla da installare per farla partire.
  - Modalità Locale : Ollama (https://ollama.com) sul tuo computer, offline
  - Modalità Online : Groq (https://console.groq.com/keys), gratis con una chiave
  - Voce / microfono / "Hey SLAW": opzionali, gestiti da Python sul computer che avvia il server
    (servono i pacchetti di requirements-voce.txt)

Avvio:   python server.py            (solo da questo computer)
         python server.py --lan      (raggiungibile anche dal telefono sulla stessa rete Wi-Fi)
"""

import argparse
import html
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"

# Modalità pubblica (sito online): si attiva da sola su Render e simili (variabile PORT) oppure con SLAW_HOSTED=1.
# In questa modalità il pubblico può solo chattare: la chiave Groq sta sul server (variabile GROQ_API_KEY)
# e nessuno può cambiarla, cambiare modello o usare voce/microfono del server.
HOSTED = bool(os.environ.get("SLAW_HOSTED") or os.environ.get("PORT"))
APP = "/chat"  # indirizzo della chat; in locale funziona anche "/"
RATE_LIMIT = int(os.environ.get("SLAW_RATE_LIMIT", "20"))       # messaggi per persona ogni 10 minuti
DAILY_LIMIT = int(os.environ.get("SLAW_DAILY_LIMIT", "1500"))   # messaggi totali al giorno (protegge la quota gratuita)
MAX_SESSIONS = 5000
_groq_model_override = {"model": None}  # in modalità pubblica il modello scelto resta in memoria, non su file

OLLAMA_BASE = os.environ.get("SLAW_OLLAMA_BASE", "http://localhost:11434")
GROQ_BASE = os.environ.get("SLAW_GROQ_BASE", "https://api.groq.com/openai/v1")
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

AI_FAIL = "Mi dispiace, non sono riuscito a generare una risposta. Riprova."

# ══════════════════════════════════════════════
#  MODELLI LOCALI (Ollama)
# ══════════════════════════════════════════════
MODELS = {
    "ultra":    {"id": "smollm2:360m", "label": "Ultra leggero", "max_tokens": 350,
                 "desc": "SmolLM2 360M — circa 250 MB, il più leggero. Risposte più semplici."},
    "light":    {"id": "llama3.2:1b",  "label": "Leggero",       "max_tokens": 500,
                 "desc": "Llama 3.2 1B — circa 1,3 GB, buon compromesso velocità/qualità."},
    "balanced": {"id": "llama3.2:3b",  "label": "Bilanciato",    "max_tokens": 500,
                 "desc": "Llama 3.2 3B — circa 2 GB, risposte più elaborate."},
    "ultramod": {"id": "llama3.1:8b",  "label": "Ultra Mod",     "max_tokens": 500,
                 "desc": "Llama 3.1 8B — circa 4,7 GB, il più potente. Serve un computer con GPU/RAM abbondante."},
}

# ══════════════════════════════════════════════
#  PROMPT
# ══════════════════════════════════════════════
PROMPT_COMMON = (
    "Sei SLAW, un assistente IA amichevole, curioso, diretto e preciso. "
    "Rispondi sempre in una di queste quattro lingue soltanto: italiano, inglese, francese o tedesco "
    "— di norma in italiano, a meno che l'utente scriva in un'altra di queste tre lingue, nel qual caso "
    "rispondi in quella. Non usare mai altre lingue. Sii chiaro, naturale e conciso: non generare testo "
    "ripetitivo, incoerente o eccessivamente lungo. "
)
PROMPT_OFFLINE = (
    "Importante: sei un modello che gira offline, senza accesso a internet, e la tua conoscenza si ferma "
    "alla data del tuo addestramento, che può essere già passata da tempo. Per prodotti recenti, eventi "
    "attuali, ultime versioni di qualcosa, o qualsiasi fatto che potrebbe essere cambiato di recente, "
    "avvisa chiaramente che le tue informazioni potrebbero essere superate invece di darle come certe. "
)
PROMPT_END = (
    "Se non sai qualcosa con certezza, dillo onestamente invece di inventare. "
    "Se ti chiedono chi ti ha creato o chi è il tuo creatore, rispondi che il tuo creatore si chiama "
    "Mattia, senza fornire il cognome perché non sei autorizzato a condividerlo."
)
SYSTEM_LOCAL = PROMPT_COMMON + PROMPT_OFFLINE + PROMPT_END
SYSTEM_ONLINE = PROMPT_COMMON + PROMPT_END

WAKE_PHRASES = ["hey slaw", "ehi slaw", "hei slaw", "ok slaw", "ciao slaw", "ei slaw"]

# ══════════════════════════════════════════════
#  VOCE (opzionale)
# ══════════════════════════════════════════════
try:
    import pyttsx3
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False

try:
    import speech_recognition as sr
    sr.Microphone.list_microphone_names()  # fallisce se manca PyAudio
    MIC_AVAILABLE = True
except Exception:
    MIC_AVAILABLE = False

_tts_lock = threading.Lock()


def speak(text):
    """Legge la risposta ad alta voce sul computer che esegue il server."""
    if not TTS_AVAILABLE:
        return
    clean = "".join(c for c in text if c not in "*_#`>~").strip()[:500]
    if not clean:
        return

    def run():
        with _tts_lock:
            try:
                engine = pyttsx3.init()
                engine.say(clean)
                engine.runAndWait()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()


def listen_once(timeout=6, phrase_limit=12):
    """Ascolta il microfono del computer e restituisce il testo (o None)."""
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            r.adjust_for_ambient_noise(source, duration=0.4)
            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
        return r.recognize_google(audio, language="it-IT").strip()
    except Exception:
        return None


# ══════════════════════════════════════════════
#  CONFIG (chiave Groq e modello Groq)
# ══════════════════════════════════════════════
_cfg_lock = threading.Lock()


def load_config():
    with _cfg_lock:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_config(**changes):
    with _cfg_lock:
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg.update(changes)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass


def get_groq_key():
    return os.environ.get("GROQ_API_KEY", "").strip() or load_config().get("groq_key", "").strip()


def get_groq_model():
    return _groq_model_override["model"] or load_config().get("groq_model") or GROQ_DEFAULT_MODEL


# ══════════════════════════════════════════════
#  RETE
# ══════════════════════════════════════════════
class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}" if status else str(body))
        self.status = status
        self.body = body


def http_json(url, payload=None, headers=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": "SLAW/1.0"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, e.read().decode("utf-8", "replace")[:300])
    except urllib.error.URLError as e:
        raise ApiError(0, e.reason)
    except (TimeoutError, socket.timeout):
        raise ApiError(0, "timeout: nessuna risposta in tempo utile")


def ollama_installed_models():
    """Ritorna la lista dei modelli installati in Ollama, oppure None se Ollama non risponde."""
    try:
        data = http_json(OLLAMA_BASE + "/api/tags", timeout=1.5)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None


def call_ollama(history, model_key):
    model = MODELS[model_key]
    messages = [{"role": "system", "content": SYSTEM_LOCAL}] + history[-16:]
    try:
        data = http_json(OLLAMA_BASE + "/api/chat", {
            "model": model["id"],
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.65, "top_p": 0.9, "num_predict": model["max_tokens"],
                "frequency_penalty": 0.4, "presence_penalty": 0.3,
            },
        }, timeout=180)
    except ApiError as e:
        if e.status == 0 and "refused" in str(e.body).lower():
            raise RuntimeError("Ollama non è avviato. Aprilo (icona nella barra) e riprova.")
        if e.status == 404:
            raise RuntimeError(f"Il modello '{model['id']}' non è installato. "
                               f"Scaricalo con:  ollama pull {model['id']}")
        raise
    text = (data.get("message", {}).get("content") or "").strip()
    return text if len(text) > 1 else AI_FAIL


def groq_headers():
    return {"Authorization": "Bearer " + get_groq_key()}


def groq_list_models():
    data = http_json(GROQ_BASE + "/models", headers=groq_headers(), timeout=20)
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))


def pick_groq_model(ids):
    """Sceglie un modello di chat adatto tra quelli disponibili per l'account."""
    skip = ("whisper", "guard", "tts", "orpheus", "playai", "embed", "safeguard")
    chat = [i for i in ids if not any(s in i.lower() for s in skip)]
    for pref in ("llama-3.3-70b", "llama-3.1-70b", "llama-3.1-8b-instant", "llama"):
        for i in chat:
            if pref in i.lower():
                return i
    return chat[0] if chat else None


def call_groq(history):
    if not get_groq_key():
        raise RuntimeError("Manca la chiave Groq: apri 'Chiave API' in alto e incollala.")
    messages = [{"role": "system", "content": SYSTEM_ONLINE}] + history[-16:]

    def attempt(model):
        data = http_json(GROQ_BASE + "/chat/completions", {
            "model": model, "messages": messages, "temperature": 0.7, "max_tokens": 700,
        }, headers=groq_headers(), timeout=60)
        text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        return text or AI_FAIL

    model = get_groq_model()
    try:
        return attempt(model)
    except ApiError as e:
        if e.status in (400, 403, 404) and "model" in str(e.body).lower():
            # Il modello non è disponibile per questo account: scelgo da solo uno di quelli che lo sono.
            alt = pick_groq_model(groq_list_models())
            if alt and alt != model:
                _groq_model_override["model"] = alt
                if not HOSTED:
                    save_config(groq_model=alt)
                return attempt(alt)
        raise


# ══════════════════════════════════════════════
#  SESSIONI (una per browser, tramite cookie)
# ══════════════════════════════════════════════
class Session:
    def __init__(self):
        self.mode = "online" if HOSTED else "local"
        self.model = "light"
        self.tts = not HOSTED
        self.wake = False
        self.history = []   # messaggi inviati al modello
        self.log = []       # messaggi mostrati: (ruolo, testo)
        self.notice = ""
        self.test_result = None
        self.ip = "locale"
        self.lock = threading.Lock()


SESSIONS = {}
_sessions_lock = threading.Lock()
WAKE_STATE = {"session": None, "thread": None}

# ── Limiti per il sito pubblico: proteggono la tua quota gratuita Groq ──
_rate = {}                      # ip -> orari degli ultimi messaggi
_daily = {"day": "", "count": 0}
_rate_lock = threading.Lock()


def allow_message(ip):
    """Ritorna None se il messaggio è consentito, altrimenti il testo da mostrare."""
    if not HOSTED:
        return None
    import time
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _rate_lock:
        if _daily["day"] != today:
            _daily["day"], _daily["count"] = today, 0
        if _daily["count"] >= DAILY_LIMIT:
            return "SLAW ha raggiunto il limite di messaggi di oggi. Riprova domani."
        recent = [t for t in _rate.get(ip, []) if now - t < 600]
        if len(recent) >= RATE_LIMIT:
            return "Hai inviato molti messaggi in poco tempo. Aspetta qualche minuto e riprova."
        recent.append(now)
        _rate[ip] = recent
        _daily["count"] += 1
        if len(_rate) > 20000:
            _rate.clear()
    return None


def ask(session, text):
    """Invia un messaggio al modello scelto e registra la risposta."""
    text = text.strip()[:2000]
    if not text:
        return
    with session.lock:
        session.log.append(("user", text))
        blocked = allow_message(session.ip)
        if blocked:
            session.log.append(("ai", blocked))
            return
        session.history.append({"role": "user", "content": text})
        try:
            if session.mode == "online":
                reply = call_groq(session.history)
            else:
                reply = call_ollama(session.history, session.model)
            session.history.append({"role": "assistant", "content": reply})
            session.log.append(("ai", reply))
            if session.tts:
                speak(reply)
        except Exception as e:
            session.history.pop()
            if HOSTED:
                print(f"[SLAW] errore: {e}", flush=True)  # il dettaglio resta nei log del server, non al pubblico
                session.log.append(("ai", AI_FAIL))
            else:
                session.log.append(("ai", f"{AI_FAIL} (dettaglio tecnico: {e})"))
        del session.log[:-80]
        del session.history[:-40]


# ── "Hey SLAW": ascolto continuo sul computer che esegue il server ──
def wake_loop(session):
    while session.wake and MIC_AVAILABLE:
        heard = listen_once(timeout=5, phrase_limit=8)
        if not heard:
            continue
        low = heard.lower()
        found = next((p for p in WAKE_PHRASES if p in low), None)
        if not found:
            continue
        rest = low.split(found, 1)[1].strip()
        if len(rest) <= 1:
            rest = (listen_once(timeout=7, phrase_limit=12) or "").strip()
        if rest:
            ask(session, rest)


def set_wake(session, on):
    session.wake = on
    if on and MIC_AVAILABLE:
        t = WAKE_STATE.get("thread")
        if not (t and t.is_alive()):
            WAKE_STATE["session"] = session
            t = threading.Thread(target=wake_loop, args=(session,), daemon=True)
            WAKE_STATE["thread"] = t
            t.start()


# ══════════════════════════════════════════════
#  HTML
# ══════════════════════════════════════════════
def esc(s):
    return html.escape(str(s), quote=True)


FACE_SVG = """<svg viewBox="0 0 40 40" width="100%" height="100%" aria-hidden="true">
<ellipse class="face-ring" cx="20" cy="20" rx="18" ry="6.5" fill="none" stroke-width="1.2" transform="rotate(-18 20 20)"/>
<circle class="face-body" cx="20" cy="20" r="13"/>
<circle class="face-eye" cx="15.5" cy="18" r="1.9"/>
<circle class="face-eye" cx="24.5" cy="18" r="1.9"/>
<path class="face-smile" d="M14.5,23.5 Q20,28 25.5,23.5" stroke-width="1.6"/>
</svg>"""


def render_page(session, is_mobile):
    local = session.mode == "local" and not HOSTED
    installed = ollama_installed_models() if local else None

    # ── chat ──
    if session.log:
        bubbles = "".join(
            f'<div class="row {"user" if role == "user" else "ai"}"><div class="bubble">{esc(text)}</div></div>'
            for role, text in session.log
        )
    else:
        bubbles = f"""<div id="empty-hint"><div class="mark">{FACE_SVG}</div>
<div class="intro-bubble">Ciao, sono SLAW — il tuo assistente personale. Scrivi qui sotto per iniziare{
    '; se è la prima volta, scegli un modello da "Modello" in alto.' if local else '.'}</div></div>"""

    # ── pannello modelli (solo CSS :target) ──
    if HOSTED:
        installed = None
    if installed is None:
        ollama_status = '<p class="warn">Ollama non risulta avviato su questo computer. Installalo da ollama.com e lascialo aperto.</p>'
    else:
        ollama_status = '<p class="okmsg">Ollama è attivo.</p>'
    opts = []
    for key, m in MODELS.items():
        badge = ""
        if key == "ultra" and is_mobile:
            badge = '<span class="badge">CONSIGLIATO SU MOBILE</span>'
        state = ""
        if installed is not None:
            has = m["id"] in installed
            state = ('<span class="badge ok">INSTALLATO</span>' if has
                     else f'<span class="hint">per scaricarlo: <code>ollama pull {esc(m["id"])}</code></span>')
        sel = " selected" if key == session.model else ""
        opts.append(f"""<form method="post" action="/model"><button class="model-opt{sel}" name="model" value="{key}">
<span class="name">{esc(m['label'])} {badge}</span><span class="desc">{esc(m['desc'])}</span>{state}</button></form>""")
    model_panel = f"""<div class="overlay" id="modelli"><div class="card">
<h2>Scegli il modello</h2>
<p class="sub">Gira sul tuo computer tramite Ollama: nessun account, nessuna chiave, funziona offline.</p>
{ollama_status}{''.join(opts)}<a class="close" href="#fine">Chiudi</a></div></div>"""

    # ── pannello chiave Groq ──
    if session.test_result is None:
        test_html = ""
    elif isinstance(session.test_result, str):
        test_html = f'<p class="warn">{esc(session.test_result)}</p>'
    else:
        current = get_groq_model()
        lis = "".join(
            f'<form method="post" action="/groqmodel"><button class="model-pick{" selected" if i == current else ""}" '
            f'name="groqmodel" value="{esc(i)}">{esc(i)}</button></form>'
            for i in session.test_result
        )
        test_html = f'<p class="sub">Modelli disponibili per questa chiave (tocca per sceglierne uno):</p><div class="picklist">{lis}</div>'
    has_key = bool(get_groq_key())
    key_panel = f"""<div class="overlay" id="chiave"><div class="card">
<h2>Modalità Online (Groq)</h2>
<p class="sub">Usa un'IA vera via internet, gratis. Crea una chiave su <b>console.groq.com/keys</b> (nessuna carta) e incollala qui.
{'La chiave è già impostata.' if has_key else ''}</p>
<form method="post" action="/key"><input class="textfield" type="password" name="key" placeholder="gsk_..." autocomplete="off">
<button class="btn primary" type="submit">Salva e usa modalità Online</button></form>
<form method="post" action="/testkey"><button class="btn" type="submit">Verifica modelli disponibili</button></form>
{test_html}<a class="close" href="#fine">Chiudi</a></div></div>"""

    notice = f'<div class="notice">{esc(session.notice)}</div>' if session.notice else ""
    session.notice = ""
    refresh = '<meta http-equiv="refresh" content="4;url=/#fine">' if session.wake and MIC_AVAILABLE and not HOSTED else ""

    voice_cls = "hdr-btn active" if session.tts else "hdr-btn"
    voice_lbl = "Voce" if session.tts else "Muto"
    wake_cls = "hdr-btn active" if session.wake else "hdr-btn"
    wake_lbl = "Hey SLAW ●" if session.wake else "Hey SLAW"
    voice_dis = "" if TTS_AVAILABLE else ' disabled title="Servono i pacchetti di requirements-voce.txt"'
    mic_dis = "" if MIC_AVAILABLE else ' disabled title="Servono i pacchetti di requirements-voce.txt"'
    model_link = ('<a class="hdr-btn" href="#modelli">Modello</a>' if local
                  else '<a class="hdr-btn" href="#chiave">Chiave API</a>')

    panels = "" if HOSTED else model_panel + key_panel
    if HOSTED:
        controls = '<a class="hdr-btn" href="/#download">Scarica</a>'
        switch = ""
        mic_btn = ""
    else:
        controls = f"""<form method="post" action="/wake"><button class="{wake_cls}"{mic_dis}>{wake_lbl}</button></form>
  <form method="post" action="/voce"><button class="{voice_cls}"{voice_dis}>{voice_lbl}</button></form>
  {model_link}"""
        switch = f"""<form class="mode-switch" method="post" action="/mode">
    <button class="mode-btn{' active' if local else ''}" name="mode" value="local">Locale</button>
    <button class="mode-btn{'' if local else ' active'}" name="mode" value="online">Online</button>
  </form>"""
        mic_btn = f'<button class="mic-btn" type="submit" formaction="/mic" formnovalidate title="Parla (usa il microfono del computer)"{mic_dis}>●</button>'
    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>SLAW</title><link rel="stylesheet" href="/style.css"></head>
<body>
<header>
  <div class="brand"><div class="brand-mark">{FACE_SVG}</div>
    <div class="brand-text"><h1>SLAW</h1>{'<div class="offline-tag">OFFLINE</div>' if local else ''}</div></div>
  {switch}
  {controls}
  <form method="post" action="/clear"><button class="hdr-btn">Pulisci</button></form>
</header>
{notice}
<main id="chat"><div id="feed">{bubbles}</div><div id="fine"></div></main>
<form id="input-bar" method="post" action="/send">
  {mic_btn}
  <input id="msg-in" type="text" name="msg" placeholder="Scrivi un messaggio…" autocomplete="off" autofocus maxlength="2000" required>
  <button id="send-btn" type="submit">↑</button>
</form>
{panels}
</body></html>"""


# ══════════════════════════════════════════════
#  SERVER
# ══════════════════════════════════════════════
MAX_BODY = 20_000


class Handler(BaseHTTPRequestHandler):
    server_version = "SLAW/1.0"

    def log_message(self, fmt, *args):
        pass

    # sessione tramite cookie
    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        if HOSTED and fwd:
            return fwd.split(",")[0].strip()[:64]   # dietro il proxy di Render l'IP vero è il primo
        return self.client_address[0]

    def _session(self):
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        sid = jar["sid"].value if "sid" in jar else None
        with _sessions_lock:
            if not sid or sid not in SESSIONS:
                if len(SESSIONS) >= MAX_SESSIONS:       # evita che la memoria si riempia
                    for old in list(SESSIONS)[: MAX_SESSIONS // 2]:
                        SESSIONS.pop(old, None)
                sid = uuid.uuid4().hex
                SESSIONS[sid] = Session()
            self._new_cookie = sid
            sess = SESSIONS[sid]
        sess.ip = self._client_ip()
        return sess

    def _cookie_header(self):
        self.send_header("Set-Cookie", f"sid={self._new_cookie}; Path=/; HttpOnly; SameSite=Lax")

    def _redirect(self, where="#fine"):
        home = APP if HOSTED else "/"
        self.send_response(303)
        self.send_header("Location", home + where)
        self._cookie_header()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, body, ctype, status=200, cookie=True):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self._cookie_header()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/style.css":
            self._new_cookie = ""
            self._send((BASE / "style.css").read_text(encoding="utf-8"), "text/css; charset=utf-8", cookie=False)
        elif path == "/healthz":
            self._new_cookie = ""
            self._send("ok", "text/plain; charset=utf-8", cookie=False)
        elif HOSTED and path in ("/", "/index.html"):
            self._new_cookie = ""
            self._send((BASE / "site" / "index.html").read_bytes(), "text/html; charset=utf-8", cookie=False)
        elif path.startswith("/download/"):
            self._new_cookie = ""
            name = path[len("/download/"):]
            f = BASE / "site" / "download" / name
            if name in ("slaw-windows.zip", "slaw-mac.zip") and f.is_file():
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send("File non trovato", "text/plain; charset=utf-8", 404, cookie=False)
        elif path == "/chat" or (path == "/" and not HOSTED):
            session = self._session()
            ua = self.headers.get("User-Agent", "")
            mobile = any(k in ua for k in ("Android", "iPhone", "iPad", "Mobile"))
            self._send(render_page(session, mobile), "text/html; charset=utf-8")
        else:
            self._new_cookie = ""
            self._send("Non trovato", "text/plain; charset=utf-8", 404, cookie=False)

    def do_POST(self):
        session = self._session()
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        field = lambda name: (form.get(name) or [""])[0]
        path = urllib.parse.urlparse(self.path).path

        # Sito pubblico: i visitatori possono solo scrivere e pulire la chat. Tutto il resto è bloccato.
        if HOSTED and path not in ("/send", "/clear"):
            return self._redirect("#fine")

        if path == "/send":
            ask(session, field("msg"))

        elif path == "/mic":
            if not MIC_AVAILABLE:
                session.notice = "Microfono non disponibile: installa i pacchetti di requirements-voce.txt."
            else:
                heard = listen_once()
                if heard:
                    ask(session, heard)
                else:
                    session.notice = "Non ho sentito nulla, riprova."

        elif path == "/mode":
            m = field("mode")
            if m == "online" and not get_groq_key():
                session.notice = "Per la modalità Online serve una chiave Groq gratuita."
                return self._redirect("#chiave")
            if m in ("local", "online"):
                session.mode = m

        elif path == "/model":
            if field("model") in MODELS:
                session.model = field("model")
            return self._redirect("#fine")

        elif path == "/key":
            key = field("key").strip()
            if key:
                save_config(groq_key=key, groq_model=GROQ_DEFAULT_MODEL)
                session.mode = "online"
                session.test_result = None
            else:
                session.notice = "Incolla prima la chiave API."

        elif path == "/testkey":
            try:
                session.test_result = groq_list_models() or "Nessun modello trovato per questa chiave."
            except Exception as e:
                session.test_result = f"Errore: {e}"
            return self._redirect("#chiave")

        elif path == "/groqmodel":
            if field("groqmodel"):
                save_config(groq_model=field("groqmodel"))
                session.notice = f"Modello Online impostato: {field('groqmodel')}"

        elif path == "/voce":
            if TTS_AVAILABLE:
                session.tts = not session.tts

        elif path == "/wake":
            if MIC_AVAILABLE:
                set_wake(session, not session.wake)
            else:
                session.notice = "Hey SLAW non è disponibile: installa i pacchetti di requirements-voce.txt."

        elif path == "/clear":
            session.history.clear()
            session.log.clear()

        self._redirect()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "IP-DEL-COMPUTER"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="SLAW — chat IA in Python + HTML + CSS")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000))
    ap.add_argument("--lan", action="store_true",
                    help="rendi SLAW raggiungibile dagli altri dispositivi sulla tua rete (es. il telefono)")
    args = ap.parse_args()

    host = "0.0.0.0" if (args.lan or HOSTED) else "127.0.0.1"
    srv = ThreadingHTTPServer((host, args.port), Handler)
    print("=" * 52)
    print("  SLAW avviato")
    if HOSTED:
        print(f"  Modalità pubblica sulla porta {args.port} (chiave Groq: {'presente' if get_groq_key() else 'MANCANTE'})")
    print(f"  Su questo computer:  http://localhost:{args.port}")
    if args.lan:
        print(f"  Dal telefono (stessa Wi-Fi):  http://{lan_ip()}:{args.port}")
        print("  ATTENZIONE: chiunque sia sulla tua rete può usare SLAW")
        print("  (e la tua chiave Groq, se l'hai impostata).")
    print(f"  Voce: {'sì' if TTS_AVAILABLE else 'no'}   Microfono: {'sì' if MIC_AVAILABLE else 'no'}")
    print("  Per fermarlo: Ctrl+C")
    print("=" * 52)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nSLAW fermato.")


if __name__ == "__main__":
    main()
