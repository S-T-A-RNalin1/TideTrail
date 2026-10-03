"""Operator data: radar scenes, AIS files and optical chips brought in at run time.

The demo scenes are prepared in advance, but an analyst on duty works on the
pass that came in this morning, over their own sea, against their own AIS
feed. This module takes those files, checks them the way the prepared scenes
were checked, and registers them so the ordinary pipeline runs on them
unchanged.

Uploads live under data/uploads with their own index, so an analyst's files
never mix into the tracked demo scene list.

What each upload must carry, and why:

  radar    a georeferenced Sigma0 GeoTIFF (dB or linear) and the pass time.
           Without a georeference nothing downstream has a position; without
           the pass time the drift and the AIS window are anchored to nothing,
           and guessing "now" would quietly produce a wrong answer.
  AIS      a CSV in the MarineCadastre columns (MMSI, BaseDateTime, LAT, LON,
           SOG, COG, Heading, VesselName, ...), the format the problem
           statement points to. Positions are stored with the file name as
           their source, so every lead says which feed it came from.
  optical  an RGB GeoTIFF over the same sea, with its acquisition time. It is
           used only to corroborate detections, as the prepared Sentinel-2
           chips are.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import config, scenes as scenes_mod
from .ais import ingest
from .drift import land
from .geo import raster as raster_mod

MAX_SIDE_PX = 4096           # about 40 km at 10 m; crop larger passes first
MIN_SIDE_PX = 256
LAND_MARGIN_DEG = 1.5
OPTICAL_MAX_PX = 1600
REQUIRED_AIS = ("MMSI", "BaseDateTime", "LAT", "LON")


class UploadError(ValueError):
    """A file that cannot be used, with a sentence saying why."""


def upload_dir() -> Path:
    d = Path(config.DATA_DIR) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "upload").name).strip("._") or "upload"
    return stem[:80]


def parse_time(value: Optional[str], what: str) -> datetime:
    if not value or not str(value).strip():
        raise UploadError("The %s is required, in UTC (for example 2025-05-28T00:41:25Z)." % what)
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        t = datetime.fromisoformat(s)
    except ValueError as exc:
        raise UploadError("Could not read the %s %r; use ISO 8601 in UTC." % (what, value)) from exc
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc)
    if t > datetime.now(timezone.utc) + timedelta(hours=1):
        raise UploadError("The %s %s is in the future." % (what, t.strftime("%Y-%m-%d %H:%M UTC")))
    if t.year < 1991:
        raise UploadError("The %s %s is before the first civil radar satellite." % (what, t.date()))
    return t


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Radar
# ---------------------------------------------------------------------------

def register_sar(src: Path, original_name: str, t_sat: Optional[str], title: Optional[str] = None) -> Dict[str, Any]:
    """Check an uploaded radar chip and add it to the scene list.

    Returns the scene and the notes an operator needs before running it: which
    AIS covers it, whether there is a coastline, whether weather is cached.
    """
    try:
        sar = raster_mod.load_sar(src)
    except raster_mod.GeorefError as exc:
        raise UploadError("%s has no usable georeference (affine transform and CRS). Export it "
                          "as a GeoTIFF with its projection, for example from SNAP or GDAL." % original_name) from exc
    except Exception as exc:
        raise UploadError("Could not read %s as a GeoTIFF: %s" % (original_name, exc)) from exc

    h, w = sar.array.shape[-2:]
    if max(h, w) > MAX_SIDE_PX:
        raise UploadError("%s is %d x %d pixels. Crop it to the area of interest, at most %d on a side "
                          "(about 40 km at 10 m pixels), so detection runs in about a minute."
                          % (original_name, w, h, MAX_SIDE_PX))
    if min(h, w) < MIN_SIDE_PX:
        raise UploadError("%s is only %d x %d pixels; the detector needs at least %d on a side."
                          % (original_name, w, h, MIN_SIDE_PX))
    finite = np.isfinite(sar.array)
    if finite.mean() < 0.2:
        raise UploadError("%s is mostly empty (%.0f%% of pixels have a value)." % (original_name, finite.mean() * 100))
    px_m = float(np.sqrt(sar.pixel_area_km2()) * 1000.0)
    if not 2.0 <= px_m <= 200.0:
        raise UploadError("%s has %.0f m pixels. The detector expects radar at 5 to 50 m, like "
                          "Sentinel-1 or RISAT-1A GRD." % (original_name, px_m))

    meta_time = scenes_mod._time_from_meta(sar)
    t = parse_time(t_sat or meta_time, "radar pass time")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    scene_id = "upload_%s_%s" % (stamp, re.sub(r"[^a-z0-9]+", "_", Path(original_name).stem.lower())[:40].strip("_"))
    dest = upload_dir() / ("%s.tif" % scene_id)
    shutil.move(str(src), dest)

    scene = scenes_mod.describe_geotiff(dest, scene_id=scene_id, title=title or Path(original_name).stem,
                                        t_sat=_iso(t), source="uploaded: %s" % original_name,
                                        license_="operator supplied", notes="uploaded by an operator")
    scene.ais_mode = ais_mode_for(scene.bounds, t)
    notes = [_ais_note(scene.ais_mode)]
    notes.append(ensure_coastline(scene))
    notes.append(_metocean_note(scene, t))
    scenes_mod.save_upload(scene)
    return {"scene": scene.to_dict(), "notes": [n for n in notes if n], "pixel_m": round(px_m, 1),
            "size": [int(w), int(h)]}


def ais_mode_for(bounds, t: datetime) -> str:
    """"real" when stored AIS (recorded or uploaded) covers the scene around its pass."""
    w, s, e, n = bounds
    conn = ingest.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE source != 'simulated_traffic' AND lon BETWEEN ? AND ? "
            "AND lat BETWEEN ? AND ? AND ts BETWEEN ? AND ?",
            (w - 0.5, e + 0.5, s - 0.5, n + 0.5, int(t.timestamp()) - 48 * 3600, int(t.timestamp()) + 6 * 3600),
        ).fetchone()
    finally:
        conn.close()
    return "real" if row and row[0] > 0 else "none"


def _ais_note(mode: str) -> str:
    if mode == "real":
        return "AIS on file covers this sea around the pass; attribution will use it."
    return ("No AIS on file covers this sea around the pass. Upload an AIS CSV for it, or the "
            "run will detect and drift the oil but rank no vessels.")


def _metocean_note(scene, t: datetime) -> str:
    from .drift import fields as fields_mod
    clon, clat = scene.centroid
    for item in fields_mod.list_cached():
        wb, sb, eb, nb = item["bounds"]
        if sb <= clat <= nb and wb <= clon <= eb:
            t0 = datetime.fromisoformat(item["t_start"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(item["t_end"].replace("Z", "+00:00"))
            if t0 <= t - timedelta(hours=config.HINDCAST_H) and t + timedelta(hours=config.FORECAST_H) <= t1:
                return ""
    if config.OFFLINE:
        return ("No wind or current data is cached for this place and time, so the drift will use a "
                "flagged constant field. On a networked machine, fetch the real data with "
                "python scripts/build_metocean_cache.py --scene %s" % scene.id)
    return ("No wind or current data is cached for this place and time yet; use Fetch weather "
            "before running.")


def fetch_metocean(scene_id: str) -> Dict[str, Any]:
    """Fetch and cache wind and currents for an uploaded scene. Needs the network."""
    if config.OFFLINE:
        raise UploadError("This console runs offline (TIDETRACE_OFFLINE=1), so it cannot fetch weather. "
                          "Run python scripts/build_metocean_cache.py --scene %s on a networked machine "
                          "and copy data/metocean across." % scene_id)
    scene = scenes_mod.get(scene_id)
    if scene is None:
        raise UploadError("Unknown scene %r." % scene_id)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_metocean_fetch", config.ROOT_DIR / "scripts" / "build_metocean_cache.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    t = datetime.fromisoformat(scene.t_sat.replace("Z", "+00:00"))
    clon, clat = scene.centroid
    return mod.build_box(scene.id, clat, clon, t, half_deg=0.6, n=13,
                         back_h=int(config.HINDCAST_H) + 24, fwd_h=int(config.FORECAST_H) + 12)


def ensure_coastline(scene) -> str:
    """Make sure the land mask covers an uploaded scene.

    The shipped coastline is Natural Earth 1:10m around the prepared scenes.
    Anywhere else the bundled world file (1:50m, simplified to about 2 km) is
    clipped in. It is too coarse for harbours, and an inland sea it does not
    carry, the Caspian for one, would read as solid land, so a chip that comes
    out almost entirely ashore is left unmasked instead, with a warning.
    """
    w, s, e, n = scene.bounds
    box = (w - LAND_MARGIN_DEG, s - LAND_MARGIN_DEG, e + LAND_MARGIN_DEG, n + LAND_MARGIN_DEG)
    path = Path(config.DATA_DIR) / "land" / "coastline.geojson"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"type": "FeatureCollection", "features": []}

    def overlaps(f):
        g = f.get("geometry") or {}
        coords = g.get("coordinates") or []
        polys = coords if g.get("type") == "MultiPolygon" else [coords]
        for poly in polys:
            if not poly:
                continue
            xs = [p[0] for p in poly[0]]
            ys = [p[1] for p in poly[0]]
            if not (max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n):
                return True
        return False

    if any(overlaps(f) for f in doc.get("features", [])):
        return ""
    try:
        world = json.loads((Path(config.DATA_DIR) / "land" / "world_land_detail.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "No coastline data for this sea, so land is not masked and oil can drift ashore unchecked."

    feats = []
    for ring in world.get("rings", []):
        clipped = _clip_ring(ring, box)
        if len(clipped) >= 4:
            if clipped[0] != clipped[-1]:
                clipped.append(clipped[0])
            feats.append({"type": "Feature",
                          "properties": {"scene": scene.id, "kind": "land", "source": "Natural Earth 1:50m"},
                          "geometry": {"type": "Polygon", "coordinates": [clipped]}})
    if not feats:
        return ""           # open ocean: nothing to mask, and nothing is missing
    lon = np.linspace(w, e, 24)
    lat = np.linspace(s, n, 24)
    X, Y = np.meshgrid(lon, lat)
    ashore = np.zeros(X.shape, dtype=bool)
    for f in feats:
        ashore |= land._ring_contains(X, Y, [tuple(p) for p in f["geometry"]["coordinates"][0]])
    if ashore.mean() > 0.9:
        return ("The only coastline available here puts %.0f%% of the chip on land, which usually means an "
                "inland sea the world file does not carry. Land is not masked for this scene."
                % (ashore.mean() * 100))
    doc.setdefault("features", []).extend(feats)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    land.reload()
    return ("Coastline here is Natural Earth 1:50m, accurate to about 2 km; harbours and river mouths "
            "read as land.")


def _clip_ring(ring, box) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman against an axis-aligned box."""
    w, s, e, n = box
    tests = (lambda p: p[0] >= w, lambda p: p[0] <= e, lambda p: p[1] >= s, lambda p: p[1] <= n)

    def cross(a, b, k):
        (ax, ay), (bx, by) = a, b
        if k < 2:
            x = w if k == 0 else e
            t = (x - ax) / (bx - ax) if bx != ax else 0.0
            return (x, ay + t * (by - ay))
        y = s if k == 2 else n
        t = (y - ay) / (by - ay) if by != ay else 0.0
        return (ax + t * (bx - ax), y)

    out = [(float(p[0]), float(p[1])) for p in ring]
    for k, inside in enumerate(tests):
        if not out:
            return []
        nxt = []
        for i in range(len(out)):
            cur, prev = out[i], out[i - 1]
            if inside(cur):
                if not inside(prev):
                    nxt.append(cross(prev, cur, k))
                nxt.append(cur)
            elif inside(prev):
                nxt.append(cross(prev, cur, k))
        out = nxt
    return out


