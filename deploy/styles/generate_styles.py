#!/usr/bin/env python3
"""
Generate Mapbox/MapLibre style JSON files (style spec v8) for the Protomaps
Basemap vector tiles served by the pmtiles service.

One layer definition (build_layers) is combined with one colour palette per
style, so the light and dark variants can't drift apart. To add a palette
(e.g. high contrast), add a dict to PALETTES and re-run this script.

The layers target the Protomaps Basemap v4 schema (layers: earth, landcover,
landuse, water, roads, buildings, boundaries, places, pois) and only use
features supported by vector_tile_renderer / vector_map_tiles in the Flutter
client. POIs are intentionally not drawn (no sprite sheet, and the app draws
its own markers).

Usage:
    generate_styles.py --tiles-url http://HOST/tiles/bw/{z}/{x}/{y}.mvt --out DIR
"""

import argparse
import hashlib
import json
import os

SOURCE = "protomaps"
MAX_ZOOM = 15  # Protomaps Basemap v4 tiles go up to z15

PALETTES = {
    "light": {
        "background": "#f8f4f0",
        "urban": "#efeae3",
        "park": "#d5e8c8",
        "farmland": "#eef0dc",
        "forest": "#c5dfb3",
        "wetland": "#cfe6d9",
        "cemetery": "#d3e3cc",
        "sand": "#efe6c8",
        "industrial": "#e9e5e0",
        "institution": "#f0e6d6",
        "airport": "#e6e3ee",
        "plaza": "#f1ede6",
        "building": "#e6dfd5",
        "building_outline": "#d4ccc0",
        "water": "#c4e3f3",
        "water_line": "#a9d3ea",
        "road_minor": "#ffffff",
        "road_minor_casing": "#d9d2c7",
        "road_major": "#fff3c4",
        "road_major_casing": "#d8c9a0",
        "road_highway": "#fcd48a",
        "road_highway_casing": "#d9a95c",
        "path": "#b9a99a",
        "cycleway": "#6b9bd8",
        "rail": "#a8a29a",
        "runway": "#dcd6e6",
        "boundary_country": "#9a8f9f",
        "boundary_region": "#b8aec0",
        "text": "#3b3733",
        "text_muted": "#7a736c",
        "text_halo": "#f8f4f0",
        "water_text": "#4a86a8",
        "road_text": "#5b544d",
        "road_text_halo": "#ffffff",
    },
    "dark": {
        "background": "#1a1a1a",
        "urban": "#202020",
        "park": "#1f2b21",
        "farmland": "#222620",
        "forest": "#1b2a1e",
        "wetland": "#1b2a29",
        "cemetery": "#202a22",
        "sand": "#2a2820",
        "industrial": "#222222",
        "institution": "#2a2622",
        "airport": "#232330",
        "plaza": "#262626",
        "building": "#2a2a2a",
        "building_outline": "#3a3a3a",
        "water": "#0d2b45",
        "water_line": "#12395c",
        "road_minor": "#3a3a3a",
        "road_minor_casing": "#141414",
        "road_major": "#4a4a48",
        "road_major_casing": "#141414",
        "road_highway": "#7a6a48",
        "road_highway_casing": "#141414",
        "path": "#5a5a5a",
        "cycleway": "#4b7bbf",
        "rail": "#555555",
        "runway": "#333333",
        "boundary_country": "#6b6470",
        "boundary_region": "#55505a",
        "text": "#e6e1da",
        "text_muted": "#9a948c",
        "text_halo": "#1a1a1a",
        "water_text": "#6fa8d0",
        "road_text": "#b5aea5",
        "road_text_halo": "#1a1a1a",
    },
}


def zoom_interp(*pairs, base=1.0):
    """["interpolate", ..., ["zoom"], z1, v1, z2, v2, ...] from (zoom, value) pairs."""
    curve = ["linear"] if base == 1.0 else ["exponential", base]
    expr = ["interpolate", curve, ["zoom"]]
    for zoom, value in pairs:
        expr += [zoom, value]
    return expr


def layer(id_, type_, source_layer=None, paint=None, layout=None, filter_=None,
          minzoom=None, maxzoom=None):
    out = {"id": id_, "type": type_}
    if source_layer:
        out["source"] = SOURCE
        out["source-layer"] = source_layer
    if minzoom is not None:
        out["minzoom"] = minzoom
    if maxzoom is not None:
        out["maxzoom"] = maxzoom
    if filter_ is not None:
        out["filter"] = filter_
    if layout:
        out["layout"] = layout
    out["paint"] = paint or {}
    return out


def kinds(*values):
    return ["in", "kind"] + list(values)


def fill(id_, source_layer, color, filter_, minzoom=None, maxzoom=None, outline=None):
    paint = {"fill-color": color}
    if outline:
        paint["fill-outline-color"] = outline
    return layer(id_, "fill", source_layer, paint, filter_=filter_,
                 minzoom=minzoom, maxzoom=maxzoom)


