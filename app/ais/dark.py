"""Ships the radar sees, checked against the AIS they should be broadcasting.

A steel hull is a strong corner reflector, so a ship at sea is a small cluster
of pixels far brighter than the water around it. Every such target in the
co-polarised band is compared with where AIS says each vessel was at the
moment of the radar pass. A target no AIS position explains is reported as
radar-only: a vessel with its transponder off, or a structure no map records.
The physics cannot say which from one pass, and the report says so.

Detection is a local-contrast test, the same idea as a CFAR detector:

    contrast = backscatter - median of the surrounding sea, both in dB
    target   = a connected cluster with contrast above TARGET_DB and a peak
               above PEAK_DB, between MIN_PIXELS and MAX_PIXELS in size,
               not on land and not within INSTALLATION_M of a known platform

Matching allows for the SAR azimuth shift: a ship moving towards or away from
the satellite is imaged displaced along the flight track, by hundreds of metres
at ordinary speeds, so a radar target and its AIS fix are paired within
MATCH_KM rather than on the same pixel.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from scipy import ndimage as ndi

from ..geo.crs import haversine_km
from ..ml import dataset as ds_mod
from . import ingest, vessel_types

TARGET_DB = 9.0
PEAK_DB = 13.0
BACKGROUND_PX = 61
MIN_PIXELS = 3
MAX_PIXELS = 1500
INSTALLATION_M = 300.0
MATCH_KM = 1.5
AIS_BRACKET_MIN = 30.0
LAND_GUARD_PX = 20


def detect_targets(sigma0_db: np.ndarray, raster, land_mask: Optional[np.ndarray] = None) -> List[Dict[str, Any]]:
    """Bright point targets in the co-polarised band, as lon/lat with their contrast."""
    a = ds_mod.order_bands(np.asarray(sigma0_db, dtype=np.float32))
    co = a[-1]
    valid = np.isfinite(co)
    filled = np.where(valid, co, np.nanmedian(co))
    smooth = ndi.uniform_filter(filled, size=3)
    background = ndi.median_filter(ndi.zoom(filled, 0.25, order=1), size=max(3, BACKGROUND_PX // 4))
    background = ndi.zoom(background, (co.shape[0] / background.shape[0], co.shape[1] / background.shape[1]), order=1)
    background = background[:co.shape[0], :co.shape[1]]
    contrast = smooth - background

    cand = (contrast > TARGET_DB) & valid
    if land_mask is not None and np.asarray(land_mask).any():
        cand &= ~ndi.binary_dilation(np.asarray(land_mask, dtype=bool), iterations=LAND_GUARD_PX)
    labels, n = ndi.label(cand)
    if n == 0:
        return []
    idx = np.arange(1, n + 1)
    sizes = ndi.sum(np.ones_like(contrast), labels, idx)
    peaks = ndi.maximum(contrast, labels, idx)
    peak_db = ndi.maximum(co, labels, idx)
    centres = ndi.center_of_mass(contrast.clip(min=0), labels, idx)
    slices = ndi.find_objects(labels)

    out: List[Dict[str, Any]] = []
    px_m = float(np.sqrt(raster.pixel_area_km2()) * 1000.0)
    for k in range(n):
        if not (MIN_PIXELS <= sizes[k] <= MAX_PIXELS) or peaks[k] < PEAK_DB:
            continue
        r, c = centres[k]
        lon, lat = raster.lonlat(float(c), float(r))   # lonlat adds the half-pixel itself
        sl = slices[k]
        extent_px = float(np.hypot(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start))
        out.append({
            "lon": round(float(np.atleast_1d(lon)[0]), 6),
            "lat": round(float(np.atleast_1d(lat)[0]), 6),
            "contrast_db": round(float(peaks[k]), 1),
            "peak_db": round(float(peak_db[k]), 1),
            "pixels": int(sizes[k]),
            "extent_m": round(extent_px * px_m),
        })
    out.sort(key=lambda t: -t["contrast_db"])
    return out


def drop_installations(targets: Sequence[Dict[str, Any]], installations: Sequence[Dict[str, Any]]):
    """Remove targets sitting on a known platform; return (kept, removed count)."""
    if not installations:
        return list(targets), 0
    ilat = np.array([float(i["lat"]) for i in installations])
    ilon = np.array([float(i["lon"]) for i in installations])
    kept, removed = [], 0
    for t in targets:
        d = np.asarray(haversine_km(t["lat"], t["lon"], ilat, ilon), dtype=float)
        if d.size and float(d.min()) * 1000.0 <= INSTALLATION_M:
            removed += 1
        else:
            kept.append(t)
    return kept, removed


def ais_at(conn, bbox: Sequence[float], t: datetime) -> List[Dict[str, Any]]:
    """Every vessel's position at time t, interpolated between the fixes around it.

    A vessel counts only if it has a fix on each side of t within
    AIS_BRACKET_MIN, or one within five minutes. Anything sparser would place
    a ship where it may not have been, and a false position here reads as a
    radar target with no AIS.
    """
    ts = int(t.timestamp())
    pad = int(AIS_BRACKET_MIN * 60)
    w, s, e, n = bbox
    grouped = ingest.query_window(conn, (w - 0.1, s - 0.1, e + 0.1, n + 0.1), ts - pad, ts + pad)
    out = []
    for mmsi, rows in grouped.items():
        before = [r for r in rows if int(r["ts"]) <= ts]
        after = [r for r in rows if int(r["ts"]) >= ts]
        if before and after:
            a, b = before[-1], after[0]
            span = int(b["ts"]) - int(a["ts"])
            f = 0.0 if span == 0 else (ts - int(a["ts"])) / span
            lat = float(a["lat"]) + f * (float(b["lat"]) - float(a["lat"]))
            lon = float(a["lon"]) + f * (float(b["lon"]) - float(a["lon"]))
        else:
            near = min(rows, key=lambda r: abs(int(r["ts"]) - ts))
            if abs(int(near["ts"]) - ts) > 300:
                continue
            a, lat, lon = near, float(near["lat"]), float(near["lon"])
        if not (s <= lat <= n and w <= lon <= e):
            continue
        bucket, _, human = vessel_types.describe(a["vessel_type"])
        out.append({"mmsi": int(mmsi), "name": a["vessel_name"], "type": bucket, "lat": lat, "lon": lon,
                    "sog": a["sog"], "source": a["source"] if "source" in a.keys() else None})
    return out


def survey(sigma0_db: np.ndarray, raster, t_sat: datetime, conn=None,
           land_mask: Optional[np.ndarray] = None, installations: Sequence[Dict[str, Any]] = (),
           slick_points: Sequence[Sequence[float]] = (), ais_checked: bool = True) -> Dict[str, Any]:
    """Radar targets in the scene, each paired with an AIS vessel or reported radar-only.

    With ais_checked False (simulated traffic, or no AIS for this sea) targets
    are still reported, but nothing is called radar-only: an absence of AIS
    that was never recorded is not evidence of anything.
    """
    targets = detect_targets(sigma0_db, raster, land_mask)
    targets, on_platforms = drop_installations(targets, installations)
    bbox = raster.bounds_lonlat()
    vessels: List[Dict[str, Any]] = []
    if ais_checked:
        own = conn is None
        conn = conn or ingest.connect()
        try:
            vessels = ais_at(conn, bbox, t_sat)
        finally:
            if own:
                conn.close()

    used = set()
    for t in targets:
        best, best_d = None, float("inf")
        for v in vessels:
            if v["mmsi"] in used:
                continue
            d = float(haversine_km(t["lat"], t["lon"], v["lat"], v["lon"]))
            if d < best_d:
                best, best_d = v, d
        if ais_checked and best is not None and best_d <= MATCH_KM:
            used.add(best["mmsi"])
            t["ais"] = {"mmsi": best["mmsi"], "name": best["name"], "type": best["type"],
                        "distance_km": round(best_d, 2)}
        else:
            t["ais"] = None
        if slick_points:
            sp = np.asarray(slick_points, dtype=float)
            t["slick_km"] = round(float(np.min(haversine_km(t["lat"], t["lon"], sp[:, 1], sp[:, 0]))), 2)

    radar_only = [t for t in targets if t["ais"] is None] if ais_checked else []
    return {
        "available": True,
        "ais_checked": bool(ais_checked),
        "targets": targets,
        "matched": len(targets) - len(radar_only) if ais_checked else 0,
        "radar_only": len(radar_only) if ais_checked else None,
        "ais_vessels_in_scene": len(vessels),
        "ais_not_seen": len([v for v in vessels if v["mmsi"] not in used]),
        "on_known_platforms": on_platforms,
        "ais_sources": sorted({str(v.get("source")) for v in vessels}),
        "method": {"target_db": TARGET_DB, "peak_db": PEAK_DB, "match_km": MATCH_KM,
                   "installation_m": INSTALLATION_M, "min_pixels": MIN_PIXELS, "max_pixels": MAX_PIXELS},
    }