def remove_scene(scene_id: str) -> None:
    scene = scenes_mod.get(scene_id)
    if scene is None or not str(scene.source or "").startswith("uploaded"):
        raise UploadError("Only uploaded scenes can be removed, and %r is not one." % scene_id)
    scenes_mod.remove_upload(scene_id)
    for p in (Path(scene.sar_path), Path(config.DATA_DIR) / "optical" / ("%s.png" % scene_id),
              Path(config.DATA_DIR) / "optical" / ("%s.json" % scene_id)):
        try:
            p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# AIS
# ---------------------------------------------------------------------------

def ingest_ais(src: Path, original_name: str) -> Dict[str, Any]:
    """Load an AIS CSV and say what it covers."""
    try:
        with src.open("r", newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh), [])
    except (OSError, UnicodeDecodeError) as exc:
        raise UploadError("Could not read %s as a text CSV: %s" % (original_name, exc)) from exc
    have = {h.strip().lower() for h in header}
    missing = [c for c in REQUIRED_AIS if c.lower() not in have]
    if missing:
        raise UploadError("%s is missing the column%s %s. The file needs the MarineCadastre columns: "
                          "MMSI, BaseDateTime, LAT, LON, and ideally SOG, COG, Heading, VesselName, VesselType."
                          % (original_name, "" if len(missing) == 1 else "s", ", ".join(missing)))
    source = "upload:%s" % _safe(original_name)
    conn = ingest.connect()
    try:
        ingest.clear_source(conn, source)
        n = ingest.ingest_csv(src, conn=conn, source=source)
        if n == 0:
            raise UploadError("%s has the right columns but no row with a valid MMSI, time and position." % original_name)
        r = conn.execute("SELECT COUNT(DISTINCT mmsi), MIN(ts), MAX(ts), MIN(lon), MIN(lat), MAX(lon), MAX(lat) "
                         "FROM positions WHERE source = ?", (source,)).fetchone()
    finally:
        conn.close()

    covered = []
    for sc in scenes_mod.load_uploads():
        t = datetime.fromisoformat(sc.t_sat.replace("Z", "+00:00"))
        mode = ais_mode_for(sc.bounds, t)
        if mode != sc.ais_mode:
            sc.ais_mode = mode
            scenes_mod.save_upload(sc)
        if mode == "real":
            covered.append(sc.title)
    return {
        "source": source, "rows": int(n), "vessels": int(r[0]),
        "t_start": ingest.iso(int(r[1])) + "Z", "t_end": ingest.iso(int(r[2])) + "Z",
        "bbox": [round(float(v), 4) for v in r[3:7]],
        "covers_uploaded_scenes": covered,
    }


