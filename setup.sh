#!/usr/bin/env bash
#
# Complete, automated setup for the mobidata-bw-flutter-backend TRIAS proxy.
#
# Installs system + Python dependencies, creates trias-proxy/.env (with a
# generated API key) if missing, builds the GTFS seed and locations SQLite
# databases (via generate_gtfs_seed.py / build_locations_from_lgl.py) and
# moves them into place, installs the app as a systemd service, and puts an
# nginx reverse proxy in front of it (using --domain if given, otherwise the
# server's own detected IP address as the hostname). Also installs the
# PMTiles server (go-pmtiles) as a second systemd service for vector map tiles
# and, if --tiles-source is given, extracts a regional .pmtiles file from it.
# With --web-build, deploys the Flutter web client, which nginx serves at /
# next to the API at /api/, the tiles at /tiles/ and the styles at /styles/.
#
# Usage:
#   ./setup.sh [options]
#
# Options:
#   --app-user USER       System user the service runs as (default: current user)
#   --port PORT             Port uvicorn listens on (default: 8080)
#   --api-interface ADDR    Address uvicorn binds to (default: 0.0.0.0). Use
#                           127.0.0.1 to reach the API only through nginx at
#                           /api/ — but app builds that still call
#                           http://<host>:8080 directly then stop working.
#   --gtfs-db PATH          Use this pre-built file instead of running
#                           generate_gtfs_seed.py (copied to trias-proxy/gtfs_seed.sqlite)
#   --skip-gtfs-build       Don't run generate_gtfs_seed.py if no GTFS DB is present
#   --locations-db PATH     Use this pre-built file instead of running
#                           build_locations_from_lgl.py (copied to trias-proxy/locations.sqlite)
#   --lgl-shp PATH          Path to the LGL-BW v_al_gemeinde.shp shapefile;
#                           if given, runs build_locations_from_lgl.py to build
#                           trias-proxy/locations.sqlite from it
#   --domain DOMAIN         Hostname for the nginx reverse proxy (default: the
#                           server's own detected IP address)
#   --https                 Generate https:// URLs (tile URL in the map styles,
#                           pmtiles public URL). Use once TLS is set up with
#                           certbot, otherwise browsers block the http:// tile
#                           URLs as mixed content.
#   --web-build DIR         Flutter web build to deploy (the output of
#                           `flutter build web`, containing index.html)
#   --web-dir DIR           Directory nginx serves the web client from
#                           (default: /var/www/mobility4bw)
#   --skip-apt              Skip system package installation
#   --skip-systemd          Skip systemd service installation
#   --skip-nginx            Skip nginx installation/config entirely
#   --tiles-source SRC      Source archive for `pmtiles extract`: a local path
#                           or remote URL of a (planet) .pmtiles file. Without
#                           it, no tile file is created (a warning is printed).
#   --tiles-bbox BBOX       min_lon,min_lat,max_lon,max_lat to extract
#                           (default: 7.4,47.4,10.6,49.9 = Baden-Wuerttemberg)
#   --tiles-name NAME       Region name; the file becomes NAME.pmtiles and is
#                           served as /NAME/{z}/{x}/{y}.mvt (default: bw)
#   --tiles-dir DIR         Directory holding .pmtiles files (default: /var/www/maps)
#   --tiles-port PORT       Port of the tile server (default: 8083; 8080 is the API)
#   --skip-tiles            Skip the tile server (binary, service, extract)
#   --no-start              Install the systemd services but don't start them now
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
API_INTERFACE="0.0.0.0"
SCHEME="http"
WEB_BUILD=""
WEB_DIR="/var/www/mobility4bw"
GTFS_DB_SRC=""
LOCATIONS_DB_SRC=""
LGL_SHP=""
DOMAIN=""
SKIP_APT="false"
SKIP_SYSTEMD="false"
SKIP_NGINX="false"
SKIP_GTFS_BUILD="false"
START_SERVICE="true"

