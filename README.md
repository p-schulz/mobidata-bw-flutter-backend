# mobidata-bw-flutter-backend - Lightweight Mobility API Proxy

*A FastAPI-based backend for GTFS/SQLite stop lookups, geocoding, and optional TRIAS integration.*

This project provides a lightweight, self-hosted mobility API backend for the **MobiData BW in Flutter App**: https://github.com/p-schulz/mobidata-bw-flutter  

The provided shape files 'v_al_gemeinde.*' for the geocoder are under license "Datenquelle: LGL, www.lgl-bw.de, dl-de/by-2-0" and only provided for fast server setup

It exposes a clean HTTP API for:
- `GET /stops/bbox` — spatial stop queries using local GTFS data  
- `GET /departures` — GTFS-based fallback departure board  
- `GET /locations/search` — geocoding from a local SQLite gazetteer  
- Optional TRIAS proxy (can be fully disabled)

The backend is designed to be:

- **Secure** (API key + rate limiting)  
- **Fast** (SQLite queries)  
- **Portable** (runs anywhere)  
- **Lightweight** (no external services required)

## Features

- **SQLite-based GTFS database** for instant lookup of stops, trips, stop_times and service days  
- **Local Geocoder** based on a simple SQLite lookup table  
- **Rate Limiting** powered by `slowapi`  
- **API Key Authentication** via `x-api-key`  
- **Optional TRIAS proxy** for live data  
- **CORS support** for browser clients (e.g., Flutter Web)

---

## Automated Setup (Ubuntu)

`./setup.sh` is a complete, one-shot setup: system packages, the venv,
Python dependencies, a generated `trias-proxy/.env`, the GTFS seed and
locations databases, a `systemd` service (so the proxy survives crashes and
reboots — no more manual `screen` sessions), and an nginx reverse proxy in
front of it.

```bash
sudo ./setup.sh
```

With no options, this:
- runs `generate_gtfs_seed.py` to download the current GTFS ZIP and build
  `trias-proxy/gtfs_seed.sqlite` (pass `--gtfs-db <path>` to use a pre-built
  file instead, or `--skip-gtfs-build` to skip it)
- installs `trias-proxy/locations.sqlite` from `--locations-db <path>` if
  given, or builds it from a shapefile with `--lgl-shp
  /path/to/v_al_gemeinde.shp` (the LGL-BW admin-boundaries shapefile isn't
  auto-downloadable — obtain it separately); without either, this step is
  skipped with a warning
- installs and enables the `trias-proxy` systemd service
- installs an nginx reverse proxy in front of it, using `--domain
  your.host.name` if given, otherwise the server's own detected IP address
  (see `deploy/nginx-trias-proxy.conf`; follow up with `sudo certbot --nginx
  -d your.host.name` for TLS — that needs a real domain, not a bare IP)

Run `./setup.sh --help` for all options, or `./setup.sh --skip-systemd
--skip-nginx --skip-gtfs-build` to only do the manual steps below. The
script is idempotent — safe to re-run after pulling updates; it never
overwrites an `.env` or database file that's already in place.

Once installed:

```bash
sudo systemctl {start|stop|restart|status} trias-proxy
journalctl -u trias-proxy -f          # logs
```

The `systemd` unit template lives at `deploy/trias-proxy.service`; the
`screen`-based `start_triasproxy.sh` / `stop_trias.sh` scripts below still
work for quick manual/dev runs.

## Manual Setup

### System Requirements (tested on Ubuntu 22.04+)

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip sqlite3 unzip curl
```

## Python Dependencies

Install into a virtual environment:

```bash
cd trias-proxy
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```


## Structure

```bash
trias-proxy/
│
├── main.py                 # FastAPI application
├── gtfs_seed.sqlite        # Preloaded GTFS SQLite DB
├── locations.sqlite        # Local geocoding DB
├── start_proxy.sh          # Script for production mode (screen)
└── README.md
```

## Environment Variables

| Variable      | Description   |
| ------------- | ------------- |
| TRIAS_PROXY_API_KEY | API key required for all requests |
| GTFS_DB_PATH | Path to the GTFS SQLite database |
| LOCATIONS_DB_PATH | Path to the geocoding SQLite dataset |
| (optional) TRIAS_ENDPOINT | TRIAS endpoint URL |

```bash
export TRIAS_PROXY_API_KEY="your-secret-key"
export GTFS_DB_PATH="/home/user/trias-proxy/gtfs_seed.sqlite"
export LOCATIONS_DB_PATH="/home/user/trias-proxy/locations.sqlite"
```

## API Endpoints

Stops in bbox
```bash
GET /stops/bbox?south=...&west=...&north=...&east=...
Headers: x-api-key
```

Departure Board (GTFS only)
```bash
GET /departures?stop_id=1234&max_results=10&horizon_min=30
Headers: x-api-key
```

Location Search (SQLite geocoder)
```bash
GET /locations/search?q=Stuttgart&limit=10
Headers: x-api-key
```

## Running

Development mode
```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Production Mode (using screen)
```bash
./start_proxy.sh
```

Stopping the service
```bash
screen -S triasproxy -X quit
```

## Security Notes
-	Always protect the API behind the x-api-key header
-	Consider additional throttling or IP whitelisting for public deployments
-	Do not expose SQLite files containing sensitive metadata
