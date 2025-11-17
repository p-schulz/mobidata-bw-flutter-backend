#!/usr/bin/env python3
"""
Script to build locations from LGL-BW administrative boundaries (v_al_gemeinde.shp)
into a SQLite database with municipality centroids for use as a simple geocoder.

- Input: Verwaltungsgrenzen/v_al_gemeinde.shp
- CRS: EPSG:25832 (ETRS89 / UTM 32N)
    -> transformed to EPSG:4326
- Output: locations.sqlite with table 'locations'
"""

import os
import sqlite3

import geopandas as gpd
import pandas as pd


# --------------------------------------------------
# CONFIG – adjust paths and field names as needed
# --------------------------------------------------

SHP_PATH = "/home/trias/Verwaltungsgrenzen/v_al_gemeinde.shp"

# field name mappings in the shapefile
#   gemeinde_i: Integer64 -> key/id
#   gemeinde_n: String    -> name of municipality
#   kreis_name: String    -> region/district
#   regierun_1: String    -> higher-level region
SHP_FIELD_AGS = "gemeinde_i"
SHP_FIELD_NAME = "gemeinde_n"
SHP_FIELD_LANDKREIS = "kreis_name"
SHP_FIELD_REGION = "regierun_1"


# optional filter to limit to specific region
SHP_FILTER_FIELD = None
SHP_FILTER_VALUE = None

KEY_CSV_PATH = None

OUT_DB_PATH = "locations.sqlite"
OUT_TABLE = "locations"


# --------------------------------------------------
# HELPER
# --------------------------------------------------

def load_gemeinden():
    if not os.path.exists(SHP_PATH):
        raise FileNotFoundError(f"SHP file not found: {SHP_PATH}")

    print(f"[INFO] Lade Shapefile: {SHP_PATH}")
    gdf = gpd.read_file(SHP_PATH)

    print(f"[INFO] Datensätze gesamt: {len(gdf)}")
    if SHP_FILTER_FIELD and SHP_FILTER_VALUE is not None:
        before = len(gdf)
        gdf = gdf[gdf[SHP_FILTER_FIELD] == SHP_FILTER_VALUE]
        print(f"[INFO] Filter {SHP_FILTER_FIELD} = {SHP_FILTER_VALUE}: {before} -> {len(gdf)}")

    # CRS check, then transform to EPSG:4326
    if gdf.crs is None:
        print("[WARN] Kein CRS im Shapefile gesetzt, nehme EPSG:25832 an.")
        gdf.set_crs(epsg=25832, inplace=True)

    if gdf.crs.to_epsg() != 4326:
        print(f"[INFO] Transformiere CRS {gdf.crs} -> EPSG:4326")
        gdf = gdf.to_crs(epsg=4326)

    print("[INFO] Berechne Centroids...")
    gdf["centroid"] = gdf.geometry.centroid
    gdf["lat"] = gdf["centroid"].y
    gdf["lon"] = gdf["centroid"].x

    cols = [SHP_FIELD_AGS, SHP_FIELD_NAME, "lat", "lon"]
    if SHP_FIELD_LANDKREIS:
        cols.append(SHP_FIELD_LANDKREIS)
    if SHP_FIELD_REGION:
        cols.append(SHP_FIELD_REGION)

    gdf = gdf[cols].copy()
    gdf.rename(columns={
        SHP_FIELD_AGS: "ags",
        SHP_FIELD_NAME: "name",
        SHP_FIELD_LANDKREIS: "landkreis" if SHP_FIELD_LANDKREIS else None,
        SHP_FIELD_REGION: "region" if SHP_FIELD_REGION else None,
    }, inplace=True)

    print(f"[INFO] Gemeinden nach Aufbereitung: {len(gdf)}")
    return gdf


def write_sqlite(df: pd.DataFrame):
    if os.path.exists(OUT_DB_PATH):
        print(f"[WARN] Ziel-DB {OUT_DB_PATH} existiert, lösche sie.")
        os.remove(OUT_DB_PATH)

    print(f"[INFO] Erzeuge SQLite: {OUT_DB_PATH}")
    conn = sqlite3.connect(OUT_DB_PATH)
    cur = conn.cursor()

    cur.execute(f"""
      CREATE TABLE IF NOT EXISTS {OUT_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ags TEXT,
        name TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        landkreis TEXT,
        region TEXT
      );
    """)
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE}_name ON {OUT_TABLE}(name COLLATE NOCASE);")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{OUT_TABLE}_lat_lon ON {OUT_TABLE}(lat, lon);")

    conn.commit()

    print("[INFO] Schreibe Datensätze in die DB...")
    records = []
    for _, row in df.iterrows():
        records.append((
            str(row.get("ags")),
            row.get("name"),
            float(row.get("lat")),
            float(row.get("lon")),
            row.get("landkreis") if "landkreis" in df.columns else None,
            row.get("region") if "region" in df.columns else None,
        ))

    cur.executemany(f"""
      INSERT INTO {OUT_TABLE} (ags, name, lat, lon, landkreis, region)
      VALUES (?, ?, ?, ?, ?, ?);
    """, records)
    conn.commit()
    conn.close()

    print(f"[INFO] Fertig. Geschriebene Datensätze: {len(records)}")
    print(f"[INFO] SQLite-DB bereit: {OUT_DB_PATH}")


def main():
    gdf = load_gemeinden()
    df = pd.DataFrame(gdf.drop(columns=["geometry", "centroid"], errors="ignore"))
    write_sqlite(df)


if __name__ == "__main__":
    main()
