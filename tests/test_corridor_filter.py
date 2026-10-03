"""The drift-corridor rule catches a ship that discharged shortly before the pass.

The origin rule alone looks for vessels near the estimated origin within a few
hours of the origin time, and that window closes hours before the radar pass.
A ship that crossed the slick an hour before the image is invisible to it. The
corridor rule keeps a vessel that was where the drifting oil was at the same
hour, and the scorer then ranks it under that hypothesis.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from app.ais import filter as ais_filter, ingest, score
from app.geo.crs import LocalAEQD

T_SAT = datetime(2024, 3, 13, 1, 0, tzinfo=timezone.utc)
T_ORIGIN = T_SAT - timedelta(hours=10)
LAT0, LON0 = 19.05, 71.60
FRAME = LocalAEQD(LAT0, LON0)
SLICK_KM = (15.0, 0.0)          # the slick sits 15 km east of the origin


def _deg(x_km, y_km):
    lon, lat = FRAME.to_deg(np.atleast_1d(x_km) * 1000.0, np.atleast_1d(y_km) * 1000.0)
    return float(np.atleast_1d(lon)[0]), float(np.atleast_1d(lat)[0])


def _rows(mmsi, name, vtype, path, cog, sog):
    """path(t_hours_relative_to_pass) -> (x_km, y_km); one fix every 5 minutes."""
    out = []
    for m in range(-14 * 60, 60 + 1, 5):
        ts = int((T_SAT + timedelta(minutes=m)).timestamp())
        lon, lat = _deg(*path(m / 60.0))
        out.append((mmsi, ts, ingest.iso(ts), lat, lon, sog, cog, cog, name, None, None,
                    vtype, None, None, None, None, None, "test"))
    return out


def _store(tmp_path):
    conn = ingest.connect(tmp_path / "ais.sqlite")
    # 12 kn southbound along x = 15 km, on the slick one hour before the pass
    fresh = _rows(111, "FRESH DISCHARGER", "84", lambda h: (15.0, -22.2 * (h + 1.0)), 180.0, 12.0)
    # loitering beside the origin
    passer = _rows(222, "ORIGIN PASSER", "70", lambda h: (-1.0 + 0.3 * (h + 14.0), 0.5), 90.0, 0.2)
    # far away the whole time
    bystander = _rows(333, "BYSTANDER", "70", lambda h: (100.0 - 5.0 * (h + 14.0), 100.0), 270.0, 2.7)
    ingest.ingest_rows(conn, fresh + passer + bystander, "test")
    conn.commit()
    return conn


def _origin_ring(half_km=2.0):
    return [_deg(x, y) for x, y in ((-half_km, -half_km), (half_km, -half_km),
                                   (half_km, half_km), (-half_km, half_km), (-half_km, -half_km))]


def _corridor():
    """Hourly drift centre moving from the origin to the slick, 2 km spread."""
    out = []
    for h in range(0, 13):
        t = T_SAT - timedelta(hours=h)
        f = max(0.0, 1.0 - h / 10.0)
        lon, lat = _deg(SLICK_KM[0] * f, 0.0)
        out.append({"t": t.isoformat(), "lon": lon, "lat": lat, "spread_km": 2.0})
    return out


def test_origin_rule_alone_misses_the_fresh_discharger(tmp_path):
    conn = _store(tmp_path)
    res = ais_filter.candidates(conn, _origin_ring(), LON0, LAT0, T_ORIGIN, radius_km=10, window_hours=3)
    assert 222 in res.tracks
    assert 111 not in res.tracks
    assert 333 not in res.tracks


def test_corridor_keeps_and_ranks_the_fresh_discharger(tmp_path):
    conn = _store(tmp_path)
    res = ais_filter.candidates(conn, _origin_ring(), LON0, LAT0, T_ORIGIN, radius_km=10, window_hours=3,
                                corridor=_corridor(), t_sat=T_SAT)
    assert 111 in res.tracks and 222 in res.tracks
    assert 333 not in res.tracks
    assert res.kept_by_corridor_only == 1
    match = res.corridor[111]
    assert abs(match["ts"] - (T_SAT - timedelta(hours=1)).timestamp()) <= 15 * 60
    assert match["distance_km"] < 2.0

    slick_lon, slick_lat = _deg(*SLICK_KM)
    ranked = score.rank_suspects(res.tracks, res.closest, _origin_ring(), LON0, LAT0, slick_lon, slick_lat,
                                 t_origin_ts=int(T_ORIGIN.timestamp()), zone_radius_km=2.0,
                                 corridor_matches=res.corridor, slick_axis_deg=0.0,
                                 t_sat_ts=int(T_SAT.timestamp()))
    fresh = [s for s in ranked if s.mmsi == 111][0]
    assert fresh.detail["hypothesis"] == "drift_corridor"
    assert fresh.reasons[0].startswith("with_drifting_oil_")
    assert fresh.detail["trajectory"]["against"] == "slick_axis"
    assert fresh.detail["trajectory"]["aligned"] is True     # southbound along a north-south slick
    assert fresh.rank == 1


def test_berthed_vessel_away_from_the_oil_is_dropped(tmp_path):
    conn = _store(tmp_path)
    # moored 12 km north of the origin for the whole window, AIS on
    moored = _rows(444, "HARBOUR YACHT", "37", lambda h: (0.0, 12.0), 0.0, 0.0)
    # at anchor on the origin itself: could be leaking, so it stays
    anchored = _rows(555, "ANCHORED TANKER", "80", lambda h: (0.3, 0.2), 0.0, 0.0)
    ingest.ingest_rows(conn, moored + anchored, "test")
    conn.commit()
    res = ais_filter.candidates(conn, _origin_ring(), LON0, LAT0, T_ORIGIN, radius_km=15, window_hours=3,
                                corridor=_corridor(), t_sat=T_SAT)
    assert 444 not in res.tracks
    assert 555 in res.tracks
    assert res.dropped_stationary >= 1
