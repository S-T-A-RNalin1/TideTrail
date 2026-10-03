"""The forward release test must credit the true source and not a decoy.

Synthetic slicks are made with the same drift model the test uses, on the
constant metocean field, so the right answer is known: a leak from a fixed
point, and a ship discharging under way. Each is then assessed against the
true source and a decoy.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from app.drift import advection, fields as fields_mod, source
from app.geo.crs import LocalAEQD
from app.geo.geometry import hull_ring

T_SAT = datetime(2024, 3, 13, 1, 0, tzinfo=timezone.utc)
LAT0, LON0 = 19.05, 71.60


def _field():
    return fields_mod.synthetic_field(LAT0, LON0, T_SAT, hours=96)


def _slick_from(lon, lat):
    return [hull_ring(lon, lat)]


def _track(lon, lat, ts, name, mmsi):
    return {"name": name, "mmsi": mmsi, "rank": 1, "type": "tanker",
            "track": {"samples": [{"ts": int(t), "lon": float(x), "lat": float(y)}
                                  for x, y, t in zip(lon, lat, ts)]}}


def test_fixed_leak_is_explained_by_its_installation_not_a_decoy():
    field = _field()
    rt = T_SAT.timestamp() - np.linspace(0, 8 * 3600, 200)
    lon, lat = advection.advect_released(np.full(rt.size, LON0), np.full(rt.size, LAT0), rt, T_SAT, field,
                                         noise_scale=0.3)
    frame = LocalAEQD(LAT0, LON0)
    dlon, dlat = frame.to_deg(-15000.0, 12000.0)
    inst = [
        {"name": "true platform", "lat": LAT0, "lon": LON0, "source": "test"},
        {"name": "decoy platform", "lat": float(dlat), "lon": float(dlon), "source": "test"},
    ]
    r = source.assess(_slick_from(lon, lat), T_SAT, field, installations=inst)
    by = {h["name"]: h for h in r["hypotheses"]}
    assert by["true platform"]["fit"] >= source.EXPLAINS_AT
    assert by["true platform"]["support"] >= 0.25
    assert by["decoy platform"]["fit"] < 0.1
    assert r["verdict"] == "installation"
    assert by["true platform"]["ends_before_pass_h"] <= 0.5


def test_ship_discharge_is_explained_by_its_track_not_a_crossing_decoy():
    field = _field()
    frame = LocalAEQD(LAT0, LON0)
    # ship heading 045 at 12 kn, discharging from 3 h to 1 h before the pass
    t_track = T_SAT.timestamp() - np.arange(6 * 3600, -1, -60)
    speed = 12 * 0.5144
    along = speed * (t_track - t_track[0])
    sx, sy = frame.to_deg(-20000 + along * np.sin(np.radians(45)), -20000 + along * np.cos(np.radians(45)))
    sx, sy = np.asarray(sx, float), np.asarray(sy, float)
    dis = (t_track >= T_SAT.timestamp() - 3 * 3600) & (t_track <= T_SAT.timestamp() - 1 * 3600)
    lon, lat = advection.advect_released(sx[dis], sy[dis], t_track[dis], T_SAT, field, noise_scale=0.3)

    # decoy: same hours, heading 300, crossing well away from the slick
    dx, dy = frame.to_deg(25000 - along * np.sin(np.radians(60)), 5000 + along * np.cos(np.radians(60)))
    suspects = [_track(sx, sy, t_track, "true ship", 1), _track(np.asarray(dx, float), np.asarray(dy, float),
                                                                  t_track, "decoy ship", 2)]
    r = source.assess(_slick_from(lon, lat), T_SAT, field, suspects=suspects)
    by = {h["name"]: h for h in r["hypotheses"]}
    assert by["true ship"]["fit"] >= source.EXPLAINS_AT
    assert by["true ship"]["support"] >= 0.25
    assert by["decoy ship"]["fit"] < 0.1
    assert r["verdict"] == "shortlist"
    assert r["shortlist"][0]["name"] == "true ship"
    assert "decoy ship" not in [v["name"] for v in r["shortlist"]]
    w0 = datetime.fromisoformat(by["true ship"]["window"][0].replace("Z", "+00:00"))
    assert abs((T_SAT - w0).total_seconds() / 3600.0 - 3.0) <= 1.0


def test_no_outline_means_no_verdict():
    r = source.assess([], T_SAT, _field())
    assert r["available"] is False


def test_vessel_on_the_slick_at_the_pass_is_presence_and_serialises():
    """A vessel sitting on the leak at the pass is flagged as present.

    Its result must be plain JSON: the API serialises the job with no numpy
    fallback, and a numpy bool here once turned every run with a vessel on the
    slick into an HTTP 500.
    """
    import json

    field = _field()
    rt = T_SAT.timestamp() - np.linspace(0, 8 * 3600, 200)
    lon, lat = advection.advect_released(np.full(rt.size, LON0), np.full(rt.size, LAT0), rt, T_SAT, field,
                                         noise_scale=0.3)
    ts = T_SAT.timestamp() - np.arange(0, 2 * 3600 + 1, 60)[::-1]
    sitting = _track(np.full(ts.size, LON0), np.full(ts.size, LAT0), ts, "sitting ship", 7)
    r = source.assess(_slick_from(lon, lat), T_SAT, field, suspects=[sitting])
    json.dumps(r)
    assert [v["mmsi"] for v in r["on_slick_at_pass"]] == [7]
    assert r["hypotheses"][0]["present_at_pass"] is True


def test_two_sources_the_drift_cannot_separate_are_both_reported():
    field = _field()
    rt = T_SAT.timestamp() - np.linspace(0, 8 * 3600, 200)
    lon, lat = advection.advect_released(np.full(rt.size, LON0), np.full(rt.size, LAT0), rt, T_SAT, field,
                                         noise_scale=0.3)
    dlon, dlat = LocalAEQD(LAT0, LON0).to_deg(400.0, 0.0)
    inst = [
        {"name": "platform A", "lat": LAT0, "lon": LON0, "source": "test"},
        {"name": "platform B", "lat": float(dlat), "lon": float(dlon), "source": "test"},
    ]
    r = source.assess(_slick_from(lon, lat), T_SAT, field, installations=inst)
    # no vessel in the water: both platforms are reported, neither is picked
    assert r["verdict"] == "installation"
    assert sorted(i["name"] for i in r["installations_fitting"]) == ["platform A", "platform B"]


def test_adding_a_candidate_does_not_change_another_candidates_result():
    field = _field()
    rt = T_SAT.timestamp() - np.linspace(0, 8 * 3600, 200)
    lon, lat = advection.advect_released(np.full(rt.size, LON0), np.full(rt.size, LAT0), rt, T_SAT, field,
                                         noise_scale=0.3)
    true = {"name": "true platform", "lat": LAT0, "lon": LON0, "source": "test"}
    dlon, dlat = LocalAEQD(LAT0, LON0).to_deg(-15000.0, 12000.0)
    decoy = {"name": "decoy platform", "lat": float(dlat), "lon": float(dlon), "source": "test"}
    alone = source.assess(_slick_from(lon, lat), T_SAT, field, installations=[true])
    both = source.assess(_slick_from(lon, lat), T_SAT, field, installations=[decoy, true])
    fit = lambda r: [h["fit"] for h in r["hypotheses"] if h["name"] == "true platform"][0]
    assert fit(alone) == fit(both)


def test_structures_of_one_complex_are_one_candidate():
    frame = LocalAEQD(LAT0, LON0)
    pts = [frame.to_deg(x, 0.0) for x in (0.0, 60.0, 120.0, 5000.0)]
    inst = [{"name": "osm %d" % i, "lat": float(la), "lon": float(lo), "source": "OpenStreetMap"}
            for i, (lo, la) in enumerate(pts)]
    inst.append({"name": "documented site", "lat": float(pts[1][1]), "lon": float(pts[1][0]), "source": "documented"})
    near = source.installations_near(LON0, LAT0, inst)
    assert [r["name"] for r in near] == ["documented site", "osm 3"]
    assert near[0]["structures"] == 4