PMTILES_VERSION="1.31.2"
PMTILES_URL="https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/go-pmtiles_${PMTILES_VERSION}_Linux_x86_64.tar.gz"
# The release publishes no checksum file, so this pins the SHA-256 of the
# v1.31.2 Linux x86_64 tarball as downloaded from the official release URL.
PMTILES_SHA256="3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f"
PMTILES_BIN="/usr/local/bin/pmtiles"
TILES_SOURCE=""
TILES_BBOX="7.4,47.4,10.6,49.9"
TILES_NAME="bw"
TILES_DIR="/var/www/maps"
TILES_PORT="8083"
STYLES_DIR="/var/www/styles"
SKIP_TILES="false"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

print_help() {
    sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --app-user) APP_USER="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --api-interface) API_INTERFACE="$2"; shift 2 ;;
        --https) SCHEME="https"; shift ;;
        --web-build) WEB_BUILD="$2"; shift 2 ;;
        --web-dir) WEB_DIR="$2"; shift 2 ;;
        --gtfs-db) GTFS_DB_SRC="$2"; shift 2 ;;
        --skip-gtfs-build) SKIP_GTFS_BUILD="true"; shift ;;
        --locations-db) LOCATIONS_DB_SRC="$2"; shift 2 ;;
        --lgl-shp) LGL_SHP="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        --skip-apt) SKIP_APT="true"; shift ;;
        --skip-systemd) SKIP_SYSTEMD="true"; shift ;;
        --skip-nginx) SKIP_NGINX="true"; shift ;;
        --tiles-source) TILES_SOURCE="$2"; shift 2 ;;
        --tiles-bbox) TILES_BBOX="$2"; shift 2 ;;
        --tiles-name) TILES_NAME="$2"; shift 2 ;;
        --tiles-dir) TILES_DIR="$2"; shift 2 ;;
        --tiles-port) TILES_PORT="$2"; shift 2 ;;
        --skip-tiles) SKIP_TILES="true"; shift ;;
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

# Detect the server's primary IP (routing-table lookup only, no network
# traffic and no external service) for use as the nginx hostname when
# --domain isn't given.
detect_host_ip() {
    local ip=""
    if command -v ip >/dev/null 2>&1; then
        ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    fi
    if [ -z "$ip" ] && command -v hostname >/dev/null 2>&1; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    echo "$ip"
}

if [ -z "$DOMAIN" ] && [ "$SKIP_NGINX" != "true" ]; then
    DOMAIN="$(detect_host_ip)"
    [ -n "$DOMAIN" ] && echo "No --domain given, using detected host IP: $DOMAIN"
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
    if [ -n "$LGL_SHP" ]; then
        PKGS="$PKGS gdal-bin libgdal-dev"
    fi
    if [ -n "$WEB_BUILD" ]; then
        PKGS="$PKGS rsync"
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
log "GTFS seed database"
# ---------------------------------------------------------------------------
if [ -n "$GTFS_DB_SRC" ]; then
    [ -f "$GTFS_DB_SRC" ] || die "--gtfs-db file not found: $GTFS_DB_SRC"
    cp "$GTFS_DB_SRC" "$APP_DIR/gtfs_seed.sqlite"
    echo "Copied $GTFS_DB_SRC -> $APP_DIR/gtfs_seed.sqlite"
elif [ -f "$APP_DIR/gtfs_seed.sqlite" ]; then
    echo "$APP_DIR/gtfs_seed.sqlite already present, leaving it untouched."
elif [ "$SKIP_GTFS_BUILD" = "true" ]; then
    warn "No GTFS DB at $APP_DIR/gtfs_seed.sqlite and --skip-gtfs-build given. Run generate_gtfs_seed.py manually before starting the service."
