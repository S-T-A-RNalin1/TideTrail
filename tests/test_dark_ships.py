"""Ships on radar are paired with AIS, and only real AIS can make one radar-only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from app.ais import dark, ingest
from app.geo.raster import Raster

T_SAT = datetime(2024, 3, 13, 1, 0, tzinfo=timezone.utc)
LON0, LAT0, PX = 71.50, 19.10, 0.0001      # ~11 m pixels


def _scene():
    rng = np.random.default_rng(3)
    vv = -20.0 + rng.normal(0, 1.0, (400, 400)).astype(np.float32)
    vh = vv - 7.0
    for r, c in ((100, 100), (300, 250)):          # two hulls, 5 x 3 pixels
        vv[r - 2:r + 3, c - 1:c + 2] = -3.0
        vh[r - 2:r + 3, c - 1:c + 2] = -10.0
    arr = np.stack([vh, vv]).astype(np.float32)
    return Raster(array=arr, transform=(PX, 0.0, LON0, 0.0, -PX, LAT0), crs="EPSG:4326")


def _lonlat(r, c):
    return LON0 + (c + 0.5) * PX, LAT0 - (r + 0.5) * PX


def _ais(tmp_path):
    conn = ingest.connect(tmp_path / "ais.sqlite")
    lon, lat = _lonlat(100, 100)
    rows = []
    for m in (-10, 10):                                # brackets the pass, 100 m apart
        ts = int((T_SAT + timedelta(minutes=m)).timestamp())
        rows.append((555, ts, ingest.iso(ts), lat + m * 0.00005 / 10, lon, 8.0, 0.0, 0.0, "SEEN SHIP",
                     None, None, "70", None, None, None, None, None, "test"))
    ingest.ingest_rows(conn, rows, "test")
    conn.commit()
    return conn


def test_hull_with_ais_is_matched_and_hull_without_is_radar_only(tmp_path):
    sar = _scene()
    r = dark.survey(np.asarray(sar.array), sar, T_SAT, conn=_ais(tmp_path))
    json.dumps(r)
    assert len(r["targets"]) == 2
    assert r["matched"] == 1 and r["radar_only"] == 1
    seen = [t for t in r["targets"] if t["ais"]][0]
    assert seen["ais"]["mmsi"] == 555 and seen["ais"]["distance_km"] < 0.2
    lone = [t for t in r["targets"] if not t["ais"]][0]
    lon, lat = _lonlat(300, 250)
    assert abs(lone["lon"] - lon) < 2 * PX and abs(lone["lat"] - lat) < 2 * PX


def test_without_real_ais_nothing_is_called_radar_only(tmp_path):
    sar = _scene()
    r = dark.survey(np.asarray(sar.array), sar, T_SAT, conn=_ais(tmp_path), ais_checked=False)
    assert len(r["targets"]) == 2
    assert r["radar_only"] is None and r["ais_checked"] is False
    assert all(t["ais"] is None for t in r["targets"])


def test_a_target_on_a_known_platform_is_dropped(tmp_path):
    sar = _scene()
    lon, lat = _lonlat(300, 250)
    r = dark.survey(np.asarray(sar.array), sar, T_SAT, conn=_ais(tmp_path),
                    installations=[{"name": "rig", "lat": lat, "lon": lon}])
    assert len(r["targets"]) == 1 and r["on_known_platforms"] == 1