def line(id_, source_layer, color, width, filter_, minzoom=None, maxzoom=None,
         dash=None, opacity=None):
    paint = {"line-color": color, "line-width": width}
    if dash:
        paint["line-dasharray"] = dash
    if opacity is not None:
        paint["line-opacity"] = opacity
    return layer(id_, "line", source_layer, paint,
                 layout={"line-cap": "round", "line-join": "round"},
                 filter_=filter_, minzoom=minzoom, maxzoom=maxzoom)


def label(id_, source_layer, color, halo, size, filter_, minzoom=None, maxzoom=None,
          placement="point", transform=None, max_width=None, halo_width=1.5):
    layout = {
        # name:de is only set where it differs from the local name.
        "text-field": ["coalesce", ["get", "name:de"], ["get", "name"]],
        "text-size": size,
        "symbol-placement": placement,
    }
    if transform:
        layout["text-transform"] = transform
    if max_width:
        layout["text-max-width"] = max_width
    paint = {"text-color": color, "text-halo-color": halo, "text-halo-width": halo_width}
    return layer(id_, "symbol", source_layer, paint, layout, filter_,
                 minzoom=minzoom, maxzoom=maxzoom)


def road_widths(*pairs):
    return zoom_interp(*pairs, base=1.4)


def casing_widths(*pairs):
    return zoom_interp(*[(z, round(w * 1.3 + 0.6, 2)) for z, w in pairs], base=1.4)