else
    echo "Running generate_gtfs_seed.py (downloads the current GTFS ZIP)…"
    ( cd "$REPO_ROOT" && "$APP_DIR/venv/bin/python" generate_gtfs_seed.py )
    [ -f "$REPO_ROOT/assets/gtfs/gtfs_seed.sqlite" ] || die "generate_gtfs_seed.py did not produce assets/gtfs/gtfs_seed.sqlite"
    mv -f "$REPO_ROOT/assets/gtfs/gtfs_seed.sqlite" "$APP_DIR/gtfs_seed.sqlite"
    rmdir "$REPO_ROOT/assets/gtfs" "$REPO_ROOT/assets" 2>/dev/null || true
    echo "Built and moved $APP_DIR/gtfs_seed.sqlite"
fi

# ---------------------------------------------------------------------------
log "Locations (geocoder) database"
# ---------------------------------------------------------------------------
if [ -n "$LOCATIONS_DB_SRC" ]; then
    [ -f "$LOCATIONS_DB_SRC" ] || die "--locations-db file not found: $LOCATIONS_DB_SRC"
    cp "$LOCATIONS_DB_SRC" "$APP_DIR/locations.sqlite"
    echo "Copied $LOCATIONS_DB_SRC -> $APP_DIR/locations.sqlite"
elif [ -f "$APP_DIR/locations.sqlite" ]; then
    echo "$APP_DIR/locations.sqlite already present, leaving it untouched."
elif [ -n "$LGL_SHP" ]; then
    [ -f "$LGL_SHP" ] || die "--lgl-shp file not found: $LGL_SHP"
    echo "Running build_locations_from_lgl.py against $LGL_SHP…"
    "$APP_DIR/venv/bin/pip" install geopandas pandas
    ( cd "$REPO_ROOT" && LGL_SHP_PATH="$LGL_SHP" "$APP_DIR/venv/bin/python" build_locations_from_lgl.py )
    [ -f "$REPO_ROOT/locations.sqlite" ] || die "build_locations_from_lgl.py did not produce locations.sqlite"
    mv -f "$REPO_ROOT/locations.sqlite" "$APP_DIR/locations.sqlite"
    echo "Built and moved $APP_DIR/locations.sqlite"
else
    warn "No locations DB at $APP_DIR/locations.sqlite. Pass --locations-db <path>, or --lgl-shp <path to v_al_gemeinde.shp> to build one (this source shapefile isn't auto-downloadable, it must be obtained from LGL-BW separately)."
fi

# ---------------------------------------------------------------------------
log "Vector tiles (PMTiles server)"
# ---------------------------------------------------------------------------
[ "$TILES_PORT" != "$PORT" ] || die "--tiles-port must differ from --port ($PORT is the API)."

USE_NGINX="false"
if [ "$SKIP_NGINX" != "true" ] && [ -n "$DOMAIN" ] && command -v nginx >/dev/null 2>&1; then
    USE_NGINX="true"
fi