# ---------------------------------------------------------------------------
# Optical
# ---------------------------------------------------------------------------

def register_optical(src: Path, original_name: str, scene_id: str, acquired: Optional[str]) -> Dict[str, Any]:
    """Resample an RGB GeoTIFF onto a scene's footprint for the optical cross-check."""
    scene = scenes_mod.get(scene_id)
    if scene is None:
        raise UploadError("Unknown scene %r." % scene_id)
    t_opt = parse_time(acquired, "optical acquisition time")
    t_sar = datetime.fromisoformat(scene.t_sat.replace("Z", "+00:00"))
    try:
        img = raster_mod.load_sar(src)
    except raster_mod.GeorefError as exc:
        raise UploadError("%s has no usable georeference." % original_name) from exc
    except Exception as exc:
        raise UploadError("Could not read %s as a GeoTIFF: %s" % (original_name, exc)) from exc
    arr = np.asarray(img.array, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[None]
    rgb = arr[:3] if arr.shape[0] >= 3 else np.repeat(arr[:1], 3, axis=0)

    w, s, e, n = scene.bounds
    side = OPTICAL_MAX_PX
    cols, rows = np.meshgrid(np.arange(side) + 0.5, np.arange(side) + 0.5)
    lon = w + cols / side * (e - w)
    lat = n - rows / side * (n - s)
    try:
        from pyproj import Transformer
        x, y = Transformer.from_crs("EPSG:4326", img.crs, always_xy=True).transform(lon, lat)
    except Exception:
        x, y = lon, lat
    a, b, c, d, e_, f = img.transform
    det = a * e_ - b * d
    col = (e_ * (x - c) - b * (y - f)) / det
    row = (-d * (x - c) + a * (y - f)) / det
    inside = (col >= 0) & (row >= 0) & (col < rgb.shape[2]) & (row < rgb.shape[1])
    if inside.mean() < 0.3:
        raise UploadError("%s covers only %.0f%% of the radar scene; it needs to overlap it."
                          % (original_name, inside.mean() * 100))
    out = np.zeros((side, side, 3), dtype=np.uint8)
    ci, ri = col[inside].astype(int), row[inside].astype(int)
    for k in range(3):
        band = rgb[k]
        good = band[np.isfinite(band)]
        lo, hi = (np.percentile(good, 2), np.percentile(good, 98)) if good.size else (0.0, 1.0)
        v = (band[ri, ci] - lo) / max(hi - lo, 1e-6)
        out[..., k][inside] = np.clip(v * 255, 0, 255).astype(np.uint8)

    from PIL import Image
    odir = Path(config.DATA_DIR) / "optical"
    odir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(odir / ("%s.png" % scene_id))
    offset_h = (t_opt - t_sar).total_seconds() / 3600.0
    meta = {
        "scene": scene_id, "status": "ok", "url": "/data/optical/%s.png" % scene_id,
        "bounds": [[s, w], [n, e]], "size": [side, side], "decimation": 1,
        "item_id": original_name, "acquired": _iso(t_opt), "sar_acquired": scene.t_sat,
        "offset_hours": round(offset_h, 2),
        "offset_label": "%.0f h %s the radar pass" % (abs(offset_h), "after" if offset_h >= 0 else "before"),
        "cloud_percent": None, "collection": "operator upload", "license": "operator supplied",
        "coverage": round(float(inside.mean()), 3),
    }
    (odir / ("%s.json" % scene_id)).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
