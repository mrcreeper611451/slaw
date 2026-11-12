# SLAW online — guida alla pubblicazione

Risultato: un link tipo `https://slaw.onrender.com` che chiunque apre da telefono o computer, senza installare nulla e senza account. Dalla stessa pagina si può anche scaricare la versione offline (Windows/Mac).

## Cosa ti serve (tutto gratis)
- un account **GitHub** (github.com)
- un account **Render** (render.com — puoi entrare con GitHub)
- una chiave **Groq** (console.groq.com/keys). Se la vecchia è stata incollata in chat o condivisa, **cancellala e creane una nuova**.

## Passi
1. **GitHub** → New repository → nome `slaw` → Create. Poi "uploading an existing file" e trascina **tutti i file di questa cartella** (server.py, style.css, render.yaml, requirements.txt, la cartella `site`, ecc.) → Commit.
2. **Render** → New → **Blueprint** (oppure Web Service) → scegli il repository `slaw`. Se usi Web Service: Runtime *Python*, Build Command `echo ok`, Start Command `python server.py`, piano *Free*.
3. Nelle **Environment variables** aggiungi `GROQ_API_KEY` = la tua chiave `gsk_...`.
4. **Deploy**. Quando è "Live" ti dà il link. Aprilo: vedi la pagina SLAW; "Usa SLAW online" apre la chat.
5. Vuoi un nome più bello? Su Render puoi cambiare il nome del servizio (cambia la parte prima di `.onrender.com`) oppure collegare un tuo dominio.

## Come è protetto
- La chiave Groq sta solo su Render (variabile d'ambiente): non compare mai nella pagina né nei file.
- I visitatori possono solo scrivere e pulire la chat: cambio chiave, modello, modalità, voce e microfono sono bloccati sul server.
- Limiti: `SLAW_RATE_LIMIT` (messaggi per persona ogni 10 minuti, default 20) e `SLAW_DAILY_LIMIT` (messaggi totali al giorno, default 1500). Cambiali dalle variabili d'ambiente di Render.
- Gli errori tecnici non vengono mostrati al pubblico: restano nei log di Render.

## Limiti da sapere
- La quota gratuita di Groq è **condivisa da tutti i visitatori**: se il sito diventa molto usato, finita la quota SLAW risponderà con un errore generico finché non si ricarica.
- Sul piano gratuito di Render il servizio può addormentarsi quando nessuno lo usa: la prima apertura dopo una pausa può impiegare circa un minuto.
- Le conversazioni stanno in memoria: se il server si riavvia, si perdono.
- Sul sito non ci sono voce, microfono e "Hey SLAW" (servono JavaScript o un computer locale): ci sono nella versione scaricabile.
- Il pacchetto scaricabile richiede comunque Python (e Ollama per la modalità Locale). Non è un programma `.exe`: per quello serve compilarlo su un vero Windows.

## Provare tutto sul tuo PC prima di pubblicare
```
set SLAW_HOSTED=1
set GROQ_API_KEY=gsk_...
python server.py
```
(su Mac/Linux: `SLAW_HOSTED=1 GROQ_API_KEY=gsk_... python3 server.py`) e apri http://localhost:8000