if [ "$SKIP_TILES" != "true" ] && [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
    warn "The pinned go-pmtiles release is Linux x86_64 only (this is $(uname -s)-$(uname -m)) — skipping the tile server."
    SKIP_TILES="true"
fi

if [ "$SKIP_TILES" = "true" ]; then
    echo "Skipped."
else
    INSTALLED_VERSION=""
    [ -x "$PMTILES_BIN" ] && INSTALLED_VERSION="$("$PMTILES_BIN" version 2>/dev/null || true)"
    case "$INSTALLED_VERSION" in
        "pmtiles ${PMTILES_VERSION},"*) ALREADY_INSTALLED="true" ;;
        *) ALREADY_INSTALLED="false" ;;
    esac
    if [ "$ALREADY_INSTALLED" = "true" ]; then
        echo "pmtiles ${PMTILES_VERSION} already installed at $PMTILES_BIN."
    else
        TMP_DL="$(mktemp -d)"
        echo "Downloading go-pmtiles ${PMTILES_VERSION}…"
        curl -fSL --retry 3 -o "$TMP_DL/pmtiles.tar.gz" "$PMTILES_URL"
        if ! echo "${PMTILES_SHA256}  $TMP_DL/pmtiles.tar.gz" | sha256sum -c - >/dev/null 2>&1; then
            rm -rf "$TMP_DL"
            die "SHA-256 mismatch for the downloaded go-pmtiles tarball — refusing to install it."
        fi
        tar -xzf "$TMP_DL/pmtiles.tar.gz" -C "$TMP_DL" pmtiles
        $SUDO install -m 755 "$TMP_DL/pmtiles" "$PMTILES_BIN"
        rm -rf "$TMP_DL"
        echo "Installed $PMTILES_BIN"
    fi

    $SUDO install -d -o "$APP_USER" -m 755 "$TILES_DIR"

    TILES_FILE="$TILES_DIR/$TILES_NAME.pmtiles"
    if [ -f "$TILES_FILE" ]; then
        echo "$TILES_FILE already present, leaving it untouched (delete it to re-extract)."
    elif [ -n "$TILES_SOURCE" ]; then
        echo "Extracting bbox $TILES_BBOX from $TILES_SOURCE (this can take a while)…"
        "$PMTILES_BIN" extract "$TILES_SOURCE" "$TILES_FILE.part" --bbox="$TILES_BBOX"
        chmod 644 "$TILES_FILE.part"
        mv -f "$TILES_FILE.part" "$TILES_FILE"
        echo "Wrote $TILES_FILE"
    else
        warn "No $TILES_FILE and no --tiles-source given, so no tiles will be served yet. Re-run with --tiles-source <planet .pmtiles path or URL>, or run: pmtiles extract <source> $TILES_FILE --bbox=$TILES_BBOX"
    fi

    # Map styles (Mapbox style spec) for the client. Unlike the databases these
    # are regenerated on every run, so palette edits in
    # deploy/styles/generate_styles.py take effect on the next setup.sh run.
    TILES_HOST="${DOMAIN:-$(detect_host_ip)}"
    if [ "$USE_NGINX" = "true" ]; then
        TILES_URL="${SCHEME}://${DOMAIN}/tiles/${TILES_NAME}/{z}/{x}/{y}.mvt"
    else
        TILES_URL="http://${TILES_HOST:-<server-ip>}:${TILES_PORT}/${TILES_NAME}/{z}/{x}/{y}.mvt"
    fi
    if [ -z "$TILES_HOST" ]; then
        warn "Could not determine the server's host/IP — skipping map style generation. Re-run with --domain <host>."
    else
        $SUDO install -d -o "$APP_USER" -m 755 "$STYLES_DIR"
        python3 "$DEPLOY_DIR/styles/generate_styles.py" --tiles-url "$TILES_URL" --out "$STYLES_DIR"
        if [ "$USE_NGINX" != "true" ]; then
            warn "Styles were written to $STYLES_DIR, but only nginx serves them (at /styles/). Without nginx, host that directory yourself."
        fi
    fi

    if [ "$SKIP_SYSTEMD" = "true" ]; then
        echo "systemd unit skipped (--skip-systemd). Run manually: $PMTILES_BIN serve $TILES_DIR --port=$TILES_PORT --cors='*'"
    elif ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl not found — skipping the pmtiles systemd unit."
    else
        if id -u www-data >/dev/null 2>&1; then TILES_USER="www-data"; else TILES_USER="$APP_USER"; fi

        # Behind nginx the tile server only needs to listen locally; otherwise
        # expose it directly so the client can reach it on TILES_PORT.
        if [ "$USE_NGINX" = "true" ]; then
            TILES_INTERFACE="127.0.0.1"
            PUBLIC_URL="${SCHEME}://${DOMAIN}/tiles"
        else
            TILES_INTERFACE="0.0.0.0"
            TILES_HOST="${DOMAIN:-$(detect_host_ip)}"
            PUBLIC_URL=""
            [ -n "$TILES_HOST" ] && PUBLIC_URL="http://${TILES_HOST}:${TILES_PORT}"
        fi
        PUBLIC_URL_ARG=""
        [ -n "$PUBLIC_URL" ] && PUBLIC_URL_ARG="--public-url=${PUBLIC_URL}"

        PMTILES_UNIT="/etc/systemd/system/pmtiles.service"
        sed \
            -e "s#__TILES_USER__#${TILES_USER}#g" \
            -e "s#__TILES_DIR__#${TILES_DIR}#g" \
            -e "s#__TILES_INTERFACE__#${TILES_INTERFACE}#g" \
            -e "s#__TILES_PORT__#${TILES_PORT}#g" \
            -e "s#__PUBLIC_URL_ARG__#${PUBLIC_URL_ARG}#g" \
            "$DEPLOY_DIR/pmtiles.service" | $SUDO tee "$PMTILES_UNIT" >/dev/null
        $SUDO systemctl daemon-reload
        $SUDO systemctl enable pmtiles
        echo "Installed and enabled $PMTILES_UNIT (user: $TILES_USER, ${TILES_INTERFACE}:${TILES_PORT})."

        if [ "$START_SERVICE" = "true" ]; then
            $SUDO systemctl restart pmtiles
            sleep 1
            $SUDO systemctl --no-pager status pmtiles || true
        else
            echo "Not starting service now (--no-start). Start later with: sudo systemctl start pmtiles"
        fi
    fi
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
        -e "s#__API_INTERFACE__#${API_INTERFACE}#g" \
        "$DEPLOY_DIR/trias-proxy.service" | $SUDO tee "$UNIT_PATH" >/dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable trias-proxy
    echo "Installed and enabled $UNIT_PATH (user: $APP_USER, ${API_INTERFACE}:${PORT})."

    if [ "$START_SERVICE" = "true" ]; then
        $SUDO systemctl restart trias-proxy
        sleep 1
        $SUDO systemctl --no-pager status trias-proxy || true
    else
        echo "Not starting service now (--no-start). Start later with: sudo systemctl start trias-proxy"
    fi
