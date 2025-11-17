import os
import sqlite3
import datetime as dt

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python < 3.9
    from backports.zoneinfo import ZoneInfo

DB_PATH = os.getenv("GTFS_DB_PATH", "/home/trias/gtfs_seed.sqlite")


def _get_active_service_ids(conn, today: dt.date):
    """
    Determines currently valid service_id-Werte.

    Uses service_days-table:
       service_days(service_id TEXT, service_date TEXT YYYYMMDD)

    If non-existent or empty, fallback to calendar + calendar_dates
    """
    ymd = today.strftime("%Y%m%d")
    cur = conn.cursor()

    # using service_days
    try:
        cur.execute(
            "SELECT service_id FROM service_days WHERE service_date = ?",
            (ymd,),
        )
        rows = cur.fetchall()
        if rows:
            return {r[0] for r in rows}
    except sqlite3.OperationalError:
        # table found- ignore
        pass

    # fallback: calendar + calendar_dates
    weekday = today.weekday()  # 0=Mo ... 6=So
    weekday_col = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ][weekday]

    # calendar: start_date / end_date as TEXT (YYYYMMDD)
    cur.execute(
        f"""
        SELECT service_id
        FROM calendar
        WHERE {weekday_col} = 1
          AND start_date <= ?
          AND end_date >= ?
        """,
        (ymd, ymd),
    )
    services = {row[0] for row in cur.fetchall()}

    # calendar_dates: date as TEXT (YYYYMMDD), exception_type 1/2
    cur.execute(
        """
        SELECT service_id, exception_type
        FROM calendar_dates
        WHERE date = ?
        """,
        (ymd,),
    )
    for sid, ex in cur.fetchall():
        if ex == 1:
            services.add(sid)
        elif ex == 2 and sid in services:
            services.remove(sid)

    return services


def _time_to_seconds(hms: str) -> int:
    """
    "HH:MM:SS" -> sec since midnight
    GTFS can possibly have >24h (e.g.m "25:10:00"), do not apply mdulo-logic!
    """
    parts = hms.split(":")
    if len(parts) != 3:
        return 0
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return h * 3600 + m * 60 + s


def get_static_departures_for_stop(
    stop_id: str,
    horizon_min: int = 30,
    max_results: int = 10,
):
    """
    Fallback: next departures from static GTFS.

    Expects a SQLite DB with correct schema and path DB_PATH.
    Returns a list of dicts with line, headsign and departure_time.
    """
    if not os.path.exists(DB_PATH):
        # no fallback-DB found - no results
        return []

    now = dt.datetime.now(ZoneInfo("Europe/Berlin"))
    today = now.date()

    sec_now = now.hour * 3600 + now.minute * 60 + now.second
    sec_horizon = sec_now + horizon_min * 60

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        active_services = _get_active_service_ids(conn, today)
        if not active_services:
            return []

        # TODO: maybe shard by first letter or so to prevent IN(...) from exploding with too many services,
        placeholders = ",".join(["?"] * len(active_services))

        cur = conn.cursor()
        query = f"""
            SELECT
                st.trip_id,
                st.departure_time,
                st.stop_sequence,
                t.route_id,
                t.headsign,
                t.direction_id,
                r.short_name,
                r.long_name,
                r.type AS route_type
            FROM stop_times AS st
            JOIN trips AS t ON t.trip_id = st.trip_id
            JOIN routes AS r ON r.route_id = t.route_id
            WHERE st.stop_id = ?
              AND t.service_id IN ({placeholders})
        """

        params = [stop_id, *active_services]
        cur.execute(query, params)

        results = []
        for row in cur.fetchall():
            dep_hms = row["departure_time"]
            dep_sec = _time_to_seconds(dep_hms)

            # simple version: only today's departures in the time window
            # nightlines with >24h are strictly cut off here;
            # will be refined later if needed (todo)
            if dep_sec < sec_now or dep_sec > sec_horizon:
                continue

            line = row["short_name"] or row["long_name"] or ""
            results.append(
                {
                    "trip_id": row["trip_id"],
                    "route_id": row["route_id"],
                    "line": line,
                    "headsign": row["headsign"],
                    "direction_id": row["direction_id"],
                    "route_type": row["route_type"],
                    "departure_time": dep_hms,
                    "stop_sequence": row["stop_sequence"],
                    "source": "static-gtfs",
                }
            )

        # sort by departure_time
        results.sort(key=lambda x: x["departure_time"])
        return results[:max_results]

    finally:
        conn.close()
