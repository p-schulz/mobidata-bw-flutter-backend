#!/usr/bin/env python3
"""
Generate a gtfs_seed.sqlite file in assets/gtfs/ by downloading the latest
GTFS ZIP, extracting the relevant CSV files, and populating a SQLite database
with the schema used by the Flutter app.
"""

import csv
import io
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta
from urllib.request import urlopen

GTFS_ZIP_URL = (
    "https://mobidata-bw.de/gtfs-historisierung/mit_linienverlauf/2026/"
    "20260601/bwgesamt.zip"
)
OUTPUT_PATH = os.path.join("assets", "gtfs", "gtfs_seed.sqlite")
ZIP_CACHE_PATH = os.path.join("assets", "gtfs", os.path.basename(GTFS_ZIP_URL))
MIN_ZIP_SIZE_BYTES = 500 * 1024 * 1024  # below this, treat a cached ZIP as incomplete/stale


def ensure_output_dir():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)


def _print_progress(downloaded: int, total: int) -> None:
    mib = downloaded / (1024 * 1024)
    if total:
        pct = downloaded / total * 100
        bar_width = 30
        filled = int(bar_width * downloaded / total)
        bar = "#" * filled + "-" * (bar_width - filled)
        total_mib = total / (1024 * 1024)
        print(f"\r[{bar}] {pct:5.1f}%  {mib:7.1f}/{total_mib:.1f} MiB", end="", flush=True)
    else:
        print(f"\rDownloaded {mib:7.1f} MiB", end="", flush=True)