fi

# ---------------------------------------------------------------------------
log "Web client"
# ---------------------------------------------------------------------------
if [ -z "$WEB_BUILD" ]; then
    if [ -f "$WEB_DIR/index.html" ]; then
        echo "No --web-build given, keeping the web client already in $WEB_DIR."
    else
        echo "No --web-build given and nothing in $WEB_DIR yet, so / serves no web client. Build it with \`flutter build web\` and re-run with --web-build <path to build/web>."
    fi
else
    [ -f "$WEB_BUILD/index.html" ] || die "--web-build must be a Flutter web build directory containing index.html: $WEB_BUILD"
    command -v rsync >/dev/null 2>&1 || die "rsync not found — install it (or drop --skip-apt) to deploy the web client."
    $SUDO install -d -o "$APP_USER" -m 755 "$WEB_DIR"
    # --delete removes files of the previous build; the entry points are
    # copied last so browsers never load a new index.html with old assets.
    rsync -a --delete --exclude index.html --exclude flutter_bootstrap.js "$WEB_BUILD/" "$WEB_DIR/"
    rsync -a "$WEB_BUILD/index.html" "$WEB_BUILD/flutter_bootstrap.js" "$WEB_DIR/"
    echo "Deployed $WEB_BUILD -> $WEB_DIR"
    [ "$SKIP_NGINX" != "true" ] || warn "--skip-nginx given: the web client in $WEB_DIR is only served by nginx."
fi

# ---------------------------------------------------------------------------
log "nginx reverse proxy"
# ---------------------------------------------------------------------------
if [ "$SKIP_NGINX" = "true" ]; then
    echo "Skipped (--skip-nginx). The service is reachable directly on port $PORT."
