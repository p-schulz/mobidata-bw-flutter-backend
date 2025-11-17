#!/bin/bash

# config for screen session and app
SESSION="triasproxy"
APP_DIR="/home/trias/trias-proxy"
APP="main:app"

# check if the screen session is already running
if screen -list | grep -q "\.${SESSION}"; then
    echo "Der Screen '${SESSION}' läuft bereits."
    exit 0
fi

echo "Starte TRIAS-Proxy in Screen-Session '${SESSION}'..."

# start screen session and run the app
screen -dmS ${SESSION} bash -c "
    cd ${APP_DIR}
    source venv/bin/activate
    export \$(grep -v '^#' .env | xargs)
    uvicorn ${APP} --host 0.0.0.0 --port 8080 --workers 1
"

echo "Proxy läuft nun in Screen-Session '${SESSION}'."
echo "Verbinden mit:  screen -r ${SESSION}"