def ensure_zip_downloaded(path: str) -> str:
    """Downloads the GTFS ZIP to `path`, unless an adequately sized copy is already there."""
    if os.path.exists(path) and os.path.getsize(path) >= MIN_ZIP_SIZE_BYTES:
        size_mib = os.path.getsize(path) / (1024 * 1024)
        print(f"Using cached GTFS ZIP at {path} ({size_mib:.1f} MiB), skipping download.")
        return path

    print(f"Downloading GTFS ZIP from {GTFS_ZIP_URL} …")
    tmp_path = path + ".part"
    with urlopen(GTFS_ZIP_URL) as resp, open(tmp_path, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        chunk_size = 1024 * 1024  # 1 MiB
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            _print_progress(downloaded, total)
        print()
    os.replace(tmp_path, path)
    return path


def open_db(path: str) -> sqlite3.Connection:
    need_init = not os.path.exists(path)
    conn = sqlite3.connect(path)
    # This is a scratch DB rebuilt from zero on every run (see main()); trading
    # durability for I/O keeps the many periodic commits below from turning
    # into a disk-I/O storm on constrained/slow-disk hosts. Worst case on a
    # crash is just rerunning the script.
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    if need_init:
        create_schema(conn)
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT
        );

        CREATE TABLE IF NOT EXISTS stops (
          stop_id TEXT PRIMARY KEY,
          stop_name TEXT NOT NULL,
          stop_desc TEXT,
          stop_lat REAL NOT NULL,
          stop_lon REAL NOT NULL,
          location_type INTEGER DEFAULT 0,
          parent_station TEXT,
          wheelchair_boarding INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_stops_lat_lon ON stops(stop_lat, stop_lon);
        CREATE INDEX IF NOT EXISTS idx_stops_name_nocase
          ON stops(stop_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_stops_parent ON stops(parent_station);

        CREATE TABLE IF NOT EXISTS routes (
          route_id TEXT PRIMARY KEY,
          short_name TEXT,
          long_name TEXT,
          type INTEGER
        );

        CREATE TABLE IF NOT EXISTS trips (
          trip_id TEXT PRIMARY KEY,
          route_id TEXT,
          service_id TEXT,
          headsign TEXT,
          direction_id INTEGER,
          shape_id TEXT
        );

        CREATE TABLE IF NOT EXISTS stop_times (
          trip_id TEXT,
          arrival_time TEXT,
          departure_time TEXT,
          stop_id TEXT,
          stop_sequence INTEGER,
          pickup_type INTEGER,
          drop_off_type INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id);
        CREATE INDEX IF NOT EXISTS idx_stop_times_trip ON stop_times(trip_id);

        CREATE TABLE IF NOT EXISTS calendar (
          service_id TEXT PRIMARY KEY,
          monday INTEGER,
          tuesday INTEGER,
          wednesday INTEGER,
          thursday INTEGER,
          friday INTEGER,
          saturday INTEGER,
          sunday INTEGER,
          start_date TEXT,
          end_date TEXT
        );

        CREATE TABLE IF NOT EXISTS calendar_dates (
          service_id TEXT,
          date TEXT,
          exception_type INTEGER
        );

        CREATE TABLE IF NOT EXISTS service_days (
          service_id TEXT,
          service_date TEXT,
          PRIMARY KEY (service_id, service_date)
        );

        CREATE TABLE IF NOT EXISTS stop_route_types (
          stop_id TEXT,
          route_type INTEGER,
          PRIMARY KEY (stop_id, route_type)
        );
        CREATE INDEX IF NOT EXISTS idx_stop_route_stop
          ON stop_route_types(stop_id);
        """
    )
    conn.commit()


def clear_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        DELETE FROM stop_times;
        DELETE FROM trips;
        DELETE FROM routes;
        DELETE FROM stops;
        DELETE FROM calendar;
        DELETE FROM calendar_dates;
        DELETE FROM service_days;
        DELETE FROM stop_route_types;
        DELETE FROM metadata;
        """
    )
    conn.commit()


def iter_csv_from_zip(zf: zipfile.ZipFile, name: str):
    """Streams rows of `name` out of the ZIP without ever holding the whole
    (decompressed) file or its parsed rows in memory at once — GTFS files
    like stop_times.txt can be millions of rows."""
    try:
        raw = zf.open(name)
    except KeyError:
        return
    with raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        yield from csv.DictReader(text)


BATCH_SIZE = 2000


def _batched(rows, batch_size=BATCH_SIZE):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def import_stops(conn, rows):
    cur = conn.cursor()
    for batch in _batched(rows):
        cur.executemany(
            """
            INSERT OR REPLACE INTO stops(
              stop_id, stop_name, stop_desc, stop_lat, stop_lon,
              location_type, parent_station, wheelchair_boarding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["stop_id"],
                    row.get("stop_name"),
                    row.get("stop_desc"),
                    float(row["stop_lat"]),
                    float(row["stop_lon"]),
                    int(row.get("location_type") or 0),
                    row.get("parent_station"),
                    int(row.get("wheelchair_boarding") or 0),
                )
                for row in batch
                if row.get("stop_lat") and row.get("stop_lon")
            ],
        )
        conn.commit()


def import_routes(conn, rows):
    cur = conn.cursor()
    for batch in _batched(rows):
        cur.executemany(
            """
            INSERT OR REPLACE INTO routes(route_id, short_name, long_name, type)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    row["route_id"],
                    row.get("route_short_name"),
                    row.get("route_long_name"),
                    int(row.get("route_type") or 0),
                )
                for row in batch
            ],
        )
        conn.commit()


def import_trips(conn, rows):
    cur = conn.cursor()
    for batch in _batched(rows):
        cur.executemany(
            """
            INSERT OR REPLACE INTO trips(
              trip_id, route_id, service_id, headsign, direction_id, shape_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["trip_id"],
                    row["route_id"],
                    row["service_id"],
                    row.get("trip_headsign"),
                    int(row.get("direction_id") or 0),
                    row.get("shape_id"),
                )
                for row in batch
            ],
        )
        conn.commit()


def import_stop_times(conn, rows):
    cur = conn.cursor()
    for batch in _batched(rows):
        cur.executemany(
            """
            INSERT OR REPLACE INTO stop_times(
              trip_id, arrival_time, departure_time,
              stop_id, stop_sequence, pickup_type, drop_off_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["trip_id"],
                    row.get("arrival_time"),
                    row.get("departure_time"),
                    row["stop_id"],
                    int(row.get("stop_sequence") or 0),
                    int(row.get("pickup_type") or 0),
                    int(row.get("drop_off_type") or 0),
                )
                for row in batch
            ],
        )
        conn.commit()


