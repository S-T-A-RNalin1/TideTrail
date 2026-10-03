"""Optional coastline check for the forecast cone.

The spec is explicit that the land mask is optional and that the step is skipped
when it is missing. So this module never fails: with no coastline file present
it reports `available: False` and the pipeline moves on.

Supply one by dropping a GeoJSON of land or coastline polygons at
`data/land/coastline.geojson`. scripts/fetch_land_mask.py builds it from
Natural Earth 1:10m. Polygons keep their holes: a point is
ashore when it is inside a polygon and outside that polygon's holes, so a lake
inside land and an island inside that lake both come out right.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .. import config
from ..geo.geometry import point_in_ring

_CACHE: Dict[str, Any] = {"loaded": False, "rings": None, "water": None,
                          "polys": None, "path": None}


def _candidate_paths() -> List[Path]:
    base = Path(config.DATA_DIR) / "land"
    return [base / "coastline.geojson", base / "land.geojson"] + sorted(base.glob("*.geojson"))


def load_rings() -> Optional[List[List[Tuple[float, float]]]]:
    """Flatten every polygon in the land file into a list of exterior rings."""
    if _CACHE["loaded"]:
        return _CACHE["rings"]
    _CACHE["loaded"] = True

    for p in _candidate_paths():
        if not p.exists():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rings: List[List[Tuple[float, float]]] = []
        water: List[List[Tuple[float, float]]] = []
        polys: List[Tuple[List[Tuple[float, float]], List[List[Tuple[float, float]]]]] = []
        feats = doc.get("features", [doc]) if isinstance(doc, dict) else []
        for f in feats:
            geom = (f or {}).get("geometry", f)
            if not geom:
                continue
            # Features may be tagged `kind: water`. Natural Earth models inland
            # seas as holes in the surrounding landmass, and the builder emits
            # those holes as water rather than dropping them -- without that the
            # Caspian is part of Eurasia and masking would erase a whole scene.
            props = (f or {}).get("properties") or {}
            is_water = props.get("kind") == "water"
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            parts = [coords] if gtype == "Polygon" else (coords if gtype == "MultiPolygon" else [])
            for poly in parts:
                if not poly:
                    continue
                ext = [(float(x), float(y)) for x, y in poly[0]]
                if is_water:
                    water.append(ext)
                else:
                    rings.append(ext)
                    polys.append((ext, [[(float(x), float(y)) for x, y in h] for h in poly[1:]]))
        if rings:
            _CACHE["rings"] = rings
            _CACHE["water"] = water
            _CACHE["polys"] = polys
            _CACHE["path"] = str(p)
            return rings
    _CACHE["rings"] = None
    _CACHE["water"] = None
    _CACHE["polys"] = None
    return None


def describe_source() -> str:
    """What the land mask was built from, for the run record."""
    if not load_rings():
        return "none"
    try:
        doc = json.loads(Path(_CACHE["path"]).read_text(encoding="utf-8"))
    except Exception:
        return "coastline file"
    return "Natural Earth 1:10m" if "Natural Earth" in str(doc.get("license", "")) else Path(_CACHE["path"]).name


def reload() -> None:
    """Forget the cached coastline, after the file on disk has changed."""
    _CACHE.update({"loaded": False, "rings": None, "water": None, "polys": None, "path": None})
    _TILES.clear()
    _EDGES.clear()


def load_polygons() -> List[Tuple[List[Tuple[float, float]], List[List[Tuple[float, float]]]]]:
    """Land polygons as (exterior, holes)."""
    load_rings()
    return _CACHE.get("polys") or []


def load_water_rings() -> List[List[Tuple[float, float]]]:
    """Rings that are water even though they sit inside a land polygon."""
    load_rings()
    return _CACHE.get("water") or []


def is_land(lon: float, lat: float) -> bool:
    """True when a point is on land, honouring inland seas.

    Used to keep detections off the shore. A dark patch on a hillside is radar
    shadow, not a slick, and before this existed the detector was free to report
    one: a scene over the Santa Barbara mountains produced polygons across the
    ridge line.
    """
    if not load_rings():
        return False
    ashore = any(point_in_ring(lon, lat, ext) and not any(point_in_ring(lon, lat, h) for h in holes)
                 for ext, holes in load_polygons())
    return ashore and not any(point_in_ring(lon, lat, r) for r in load_water_rings())


def _segments_cross(a, b, c, d) -> bool:
    """Do segments ab and cd properly intersect?"""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _rings_intersect(cone: Sequence[Tuple[float, float]],
                     land: Sequence[Tuple[float, float]]) -> bool:
    """True polygon overlap, not a bounding box near miss.

    Three cases have to be covered or the flag is wrong in a way that matters:
    the cone sits inside the landmass, the landmass sits inside the cone, or
    their boundaries cross. Testing land vertices against the cone's bounding
    box, which is what an earlier version did, reports a hit whenever a coast is
    merely nearby, and a false coastal impact warning is worse than none.
    """
    if len(cone) < 3 or len(land) < 3:
        return False

    lon_c = [p[0] for p in cone]
    lat_c = [p[1] for p in cone]
    lon_l = [p[0] for p in land]
    lat_l = [p[1] for p in land]
    if (max(lon_l) < min(lon_c) or min(lon_l) > max(lon_c)
            or max(lat_l) < min(lat_c) or min(lat_l) > max(lat_c)):
        return False

    if any(point_in_ring(x, y, land) for x, y in cone):
        return True
    if any(point_in_ring(x, y, cone) for x, y in land):
        return True

    for i in range(len(cone) - 1):
        a, b = cone[i], cone[i + 1]
        for j in range(len(land) - 1):
            if _segments_cross(a, b, land[j], land[j + 1]):
                return True
    return False


def check(cone_ring: Sequence[Tuple[float, float]],
          bbox: Dict[str, float]) -> Dict[str, Any]:
    """Does the forecast cone reach land? Returns a skip result if unavailable."""
    rings = load_rings()
    if not rings:
        return {
            "available": False,
            "coast_flag": None,
            "note": "No land mask present. Run scripts/build_land_mask.py, or drop a "
                    "GeoJSON at data/land/coastline.geojson, to enable the coast "
                    "impact flag. This step is optional by design.",
        }

    cone = [(float(x), float(y)) for x, y in cone_ring]
    if cone and cone[0] != cone[-1]:
        cone = cone + [cone[0]]

    # Only true land counts as coastline here. The water rings exist to punch
    # inland seas back out of the landmass, and treating them as shore would
    # raise a coast-impact flag in the middle of open water.
    hits = [i for i, ring in enumerate(rings) if _rings_intersect(cone, ring)]
    return {
        "available": True,
        "source": _CACHE["path"],
        "polygons_checked": len(rings),
        "coast_flag": bool(hits),
        "polygons_intersected": len(hits),
        "note": ("Forecast cone overlaps land within the forecast horizon." if hits
                 else "Forecast cone stays offshore over the forecast horizon."),
    }


def _ring_contains(lon: np.ndarray, lat: np.ndarray,
                   ring: Sequence[Tuple[float, float]]) -> np.ndarray:
    """Vectorised even-odd ray cast for a whole grid against one ring."""
    xs = np.asarray([p[0] for p in ring], dtype=np.float64)
    ys = np.asarray([p[1] for p in ring], dtype=np.float64)
    inside = np.zeros(lon.shape, dtype=bool)
    x1, y1 = xs[-1], ys[-1]
    for x2, y2 in zip(xs, ys):
        if y1 != y2:
            straddles = (lat >= np.minimum(y1, y2)) & (lat < np.maximum(y1, y2))
            if straddles.any():
                cross_x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                inside ^= straddles & (lon < cross_x)
        x1, y1 = x2, y2
    return inside


def mask_for_raster(sar, step: int = 8) -> Optional[np.ndarray]:
    """Boolean land mask on a scene's own pixel grid, or None with no coastline.

    A radar chip that contains coast contains dark pixels that are not slicks:
    radar shadow behind a ridge, a sheltered harbour basin, wet ground. Dropping
    polygons whose centroid is ashore, which is what the pipeline did on its
    own, cannot catch a single polygon that straddles the shoreline and takes
    the whole coastal strip with it -- on the Santa Barbara chip that was worth
    136 km2 of reported oil against a real 0.4.

    Land is therefore removed before detection rather than after. The mask is
    evaluated on a decimated grid and expanded back, because a shoreline placed
    to within `step` pixels is far finer than the coastline data itself.
    """
    rings = load_rings()
    if not rings:
        return None
    h, w = sar.array.shape[-2:]
    step = max(1, int(step))
    rows = np.arange(0, h, step, dtype=np.float64)
    cols = np.arange(0, w, step, dtype=np.float64)
    cc, rr = np.meshgrid(cols + 0.5, rows + 0.5)

    a, b, c, d, e, f = sar.transform
    x = a * cc + b * rr + c
    y = d * cc + e * rr + f
    from ..geo import raster as raster_mod

    try:
        lon, lat = raster_mod.to_wgs84(x, y, sar.crs)
    except Exception:
        return None

    land_hit = np.zeros(lon.shape, dtype=bool)
    w0, s0, e0, n0 = float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())
    for ext, holes in load_polygons():
        if len(ext) < 3:
            continue
        xs = [q[0] for q in ext]
        ys = [q[1] for q in ext]
        if max(xs) < w0 or min(xs) > e0 or max(ys) < s0 or min(ys) > n0:
            continue
        hit = _ring_contains(lon, lat, ext)
        for h in holes:
            if len(h) >= 3:
                hit &= ~_ring_contains(lon, lat, h)
        land_hit |= hit
    for ring in load_water_rings():          # lakes and inlets punched back out
        if len(ring) >= 3:
            land_hit &= ~_ring_contains(lon, lat, ring)
    if not land_hit.any():
        return None

    full = np.repeat(np.repeat(land_hit, step, axis=0), step, axis=1)
    return full[:h, :w]


# ---------------------------------------------------------------------------
# Fast point test for drifting particles
# ---------------------------------------------------------------------------
# The drift asks "is this particle ashore" for every particle at every step,
# so the coastline is rasterised once into small tiles and looked up. At
# 0.0005 degrees a cell is about 50 m, far finer than the coastline itself.

TILE_DEG = 0.25
CELL_DEG = 0.0005
_TILES: Dict[Tuple[int, int], np.ndarray] = {}
_EDGES: Dict[str, Any] = {}


def _ring_edges() -> Dict[str, Any]:
    if "land" in _EDGES:
        return _EDGES

    def pack(rings):
        out = []
        for r in rings or []:
            a = np.asarray(r, dtype=np.float64)
            if len(a) < 3:
                continue
            b = np.roll(a, -1, axis=0)
            keep = a[:, 1] != b[:, 1]
            out.append((a[keep], b[keep], (a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max())))
        return out

    _EDGES["land"] = [(pack([ext])[0], pack(holes)) for ext, holes in load_polygons() if pack([ext])]
    _EDGES["water"] = pack(load_water_rings())
    return _EDGES


def _fill(edges, west: float, north: float, n: int) -> np.ndarray:
    """Even-odd fill of one ring on an n x n tile, one scanline at a time."""
    a, b, _ = edges
    inside = np.zeros((n, n), dtype=bool)
    xs = west + (np.arange(n) + 0.5) * CELL_DEG
    ylo = np.minimum(a[:, 1], b[:, 1])
    yhi = np.maximum(a[:, 1], b[:, 1])
    for row in range(n):
        y = north - (row + 0.5) * CELL_DEG
        m = (y >= ylo) & (y < yhi)
        if not m.any():
            continue
        x1, y1, x2, y2 = a[m, 0], a[m, 1], b[m, 0], b[m, 1]
        cx = np.sort(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        right = cx.size - np.searchsorted(cx, xs, side="right")
        inside[row] = (right % 2) == 1
    return inside


def _tile(ix: int, iy: int) -> np.ndarray:
    key = (ix, iy)
    if key in _TILES:
        return _TILES[key]
    n = int(round(TILE_DEG / CELL_DEG))
    west, south = ix * TILE_DEG, iy * TILE_DEG
    east, north = west + TILE_DEG, south + TILE_DEG
    e = _ring_edges()

    def touches(bb):
        return not (bb[2] < west or bb[0] > east or bb[3] < south or bb[1] > north)

    grid = np.zeros((n, n), dtype=bool)
    for ring, holes in e["land"]:
        if touches(ring[2]):
            part = _fill(ring, west, north, n)
            for h in holes:
                if touches(h[2]):
                    part &= ~_fill(h, west, north, n)
            grid |= part
    if grid.any():
        for ring in e["water"]:
            if touches(ring[2]):
                grid &= ~_fill(ring, west, north, n)
    _TILES[key] = grid
    return grid


def on_land(lon, lat) -> np.ndarray:
    """Vectorised: which of these points are ashore. All False with no coastline."""
    lon = np.atleast_1d(np.asarray(lon, dtype=np.float64))
    lat = np.atleast_1d(np.asarray(lat, dtype=np.float64))
    out = np.zeros(lon.shape, dtype=bool)
    if not load_rings():
        return out
    ok = np.isfinite(lon) & np.isfinite(lat)
    ix = np.floor(lon / TILE_DEG).astype(np.int64)
    iy = np.floor(lat / TILE_DEG).astype(np.int64)
    n = int(round(TILE_DEG / CELL_DEG))
    for key in set(zip(ix[ok].tolist(), iy[ok].tolist())):
        sel = ok & (ix == key[0]) & (iy == key[1])
        g = _tile(*key)
        col = np.clip(((lon[sel] - key[0] * TILE_DEG) / CELL_DEG).astype(np.int64), 0, n - 1)
        row = np.clip((((key[1] + 1) * TILE_DEG - lat[sel]) / CELL_DEG).astype(np.int64), 0, n - 1)
        out[sel] = g[row, col]
    return out


def clip_to_water(ring: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """The part of a zone that is at sea, as one ring. Unchanged without shapely
    or without a coastline, or when the zone is entirely at sea."""
    rings = load_rings()
    if not rings or len(ring) < 4:
        return list(ring)
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError:
        return list(ring)
    zone = Polygon(ring).buffer(0)
    land_parts = [g for g in (Polygon(ext, [h for h in holes if len(h) >= 4]).buffer(0)
                              for ext, holes in load_polygons() if len(ext) >= 4) if g.intersects(zone)]
    if not land_parts:
        return list(ring)
    ground = unary_union(land_parts)
    water = [Polygon(r).buffer(0) for r in load_water_rings() if len(r) >= 4 and Polygon(r).intersects(zone)]
    if water:
        ground = ground.difference(unary_union(water))
    sea = zone.difference(ground)
    if sea.is_empty:
        return list(ring)
    if sea.geom_type == "MultiPolygon":
        sea = max(sea.geoms, key=lambda g: g.area)
    return [(float(x), float(y)) for x, y in sea.exterior.coords]