elif [ -z "$DOMAIN" ]; then
    warn "Could not detect a host IP and no --domain was given — skipping nginx. The service is reachable directly on port $PORT."
elif ! command -v nginx >/dev/null 2>&1; then
    warn "nginx not found — skipping reverse proxy config."
else
    SITE_AVAILABLE="/etc/nginx/sites-available/trias-proxy"
    SITE_ENABLED="/etc/nginx/sites-enabled/trias-proxy"
    NGINX_CONF="$(sed \
        -e "s#__DOMAIN__#${DOMAIN}#g" \
        -e "s#__PORT__#${PORT}#g" \
        -e "s#__TILES_PORT__#${TILES_PORT}#g" \
        -e "s#__STYLES_DIR__#${STYLES_DIR}#g" \
        -e "s#__WEB_DIR__#${WEB_DIR}#g" \
        "$DEPLOY_DIR/nginx-trias-proxy.conf")"
    if [ "$SKIP_TILES" = "true" ]; then
        NGINX_CONF="$(printf '%s\n' "$NGINX_CONF" | sed '/# BEGIN tiles/,/# END tiles/d')"
    fi
    if [ -f "$SITE_AVAILABLE" ] && grep -q "managed by Certbot" "$SITE_AVAILABLE"; then
        # Overwriting would drop the TLS server block certbot added.
        NEW_CONF="$SITE_AVAILABLE.setup-new"
        printf '%s\n' "$NGINX_CONF" | $SUDO tee "$NEW_CONF" >/dev/null
        warn "$SITE_AVAILABLE was modified by certbot, leaving it untouched. The config this run would install is in $NEW_CONF — merge its location blocks into the TLS server block by hand, then: sudo nginx -t && sudo systemctl reload nginx"
    else
        printf '%s\n' "$NGINX_CONF" | $SUDO tee "$SITE_AVAILABLE" >/dev/null
        $SUDO ln -sf "$SITE_AVAILABLE" "$SITE_ENABLED"
        $SUDO nginx -t
        $SUDO systemctl reload nginx
        echo "Installed nginx site for $DOMAIN: / -> $WEB_DIR, /api/ -> 127.0.0.1:$PORT."
    fi
    case "$DOMAIN" in
        *[a-zA-Z]*) echo "For TLS, run: sudo certbot --nginx -d $DOMAIN" ;;
        *) echo "Note: $DOMAIN looks like a bare IP — certbot/TLS needs a real domain name pointed at it." ;;
    esac
fi

# ---------------------------------------------------------------------------
log "Done"
# ---------------------------------------------------------------------------
cat <<EOF
Service:   sudo systemctl {start|stop|restart|status} trias-proxy
Logs:      journalctl -u trias-proxy -f
Config:    $APP_DIR/.env
Reverse proxy: ${DOMAIN:-"(none — use http://127.0.0.1:$PORT directly)"}
Web client: ${DOMAIN:+${SCHEME}://${DOMAIN}/}${DOMAIN:-"(needs nginx)"}   (files: $WEB_DIR)
API:       ${DOMAIN:+${SCHEME}://${DOMAIN}/api/}${DOMAIN:-"http://127.0.0.1:$PORT/"}   (direct: ${API_INTERFACE}:${PORT})
Health:    curl -H "x-api-key: \$(grep TRIAS_PROXY_API_KEY $APP_DIR/.env | cut -d= -f2)" http://127.0.0.1:$PORT/health
EOF

if [ "$SKIP_TILES" != "true" ]; then
    cat <<EOF
Tiles:     sudo systemctl {start|stop|restart|status} pmtiles   (logs: journalctl -u pmtiles -f)
Tile URL:  $TILES_URL
EOF
    if [ "$USE_NGINX" = "true" ]; then
        echo "Styles:    ${SCHEME}://${DOMAIN}/styles/light.json  and  ${SCHEME}://${DOMAIN}/styles/dark.json"
    fi
fi