def import_calendar(conn, rows):
    cur = conn.cursor()
    for batch in _batched(rows):
        cur.executemany(
            """
            INSERT OR REPLACE INTO calendar(
              service_id, monday, tuesday, wednesday,
              thursday, friday, saturday, sunday,
              start_date, end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["service_id"],
                    int(row.get("monday") or 0),
                    int(row.get("tuesday") or 0),
                    int(row.get("wednesday") or 0),
                    int(row.get("thursday") or 0),
                    int(row.get("friday") or 0),
                    int(row.get("saturday") or 0),
                    int(row.get("sunday") or 0),
                    row.get("start_date"),
                    row.get("end_date"),
                )
                for row in batch
            ],
        )
        conn.commit()


def import_calendar_dates(conn, rows):
    cur = conn.cursor()
    for batch in _batched(rows):
        cur.executemany(
            """
            INSERT INTO calendar_dates(service_id, date, exception_type)
            VALUES (?, ?, ?)
            """,
            [
                (
                    row["service_id"],
                    row["date"],
                    int(row.get("exception_type") or 0),
                )
                for row in batch
            ],
        )
        conn.commit()


def _insert_service_days(cur, pairs):
    cur.executemany(
        """
        INSERT OR REPLACE INTO service_days(service_id, service_date)
        VALUES (?, ?)
        """,
        pairs,
    )


def _iter_service_days(calendars):
    for cal in calendars:
        service_id = cal[0]
        start = parse_yyyymmdd(cal[8])
        end = parse_yyyymmdd(cal[9])
        if not start or not end:
            continue
        current = start
        while current <= end:
            weekday_column = current.weekday()
            if cal[weekday_column + 1]:
                yield (service_id, format_yyyymmdd(current))
            current += timedelta(days=1)


def build_service_days(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM service_days")
    conn.commit()

    # Expanding calendar.txt into one row per (service_id, date) can produce
    # hundreds of thousands of rows for a statewide feed. Batch + commit
    # periodically instead of one INSERT per row held in a single giant
    # transaction — that used to build up a huge rollback journal that only
    # got flushed to disk in one burst at the final commit.
    calendars = cur.execute("SELECT * FROM calendar").fetchall()
    for batch in _batched(_iter_service_days(calendars)):
        _insert_service_days(cur, batch)
        conn.commit()

    additions = cur.execute(
        """SELECT service_id, date FROM calendar_dates WHERE exception_type = 1"""
    ).fetchall()
    for batch in _batched(additions):
        _insert_service_days(cur, batch)
        conn.commit()

    removals = cur.execute(
        """SELECT service_id, date FROM calendar_dates WHERE exception_type = 2"""
    ).fetchall()
    cur.executemany(
        "DELETE FROM service_days WHERE service_id = ? AND service_date = ?",
        removals,
    )
    conn.commit()


def build_route_types(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM stop_route_types")
    cur.execute(
        """
        INSERT INTO stop_route_types(stop_id, route_type)
        SELECT DISTINCT st.stop_id, r.type
        FROM stop_times st
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE r.type IS NOT NULL
        """
    )
    conn.commit()


def parse_yyyymmdd(value: str | None):
    if not value or len(value) != 8:
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def format_yyyymmdd(value: datetime) -> str:
    return value.strftime("%Y%m%d")


def main():
    ensure_output_dir()
    zip_path = ensure_zip_downloaded(ZIP_CACHE_PATH)
    zf = zipfile.ZipFile(zip_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "gtfs_temp.sqlite")
        conn = open_db(db_path)
        clear_tables(conn)

        import_stops(conn, iter_csv_from_zip(zf, "stops.txt"))
        import_routes(conn, iter_csv_from_zip(zf, "routes.txt"))
        import_trips(conn, iter_csv_from_zip(zf, "trips.txt"))
        import_stop_times(conn, iter_csv_from_zip(zf, "stop_times.txt"))
        import_calendar(conn, iter_csv_from_zip(zf, "calendar.txt"))
        import_calendar_dates(conn, iter_csv_from_zip(zf, "calendar_dates.txt"))

        build_service_days(conn)
        build_route_types(conn)

        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('gtfs_version', ?)",
            (_gtfsVersion(),),
        )
        conn.commit()
        conn.close()

        os.replace(db_path, OUTPUT_PATH)
        print(f"Wrote seed database to {OUTPUT_PATH}")


def _gtfsVersion() -> str:
    return "20251008-stopsV2"


if __name__ == "__main__":
    sys.exit(main())
