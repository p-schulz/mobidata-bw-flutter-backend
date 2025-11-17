import os
import sqlite3
import math

DB_PATH = os.getenv("GTFS_DB_PATH")


def get_connection():
    if not DB_PATH or not os.path.exists(DB_PATH):
        raise RuntimeError(f"GTFS DB not found: {DB_PATH}")
    return sqlite3.connect(DB_PATH)


def find_stop_by_id(stop_id: str):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT stop_id, stop_name, stop_lat, stop_lon, stop_desc,
               location_type, parent_station, wheelchair_boarding
        FROM stops
        WHERE stop_id = ?
    """, (stop_id,))

    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def find_stops_in_bbox(south, west, north, east):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT stop_id, stop_name, stop_lat, stop_lon
        FROM stops
        WHERE stop_lat BETWEEN ? AND ?
          AND stop_lon BETWEEN ? AND ?
        LIMIT 5000
    """, (south, north, west, east))

    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_stops_nearby(lat, lon, radius_m=500):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT stop_id, stop_name, stop_lat, stop_lon
        FROM stops
    """)

    result = []
    for r in cur.fetchall():
        d = _haversine(lat, lon, r["stop_lat"], r["stop_lon"])
        if d <= radius_m:
            item = dict(r)
            item["distance_m"] = d
            result.append(item)

    conn.close()
    result.sort(key=lambda x: x["distance_m"])
    return result


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # Meter
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))
