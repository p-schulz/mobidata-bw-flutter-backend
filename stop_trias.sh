#!/bin/bash

SESSION="triasproxy"

if screen -list | grep -q "\.${SESSION}"; then
    echo "Beende TRIAS-Proxy..."
    screen -S ${SESSION} -X quit
    echo "Gestoppt."
else
    echo "Screen '${SESSION}' läuft nicht."
fi
