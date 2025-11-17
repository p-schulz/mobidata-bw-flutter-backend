import os
import asyncio
import sqlite3
import asyncio

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from gtfs_fallback import get_static_departures_for_stop
from gtfs_stops import find_stop_by_id, find_stops_in_bbox, find_stops_nearby

API_KEY = os.getenv("TRIAS_PROXY_API_KEY")
if API_KEY is None:
    raise RuntimeError("TRIAS_PROXY_API_KEY nicht gesetzt (siehe .env)")

app = FastAPI(title="GTFS Proxy", version="1.0.0")

LOC_DB_PATH = os.getenv("LOCATIONS_DB_PATH", "/home/trias/locations.sqlite")

# CORS
# demo: allow all origins, include restrictions later
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# limit rates
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests, slow down."},
    )


# API key verification
async def verify_api_key(
    request: Request,
    x_api_key: str = Header(None),
) -> None:
    # CORS-preflight (OPTIONS) w/o check
    if request.method == "OPTIONS":
        return
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# todo: generic OPTIONS-handler, in case framework isn't behaving correct
@app.options("/{rest_of_path:path}")
async def options_handler(rest_of_path: str):
    # empty respone – CORS-Middleware appends headers
    return Response(status_code=200)


# health 
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# departures from static GTFS
@app.get(
    "/departures",
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("30/minute")
async def departures(
    request: Request,
    stop_id: str,
    max_results: int = 10,
    horizon_min: int = 30,
) -> dict:
    results = await asyncio.to_thread(
        get_static_departures_for_stop,
        stop_id,
        horizon_min,
        max_results,
    )

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No static GTFS departures found for stop_id={stop_id} in next {horizon_min} minutes.",
        )

    return {
        "source": "static-gtfs",
        "stop_id": stop_id,
        "max_results": max_results,
        "horizon_min": horizon_min,
        "departures": results,
    }


# stations / stops: single stop by id
@app.get(
    "/stop",
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("60/minute")
async def stop(
    request: Request,
    stop_id: str,
):
    result = await asyncio.to_thread(find_stop_by_id, stop_id)
    if not result:
        raise HTTPException(404, f"Stop '{stop_id}' not found")
    return result


# stops: bbox (for map)
@app.get(
    "/stops/bbox",
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("60/minute")
async def stops_bbox(
    request: Request,
    south: float,
    west: float,
    north: float,
    east: float,
):
    result = await asyncio.to_thread(
        find_stops_in_bbox, south, west, north, east
    )
    return {"count": len(result), "stops": result}


# stops: nearby
@app.get(
    "/stops/nearby",
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("60/minute")
async def stops_nearby(
    request: Request,
    lat: float,
    lon: float,
    radius_m: int = 500,
):
    result = await asyncio.to_thread(
        find_stops_nearby, lat, lon, radius_m
    )
    return {"count": len(result), "stops": result}



# GeoCoder
def search_locations(query: str, limit: int = 20):
    """Liest Orte aus locations.sqlite anhand des Namenfilters."""
    if not os.path.exists(LOC_DB_PATH):
        raise RuntimeError(f"Locations DB not found at {LOC_DB_PATH}")

    conn = sqlite3.connect(LOC_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    pattern = f"%{query}%"
    cur.execute("""
        SELECT name, lat, lon, ags, landkreis, region
        FROM locations
        WHERE name LIKE ?
        ORDER BY name
        LIMIT ?
    """, (pattern, limit))

    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get(
    "/locations/search",
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit("60/minute")
async def locations_search(
    request: Request,
    q: str,
    limit: int = 20,
):
    if not q.strip():
        raise HTTPException(400, "Parameter 'q' must not be empty.")

    try:
        results = await asyncio.to_thread(search_locations, q, limit)
    except Exception as e:
        # todo: log exception
        raise HTTPException(
            status_code=500,
            detail=f"locations_search internal error: {type(e).__name__}: {e}",
        )

    return {
        "count": len(results),
        "locations": results,
    }

