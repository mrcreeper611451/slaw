#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 &> /dev/null; then
    echo "[ERRORE] Python non trovato. Installalo da https://python.org/downloads"
    exit 1
fi
echo "Apro SLAW: http://localhost:8000  (per il telefono: python3 server.py --lan)"
( sleep 1; (open http://localhost:8000 || xdg-open http://localhost:8000) >/dev/null 2>&1 ) &
python3 server.py
