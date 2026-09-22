#!/usr/bin/env bash
#
# Automated setup for the mobidata-bw-flutter-backend TRIAS proxy.
#
# Installs system + Python dependencies, creates trias-proxy/.env (with a
# generated API key) if missing, optionally installs the GTFS/locations
# SQLite databases, and installs the app as a systemd service (with an
# optional nginx reverse proxy in front of it).
#
# Usage:
#   ./setup.sh [options]
#
# Options:
#   --app-user USER       System user the service runs as (default: current user)
#   --port PORT            Port uvicorn listens on (default: 8080)
#   --gtfs-db PATH          Copy this file to trias-proxy/gtfs_seed.sqlite
#   --locations-db PATH     Copy this file to trias-proxy/locations.sqlite
#   --domain DOMAIN         Install an nginx reverse proxy for this hostname
#   --skip-apt              Skip system package installation
#   --skip-systemd          Skip systemd service installation
#   --skip-nginx            Skip nginx installation/config even if --domain is given
#   --no-start              Install the systemd service but don't start it now
#   -h, --help              Show this help
#
# Safe to re-run: every step checks current state before changing anything.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
APP_DIR="$REPO_ROOT/trias-proxy"
DEPLOY_DIR="$REPO_ROOT/deploy"

APP_USER="${SUDO_USER:-$(id -un)}"
PORT="8080"
GTFS_DB_SRC=""
LOCATIONS_DB_SRC=""
DOMAIN=""
SKIP_APT="false"
SKIP_SYSTEMD="false"
SKIP_NGINX="false"
START_SERVICE="true"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

print_help() {
    sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --app-user) APP_USER="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --gtfs-db) GTFS_DB_SRC="$2"; shift 2 ;;
        --locations-db) LOCATIONS_DB_SRC="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        --skip-apt) SKIP_APT="true"; shift ;;
        --skip-systemd) SKIP_SYSTEMD="true"; shift ;;
        --skip-nginx) SKIP_NGINX="true"; shift ;;
        --no-start) START_SERVICE="false"; shift ;;
        -h|--help) print_help; exit 0 ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac
done

if [ "$EUID" -eq 0 ]; then
    SUDO=""
else
    command -v sudo >/dev/null 2>&1 || die "This script needs root privileges for package/service installs — install sudo or re-run as root."
    SUDO="sudo"
fi

# ---------------------------------------------------------------------------
log "System packages"
# ---------------------------------------------------------------------------
if [ "$SKIP_APT" = "true" ]; then
    echo "Skipped (--skip-apt)."
elif ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found — skipping system package install. Ensure python3, python3-venv, python3-pip, sqlite3, unzip, curl (and nginx, if used) are installed."
else
    PKGS="python3 python3-venv python3-pip sqlite3 unzip curl"
    if [ -n "$DOMAIN" ] && [ "$SKIP_NGINX" != "true" ]; then
        PKGS="$PKGS nginx"
    fi
    $SUDO apt-get update
    # shellcheck disable=SC2086
    $SUDO apt-get install -y $PKGS
fi

# ---------------------------------------------------------------------------
log "Python virtual environment"
# ---------------------------------------------------------------------------
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
    echo "Created $APP_DIR/venv"
else
    echo "$APP_DIR/venv already exists, reusing it."
fi

"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------------------
log ".env configuration"
# ---------------------------------------------------------------------------
if [ -f "$APP_DIR/.env" ]; then
    echo "$APP_DIR/.env already exists, leaving it untouched."
else
    API_KEY="$("$APP_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sed \
        -e "s#^TRIAS_PROXY_API_KEY=.*#TRIAS_PROXY_API_KEY=${API_KEY}#" \
        -e "s#^GTFS_DB_PATH=.*#GTFS_DB_PATH=${APP_DIR}/gtfs_seed.sqlite#" \
        -e "s#^LOCATIONS_DB_PATH=.*#LOCATIONS_DB_PATH=${APP_DIR}/locations.sqlite#" \
        "$APP_DIR/.env.example" > "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "Created $APP_DIR/.env with a generated TRIAS_PROXY_API_KEY."
fi

