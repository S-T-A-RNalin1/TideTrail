"""How often does attribution name the ship that really released the oil?

No public dataset pairs a satellite slick with the AIS of the ship that made
it, so this builds the known answer from real parts. A real vessel from the
real MarineCadastre traffic is chosen, and oil is released along its real
track for a short window some hours before a radar time, then drifted to that
time through the real cached wind and currents, with one unseen realisation
of their error. The outline of that oil is handed to the unchanged pipeline
(hindcast, AIS filter, scoring, forward source test) as though the detector
had drawn it, and the pipeline has to find the ship among all the other real
traffic in the store. Nothing it runs is told which vessel it was.

What this measures: the drift, the AIS join, the scoring and the source test.
What it does not: the detector, which is measured separately on the Zenodo
tiles (IoU_oil 0.889).

Usage:
    python scripts/validate_attribution.py --cases 40
    python scripts/validate_attribution.py --cases 10 --scene santa_barbara_seeps
    python scripts/validate_attribution.py --merge run1.json run2.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString, MultiPoint          # noqa: E402

from app import config, pipeline, scenes as scenes_mod        # noqa: E402
from app.ais import ingest, interpolate                       # noqa: E402
from app.drift import advection, fields as fields_mod, land, source as source_mod   # noqa: E402
from app.geo import geometry                                   # noqa: E402
from app.geo.crs import LocalAEQD                              # noqa: E402

SCENES = ("gom_mc20_chronic_slick", "santa_barbara_seeps")
OUT = Path(config.DATA_DIR) / "validation" / "attribution.json"


def _moving_tracks(conn, scene, t_sat):
    """Real vessels under way in the scene area for the 12 hours before t_sat."""
    w, s, e, n = scene.bounds
    box = (w - 0.4, s - 0.4, e + 0.4, n + 0.4)
    t0, t1 = int(t_sat.timestamp()) - 12 * 3600, int(t_sat.timestamp())
    grouped = ingest.query_window(conn, box, t0, t1)
    tracks = interpolate.build_tracks(grouped, t0, t1, step_seconds=60)
    out = []
    for mmsi, tr in tracks.items():
        if tr.n < 240 or float(np.nanmedian(tr.sog)) < 5.0:
            continue
        if float(np.mean(tr.dead_reckoned)) > 0.3:
            continue
        out.append(tr)
    return out


def _make_slick(tr, t_sat, rng, field):
    """Release oil along the true track and drift it to t_sat. None if it goes ashore."""
    hours_before = float(rng.uniform(2.0, 9.0))
    minutes = float(rng.uniform(30.0, 120.0))
    t_end = t_sat.timestamp() - hours_before * 3600
    t_start = t_end - minutes * 60
    rel_t = np.arange(t_start, t_end, 120.0)
    if rel_t[0] < tr.ts[0] or rel_t[-1] > tr.ts[-1]:
        return None
    lon0 = np.interp(rel_t, tr.ts, tr.lon)
    lat0 = np.interp(rel_t, tr.ts, tr.lat)
    if land.on_land(lon0, lat0).any():
        return None
    lon, lat = advection.advect_released(lon0, lat0, rel_t, t_sat, field, dt_seconds=600,
                                         seed=int(rng.integers(1_000_000)),
                                         member=np.zeros(rel_t.size, dtype=int))
    if land.on_land(lon, lat).mean() > 0.2:
        return None
    frame = LocalAEQD(float(np.mean(lat)), float(np.mean(lon)))
    x, y = frame.to_m(lon, lat)
    # an oil streak is a few hundred metres wide once it has spread
    poly = LineString(np.column_stack([x, y])).buffer(250.0).simplify(40.0)
    if poly.geom_type != "Polygon" or poly.area < 0.1e6:
        return None
    rx, ry = np.array(poly.exterior.coords).T
    rlon, rlat = frame.to_deg(rx, ry)
    ring = [(float(a), float(b)) for a, b in zip(np.atleast_1d(rlon), np.atleast_1d(rlat))]
    m = geometry._ring_metrics(rx, ry)
    c = poly.centroid
    clon, clat = frame.to_deg(np.array([c.x]), np.array([c.y]))
    return {"ring": ring, "centroid": (float(clon[0]), float(clat[0])),
            "area_km2": poly.area / 1e6, "axis_deg": (90.0 - m["orientation_deg"]) % 180.0,
            "hours_before": hours_before, "minutes": minutes}


def run_case(scene, t_sat, tr, slick):
    """The production path after detection, exactly: drift, AIS, forward tests, ranking."""
    drift = pipeline.run_drift(slick["ring"], slick["centroid"], t_sat, config.HINDCAST_H,
                               config.FORECAST_H, config.ENSEMBLE_N, scene.id)
    attr = pipeline.run_attribution(
        drift["origin_ring"], drift["origin"]["lon"], drift["origin"]["lat"],
        pipeline._utc(drift["origin"]["t"]), slick["centroid"][0], slick["centroid"][1],
        radius_km=config.SEARCH_RADIUS_KM, window_h=config.ORIGIN_WINDOW_H,
        top_n=max(10, source_mod.MAX_VESSELS),
        zone_radius_km=drift["origin"].get("spread_km"), corridor=drift["hindcast_hourly"],
        t_sat=t_sat, slick_axis_deg=slick["axis_deg"])
    field = fields_mod.load_for_scene(scene.id, slick["centroid"][1], slick["centroid"][0], t_sat)
    src = source_mod.assess([slick["ring"]], t_sat, field,
                            installations=source_mod.load_installations(scene.id),
                            suspects=attr["suspects"])
    kept = len(attr["suspects"])
    ranked = pipeline._rank_by_forward_fit(attr["suspects"], src, 10)
    by = {int(s["mmsi"]): s for s in attr["suspects"]}
    me = by.get(tr.mmsi)
    rank = next((s["rank"] for s in ranked if int(s["mmsi"]) == tr.mmsi), None)
    short = [int(v["mmsi"]) for v in src.get("shortlist") or []]
    return {
        "rank": rank, "score_rank": None if me is None else me["score_rank"],
        "kept": kept, "in_shortlist": tr.mmsi in short, "shortlist_size": len(short),
        "source_verdict": src.get("verdict"),
        "true_fit": None if me is None else me.get("forward_fit"),
        "top": ranked[0]["name"] if ranked else None,
        "top_fit": ranked[0].get("forward_fit") if ranked else None,
        "installations_fitting": len(src.get("installations_fitting") or []),
    }


def summarise(rows):
    n = len(rows)
    r = [c["rank"] for c in rows]
    sr = [c["score_rank"] for c in rows]
    return {
        "cases": n,
        "top1": sum(1 for x in r if x == 1), "top3": sum(1 for x in r if x and x <= 3),
        "top10": sum(1 for x in r if x and x <= 10), "shortlist3": sum(1 for c in rows if c["in_shortlist"]),
        "score_only_top1": sum(1 for x in sr if x == 1), "score_only_top3": sum(1 for x in sr if x and x <= 3),
        "not_kept": sum(1 for x in sr if x is None),
        "median_vessels_kept": float(np.median([c["kept"] for c in rows])) if rows else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=40)
    ap.add_argument("--scene", action="append", default=[])
    ap.add_argument("--seed", type=int, default=20260929)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--merge", nargs="+", help="combine earlier runs' JSON files into --out")
    args = ap.parse_args()
    if args.merge:
        rows, seeds = [], []
        for f in args.merge:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
            rows += doc["cases"]
            seeds.append(doc["summary"].get("seed"))
        summary = summarise(rows)
        summary.update(seeds=seeds, generated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"summary": summary, "cases": rows}, indent=1, default=str),
                                  encoding="utf-8")
        print(json.dumps(summary, indent=1))
        return 0
    rng = np.random.default_rng(args.seed)
    scenes = [scenes_mod.get(s) for s in (args.scene or SCENES)]
    conn = ingest.connect()
    rows = []
    t_run = time.time()
    tries = 0
    while len(rows) < args.cases and tries < args.cases * 20:
        tries += 1
        scene = scenes[len(rows) % len(scenes)]
        st = conn.execute("SELECT MIN(ts), MAX(ts) FROM positions WHERE source='marinecadastre' AND "
                          "lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
                          (scene.bounds[0] - 0.5, scene.bounds[2] + 0.5, scene.bounds[1] - 0.5,
                           scene.bounds[3] + 0.5)).fetchone()
        # a radar time with 14 h of AIS before it and 2 h after
        ts = float(rng.uniform(st[0] + 14 * 3600, st[1] - 2 * 3600))
        t_sat = datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0)
        field = fields_mod.load_for_scene(scene.id, scene.centroid[1], scene.centroid[0], t_sat)
        if not field.covers(scene.centroid[1], scene.centroid[0], t_sat):
            continue
        tracks = _moving_tracks(conn, scene, t_sat)
        if not tracks:
            continue
        tr = tracks[int(rng.integers(len(tracks)))]
        slick = _make_slick(tr, t_sat, rng, field)
        if slick is None:
            continue
        t0 = time.time()
        res = run_case(scene, t_sat, tr, slick)
        res.update(scene=scene.id, t_sat=t_sat.isoformat(), mmsi=tr.mmsi, name=tr.name,
                   area_km2=round(slick["area_km2"], 3), hours_before=round(slick["hours_before"], 1),
                   minutes=round(slick["minutes"]), seconds=round(time.time() - t0, 1))
        rows.append(res)
        print("%2d %-24s %-22s rank %-4s (score alone %-4s) of %-2s kept | shortlist %-5s fit %s" % (
            len(rows), scene.id[:24], (tr.name or str(tr.mmsi))[:22], res["rank"], res["score_rank"],
            res["kept"], res["in_shortlist"], res["true_fit"]), flush=True)

    n = len(rows)
    ranks = [r["rank"] for r in rows]
    summary = summarise(rows)
    summary.update(seed=args.seed, minutes=round((time.time() - t_run) / 60, 1),
                   generated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cases": rows}, indent=1, default=str), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