def build_layers(p):
    minor_kind = ["any", ["==", "kind", "minor_road"], ["==", "kind_detail", "living_street"]]
    major_kind = ["==", "kind", "major_road"]
    highway_kind = ["==", "kind", "highway"]

    minor_w = [(12, 0.6), (14, 2.0), (16, 5.0), (18, 12.0)]
    major_w = [(6, 0.5), (10, 1.2), (12, 2.0), (14, 4.0), (16, 8.0), (18, 16.0)]
    highway_w = [(3, 0.4), (6, 0.8), (10, 2.0), (12, 3.0), (14, 6.0), (16, 10.0), (18, 20.0)]

    return [
        layer("background", "background", paint={"background-color": p["background"]}),

        # --- land cover / land use ---------------------------------------
        fill("landcover-farmland", "landcover", p["farmland"], kinds("farmland", "grassland", "scrub")),
        fill("landcover-forest", "landcover", p["forest"], kinds("forest")),
        fill("landcover-urban", "landcover", p["urban"], kinds("urban_area")),
        fill("landcover-barren", "landcover", p["sand"], kinds("barren", "glacier")),

        fill("landuse-farmland", "landuse", p["farmland"], kinds("farmland", "meadow", "grassland", "scrub")),
        fill("landuse-forest", "landuse", p["forest"], kinds("forest", "wood")),
        fill("landuse-park", "landuse", p["park"], kinds(
            "park", "national_park", "nature_reserve", "garden", "grass", "village_green",
            "recreation_ground", "golf_course", "allotments", "zoo", "playground", "pitch")),
        fill("landuse-wetland", "landuse", p["wetland"], kinds("wetland")),
        fill("landuse-cemetery", "landuse", p["cemetery"], kinds("cemetery")),
        fill("landuse-sand", "landuse", p["sand"], kinds("sand", "bare_rock", "glacier")),
        fill("landuse-industrial", "landuse", p["industrial"], kinds("industrial", "commercial", "railway", "military")),
        fill("landuse-institution", "landuse", p["institution"], kinds(
            "school", "college", "university", "kindergarten", "hospital")),
        fill("landuse-airport", "landuse", p["airport"], kinds("aerodrome", "airfield")),
        fill("landuse-plaza", "landuse", p["plaza"], kinds("pedestrian", "platform", "pier")),

        # --- water -----------------------------------------------------
        fill("water", "water", p["water"], kinds("lake", "ocean", "sea", "water", "basin", "swimming_pool")),
        line("water-river", "water", p["water_line"],
             zoom_interp((9, 0.6), (12, 1.5), (14, 3.0), (18, 8.0), base=1.4),
             kinds("river", "canal"), minzoom=9),
        line("water-stream", "water", p["water_line"],
             zoom_interp((13, 0.5), (16, 1.5), (18, 3.0)),
             kinds("stream", "drain"), minzoom=13),

        # --- buildings -------------------------------------------------
        fill("buildings", "buildings", p["building"], kinds("building"),
             minzoom=13, outline=p["building_outline"]),

        # --- rail, paths, roads (bottom to top) ------------------------
        line("rail", "roads", p["rail"], zoom_interp((10, 0.5), (14, 1.2), (18, 2.5)),
             ["in", "kind_detail", "rail", "light_rail", "tram", "funicular"],
             minzoom=10, dash=[3, 2]),
        line("path", "roads", p["path"], zoom_interp((14, 0.6), (18, 2.0)),
             ["all", ["==", "kind", "path"],
              ["!in", "kind_detail", "cycleway", "sidewalk", "crossing", "steps"]],
             minzoom=14, dash=[2, 1.5]),
        line("cycleway", "roads", p["cycleway"], zoom_interp((13, 0.6), (16, 1.6), (18, 3.0)),
             ["==", "kind_detail", "cycleway"], minzoom=13, dash=[3, 1.5]),
        line("aeroway", "roads", p["runway"], zoom_interp((10, 1.0), (14, 8.0), (18, 30.0)),
             ["==", "kind", "aeroway"], minzoom=10),

        line("road-minor-casing", "roads", p["road_minor_casing"], casing_widths(*minor_w),
             minor_kind, minzoom=13),
        line("road-minor", "roads", p["road_minor"], road_widths(*minor_w),
             minor_kind, minzoom=12),
        line("road-major-casing", "roads", p["road_major_casing"], casing_widths(*major_w),
             major_kind, minzoom=11),
        line("road-major", "roads", p["road_major"], road_widths(*major_w),
             major_kind, minzoom=6),
        line("road-highway-casing", "roads", p["road_highway_casing"], casing_widths(*highway_w),
             highway_kind, minzoom=9),
        line("road-highway", "roads", p["road_highway"], road_widths(*highway_w),
             highway_kind, minzoom=3),

        # --- boundaries ------------------------------------------------
        line("boundary-region", "boundaries", p["boundary_region"],
             zoom_interp((3, 0.4), (10, 1.0)), kinds("region"), maxzoom=12, dash=[4, 2]),
        line("boundary-country", "boundaries", p["boundary_country"],
             zoom_interp((2, 0.6), (10, 1.6)), kinds("country")),

        # --- labels ----------------------------------------------------
        label("label-water", "water", p["water_text"], p["text_halo"],
              zoom_interp((10, 11), (16, 14)),
              ["all", ["has", "name"], kinds("lake", "ocean", "sea", "water")],
              minzoom=10, max_width=8),
        label("label-road-highway", "roads", p["road_text"], p["road_text_halo"], 11,
              ["all", ["has", "name"], highway_kind], minzoom=11, placement="line"),
        label("label-road-major", "roads", p["road_text"], p["road_text_halo"], 11,
              ["all", ["has", "name"], major_kind], minzoom=13, placement="line"),
        label("label-road-minor", "roads", p["road_text"], p["road_text_halo"], 10.5,
              ["all", ["has", "name"], minor_kind], minzoom=15, placement="line"),

        label("label-place-neighbourhood", "places", p["text_muted"], p["text_halo"],
              zoom_interp((12, 10), (16, 13)),
              ["in", "kind", "neighbourhood", "macrohood"], minzoom=12, transform="uppercase",
              max_width=8),
        label("label-place-village", "places", p["text"], p["text_halo"],
              zoom_interp((11, 11), (15, 15)),
              ["in", "kind_detail", "village", "hamlet", "isolated_dwelling", "locality"],
              minzoom=11, max_width=8),
        label("label-place-town", "places", p["text"], p["text_halo"],
              zoom_interp((8, 12), (13, 17)),
              ["==", "kind_detail", "town"], minzoom=8, max_width=8),
        label("label-place-city", "places", p["text"], p["text_halo"],
              zoom_interp((5, 12), (10, 18), (14, 22)),
              ["==", "kind_detail", "city"], minzoom=5, max_width=8, halo_width=2),
        label("label-country", "places", p["text_muted"], p["text_halo"],
              zoom_interp((2, 11), (6, 15)),
              ["==", "kind", "country"], minzoom=2, maxzoom=8, transform="uppercase",
              max_width=8),
    ]


def build_style(name, palette, tiles_url):
    style = {
        "version": 8,
        # id + metadata.version are cache keys in vector_map_tiles: id must be
        # unique per style, version must change whenever the style changes.
        "id": f"mobility4bw-{name}",
        "name": f"Mobility4BW {name}",
        "sources": {
            SOURCE: {
                "type": "vector",
                "tiles": [tiles_url],
                "minzoom": 0,
                "maxzoom": MAX_ZOOM,
            }
        },
        "layers": build_layers(palette),
    }
    digest = hashlib.sha256(json.dumps(style, sort_keys=True).encode()).hexdigest()[:10]
    style["metadata"] = {"version": digest}
    return style


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tiles-url", required=True,
                        help="tile URL template, e.g. http://HOST/tiles/bw/{z}/{x}/{y}.mvt")
    parser.add_argument("--out", required=True, help="output directory (created if missing)")
    args = parser.parse_args()

    if "{z}" not in args.tiles_url or "{x}" not in args.tiles_url or "{y}" not in args.tiles_url:
        parser.error("--tiles-url must contain {z}, {x} and {y}")

    os.makedirs(args.out, exist_ok=True)
    for name, palette in PALETTES.items():
        path = os.path.join(args.out, f"{name}.json")
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(build_style(name, palette, args.tiles_url), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o644)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