# ---------------------------------------------------------------------------
log "GTFS / locations databases"
# ---------------------------------------------------------------------------
if [ -n "$GTFS_DB_SRC" ]; then
    [ -f "$GTFS_DB_SRC" ] || die "--gtfs-db file not found: $GTFS_DB_SRC"
    cp "$GTFS_DB_SRC" "$APP_DIR/gtfs_seed.sqlite"
    echo "Copied $GTFS_DB_SRC -> $APP_DIR/gtfs_seed.sqlite"
elif [ -f "$APP_DIR/gtfs_seed.sqlite" ]; then
    echo "$APP_DIR/gtfs_seed.sqlite already present."
else
    warn "No GTFS DB at $APP_DIR/gtfs_seed.sqlite. Pass --gtfs-db <path> or generate one with generate_gtfs_seed.py before starting the service."
fi

if [ -n "$LOCATIONS_DB_SRC" ]; then
    [ -f "$LOCATIONS_DB_SRC" ] || die "--locations-db file not found: $LOCATIONS_DB_SRC"
    cp "$LOCATIONS_DB_SRC" "$APP_DIR/locations.sqlite"
    echo "Copied $LOCATIONS_DB_SRC -> $APP_DIR/locations.sqlite"
elif [ -f "$APP_DIR/locations.sqlite" ]; then
    echo "$APP_DIR/locations.sqlite already present."
else
    warn "No locations DB at $APP_DIR/locations.sqlite. Pass --locations-db <path> or generate one with build_locations_from_lgl.py before starting the service."
fi

# ---------------------------------------------------------------------------
log "systemd service"
# ---------------------------------------------------------------------------
if [ "$SKIP_SYSTEMD" = "true" ]; then
    echo "Skipped (--skip-systemd)."
elif ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found — skipping systemd install. Run uvicorn manually or via start_triasproxy.sh instead."
else
    UNIT_PATH="/etc/systemd/system/trias-proxy.service"
    sed \
        -e "s#__APP_DIR__#${APP_DIR}#g" \
        -e "s#__APP_USER__#${APP_USER}#g" \
        -e "s#__PORT__#${PORT}#g" \
        "$DEPLOY_DIR/trias-proxy.service" | $SUDO tee "$UNIT_PATH" >/dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable trias-proxy
    echo "Installed and enabled $UNIT_PATH (user: $APP_USER, port: $PORT)."

    if [ "$START_SERVICE" = "true" ]; then
        $SUDO systemctl restart trias-proxy
        sleep 1
        $SUDO systemctl --no-pager status trias-proxy || true
    else
        echo "Not starting service now (--no-start). Start later with: sudo systemctl start trias-proxy"
    fi
fi

# ---------------------------------------------------------------------------
log "nginx reverse proxy"
# ---------------------------------------------------------------------------
if [ -z "$DOMAIN" ]; then
    echo "Skipped (no --domain given). The service is reachable directly on port $PORT."
elif [ "$SKIP_NGINX" = "true" ]; then
    echo "Skipped (--skip-nginx)."
elif ! command -v nginx >/dev/null 2>&1; then
    warn "nginx not found — skipping reverse proxy config."
else
    SITE_AVAILABLE="/etc/nginx/sites-available/trias-proxy"
    SITE_ENABLED="/etc/nginx/sites-enabled/trias-proxy"
    sed \
        -e "s#__DOMAIN__#${DOMAIN}#g" \
        -e "s#__PORT__#${PORT}#g" \
        "$DEPLOY_DIR/nginx-trias-proxy.conf" | $SUDO tee "$SITE_AVAILABLE" >/dev/null
    $SUDO ln -sf "$SITE_AVAILABLE" "$SITE_ENABLED"
    $SUDO nginx -t
    $SUDO systemctl reload nginx
    echo "Installed nginx site for $DOMAIN -> 127.0.0.1:$PORT."
    echo "For TLS, run: sudo certbot --nginx -d $DOMAIN"
fi

# ---------------------------------------------------------------------------
log "Done"
# ---------------------------------------------------------------------------
cat <<EOF
Service:   sudo systemctl {start|stop|restart|status} trias-proxy
Logs:      journalctl -u trias-proxy -f
Config:    $APP_DIR/.env
Health:    curl -H "x-api-key: \$(grep TRIAS_PROXY_API_KEY $APP_DIR/.env | cut -d= -f2)" http://127.0.0.1:$PORT/health
EOF
