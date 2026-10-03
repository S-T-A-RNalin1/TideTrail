"""Which release would have produced this slick? Forward hypothesis tests.

Each hypothesis is a concrete release: a fixed installation leaking at its own
position, or a vessel discharging along its own recorded AIS track. Parcels are
released every few minutes over the hours before the radar pass, drifted
forward through the same cached wind and current fields as the hindcast, and
compared with the slick the detector actually outlined at the pass.

    landed     a parcel that ends within HIT_KM of the observed slick
    window     the longest run of release times whose parcels landed: if this
               source discharged, this is when
    coverage   the share of the observed slick within HIT_KM of the oil
               released in that window: how much of the slick it explains
    precision  the share of that released oil lying within HIT_KM of the
               slick: how much of the hypothesis the slick confirms
    fit        the harmonic mean of the two. Coverage alone rewards a long
               discharge that sweeps across scattered fragments; precision
               alone rewards a single parcel that happens to land.

The wind and currents are uncertain, so every hypothesis is run as an ensemble
of MEMBERS perturbed drifts and each member is scored on its own. The reported
fit is the ensemble mean and support is the share of members that explain the
slick. Pooling all members into one cloud would make the score depend on how
many members were run, since every extra member adds spread oil that lowers
precision.

This is how oil spill forensics tests a suspected source, and it is symmetric:
an installation and a ship are judged by the same physics. It does not decide
between two sources that both fit, which happens when a ship's track runs along
the drift line. Both are then reported, because that is what the evidence
supports.
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from .. import config
from ..geo.crs import LocalAEQD, haversine_km
from . import advection

HOURS = 24
STEP_MIN = 10
MEMBERS = 16
HIT_KM = 1.0
GRID_KM = 0.2
GAP_MIN = 30
# A release shorter than this is presence, not discharge: a vessel sitting on a
# dark patch at the pass fits perfectly and proves nothing, it may be its wake.
MIN_WINDOW_MIN = 20
# A drift run "explains" the slick at this fit; support counts those runs. Set
# by twin experiments with a fixed source: at 0.40 the true source cleared it
# in 26 of 30 and a decoy 3 to 15 km away in 6 of 90.
EXPLAINS_AT = 0.40
MAX_INSTALLATIONS = 8
# Every kept vessel is tested, up to this many, because the forward fit is what
# ranks them: in known-answer runs on real traffic, ranking by fit put the true
# ship in the top three in 23 of 24 cases against 15 of 24 for the score alone.
MAX_VESSELS = 40
# The verdict is a shortlist, never a single culprit. With twenty ships in the
# water one innocent track often fits the drift line as well as the guilty one,
# and in those runs naming the best fit was wrong about as often as right.
SHORTLIST = 3
MIN_FIT = 0.05
DT_SECONDS = 1800
INSTALLATION_RADIUS_KM = 25.0
SAME_SITE_M = 200.0


def load_installations(scene_id: str) -> List[Dict[str, Any]]:
    """OSM platforms cached for this scene plus the documented release points."""
    base = config.DATA_DIR / "infrastructure"
    out: List[Dict[str, Any]] = []
    try:
        doc = json.loads((base / ("%s.json" % scene_id)).read_text(encoding="utf-8"))
        out += [dict(i, source="OpenStreetMap") for i in doc.get("installations", [])]
    except (OSError, ValueError):
        pass
    try:
        doc = json.loads((base / "known_sources.json").read_text(encoding="utf-8"))
        out += [dict(i, source="documented") for i in doc.get("sources", [])]
    except (OSError, ValueError):
        pass
    return out


def installations_near(lon: float, lat: float, installations: Sequence[Dict[str, Any]],
                       radius_km: float = INSTALLATION_RADIUS_KM,
                       limit: int = MAX_INSTALLATIONS) -> List[Dict[str, Any]]:
    near = []
    for item in installations or []:
        d = float(haversine_km(lat, lon, float(item["lat"]), float(item["lon"])))
        if d <= radius_km:
            near.append(dict(item, distance_km=round(d, 2)))
    # A platform complex is often mapped as several objects tens of metres
    # apart. The drift cannot tell them apart, so they are one candidate, named
    # by the documented entry when there is one, and one slot in the limit.
    near.sort(key=lambda r: (r.get("source") != "documented", r["distance_km"]))
    out: List[Dict[str, Any]] = []
    for item in near:
        twin = next((k for k in out if float(haversine_km(k["lat"], k["lon"], item["lat"], item["lon"])) * 1000.0
                     <= SAME_SITE_M), None)
        if twin is not None:
            twin["structures"] = twin.get("structures", 1) + 1
        else:
            out.append(item)
    out.sort(key=lambda r: r["distance_km"])
    # Documented release points are always tested; in a dense platform field
    # the nearest-N cut would otherwise drop the one source on record.
    keep = out[:limit]
    keep += [r for r in out[limit:] if r.get("source") == "documented"]
    return keep


class Slick:
    """The observed slick rasterised in a local metric frame, for fast lookups."""

    def __init__(self, rings: Sequence[Sequence[Tuple[float, float]]], grid_km: float = GRID_KM):
        pts = np.array([p for r in rings for p in r], dtype=float)
        self.frame = LocalAEQD(float(pts[:, 1].mean()), float(pts[:, 0].mean()))
        xy_rings = []
        for r in rings:
            r = np.asarray(r, dtype=float)
            x, y = self.frame.to_m(r[:, 0], r[:, 1])
            xy_rings.append(np.c_[np.asarray(x), np.asarray(y)] / 1000.0)
        allxy = np.vstack(xy_rings)
        x0, y0 = allxy.min(axis=0) - grid_km
        x1, y1 = allxy.max(axis=0) + grid_km
        nx = int(np.ceil((x1 - x0) / grid_km)) + 1
        ny = int(np.ceil((y1 - y0) / grid_km)) + 1
        img = Image.new("1", (nx, ny), 0)
        draw = ImageDraw.Draw(img)
        for xy in xy_rings:
            poly = [((px - x0) / grid_km, (py - y0) / grid_km) for px, py in xy]
            if len(poly) >= 3:
                draw.polygon(poly, fill=1, outline=1)
        mask = np.array(img, dtype=bool)
        iy, ix = np.nonzero(mask)
        self.cells = np.c_[x0 + ix * grid_km, y0 + iy * grid_km]
        self.tree = cKDTree(self.cells)
        self.area_km2 = float(len(self.cells)) * grid_km * grid_km

    def to_km(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        x, y = self.frame.to_m(np.asarray(lon, float), np.asarray(lat, float))
        return np.c_[np.asarray(x), np.asarray(y)] / 1000.0


def _release_times(t_sat: datetime, hours: float = HOURS, step_min: int = STEP_MIN) -> np.ndarray:
    te = t_sat.timestamp()
    return te - np.arange(0, hours * 3600 + 1, step_min * 60)[::-1]


def _score_member(slick: Slick, xy: np.ndarray, rel_t: np.ndarray, landed: np.ndarray) -> Dict[str, Any]:
    """Coverage, precision and fit of one ensemble member's release line."""
    run = _longest_run(rel_t, landed, GAP_MIN * 60)
    if run is None or rel_t[run[1]] - rel_t[run[0]] < MIN_WINDOW_MIN * 60:
        return {"fit": 0.0, "coverage": 0.0, "precision": 0.0, "run": None}
    a, b = run
    # A discharge is continuous between release samples, and a ship at 12 kn
    # moves 3.7 km in ten minutes, so the parcels are joined in release order
    # and the line is densified before measuring coverage.
    parcels = _densify(xy[a:b + 1, None, :], GRID_KM)
    near, _ = cKDTree(parcels).query(slick.cells, k=1)
    coverage = float(np.mean(near <= HIT_KM))
    back, _ = slick.tree.query(parcels, k=1)
    precision = float(np.mean(back <= HIT_KM))
    fit = 0.0 if coverage + precision == 0 else 2 * coverage * precision / (coverage + precision)
    return {"fit": fit, "coverage": coverage, "precision": precision, "run": run}


def test_release(slick: Slick, rel_lon: np.ndarray, rel_lat: np.ndarray, rel_t: np.ndarray,
                 t_sat: datetime, field, seed: int) -> Dict[str, Any]:
    """Drift one release hypothesis forward and measure what it explains."""
    k = MEMBERS
    lon0 = np.repeat(rel_lon, k)
    lat0 = np.repeat(rel_lat, k)
    t0 = np.repeat(rel_t, k)
    member = np.tile(np.arange(k), len(rel_t))
    lon, lat = advection.advect_released(lon0, lat0, t0, t_sat, field, dt_seconds=DT_SECONDS,
                                         seed=seed, member=member)
    xy = slick.to_km(lon, lat).reshape(-1, k, 2)
    d, _ = slick.tree.query(xy.reshape(-1, 2), k=1)
    landed = (d <= HIT_KM).reshape(-1, k)
    share = landed.mean(axis=1)

    scores = [_score_member(slick, xy[:, m], rel_t, landed[:, m]) for m in range(k)]
    fits = np.array([x["fit"] for x in scores])
    present = bool(share[-1] >= 0.5 and rel_t[-1] >= t_sat.timestamp() - STEP_MIN * 60)
    # When this source would have discharged: the releases most members land,
    # or failing that, the run of the member that fits best.
    run = _longest_run(rel_t, share >= 0.5, GAP_MIN * 60)
    if run is None or rel_t[run[1]] - rel_t[run[0]] < MIN_WINDOW_MIN * 60:
        run = scores[int(np.argmax(fits))]["run"]
    out = {
        "fit": round(float(fits.mean()), 4),
        "support": round(float(np.mean(fits >= EXPLAINS_AT)), 4),
        "fit_spread": [round(float(np.percentile(fits, 10)), 4), round(float(np.percentile(fits, 90)), 4)],
        "coverage": round(float(np.mean([x["coverage"] for x in scores])), 4),
        "precision": round(float(np.mean([x["precision"] for x in scores])), 4),
        "members": k,
        "present_at_pass": present,
        "window": None,
    }
    if run is None or not fits.any():
        out["landed_fraction"] = 0.0
        return out
    a, b = run
    out.update({
        "landed_fraction": round(float(share[a:b + 1].mean()), 4),
        "window": [_iso(rel_t[a]), _iso(rel_t[b])],
        "window_hours": round(float(rel_t[b] - rel_t[a]) / 3600.0, 2),
        "ends_before_pass_h": round((t_sat.timestamp() - rel_t[b]) / 3600.0, 2),
        "release_points": [[round(float(x), 5), round(float(y), 5)]
                           for x, y in zip(rel_lon[a:b + 1:6], rel_lat[a:b + 1:6])],
    })
    return out


def _seed(*parts: Any) -> int:
    """A seed fixed by the candidate itself, so adding a candidate never reseeds another."""
    return config.RANDOM_SEED + zlib.crc32(":".join(str(x) for x in parts).encode("utf-8")) % 1_000_000


def _densify(paths: np.ndarray, step_km: float) -> np.ndarray:
    """paths is (n_release, members, 2): join each member's parcels in order."""
    pts = [paths.reshape(-1, 2)]
    if paths.shape[0] > 1:
        seg = paths[1:] - paths[:-1]
        n = int(np.ceil(np.nanmax(np.hypot(seg[..., 0], seg[..., 1])) / step_km)) if seg.size else 1
        n = min(max(n, 1), 400)
        f = (np.arange(1, n) / n)[:, None, None, None]
        pts.append((paths[:-1][None] + seg[None] * f).reshape(-1, 2))
    return np.vstack(pts)


def _longest_run(t: np.ndarray, ok: np.ndarray, max_gap_s: float) -> Optional[Tuple[int, int]]:
    idx = np.nonzero(ok)[0]
    if idx.size == 0:
        return None
    best, start = (idx[0], idx[0]), idx[0]
    for p, q in zip(idx[:-1], idx[1:]):
        if t[q] - t[p] > max_gap_s:
            if t[p] - t[start] > t[best[1]] - t[best[0]]:
                best = (start, p)
            start = q
    if t[idx[-1]] - t[start] > t[best[1]] - t[best[0]]:
        best = (start, idx[-1])
    return int(best[0]), int(best[1])


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vessel_release(samples: Sequence[Dict[str, Any]], t_sat: datetime,
                   hours: float = HOURS) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Release points along a vessel's recorded track, every STEP_MIN minutes."""
    if not samples:
        return None
    ts = np.array([s["ts"] for s in samples], dtype=float)
    lon = np.array([s["lon"] for s in samples], dtype=float)
    lat = np.array([s["lat"] for s in samples], dtype=float)
    order = np.argsort(ts)
    ts, lon, lat = ts[order], lon[order], lat[order]
    rt = _release_times(t_sat, hours)
    rt = rt[(rt >= ts[0]) & (rt <= ts[-1])]
    if rt.size < 2:
        return None
    return np.interp(rt, ts, lon), np.interp(rt, ts, lat), rt


def assess(rings: Sequence[Sequence[Tuple[float, float]]], t_sat: datetime, field,
           installations: Sequence[Dict[str, Any]] = (),
           suspects: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Test every nearby installation and every ranked vessel as the source."""
    rings = [r for r in rings if len(r) >= 4]
    if not rings:
        return {"available": False, "reason": "no slick outline to test against"}
    slick = Slick(rings)
    if len(slick.cells) < 3:
        return {"available": False, "reason": "slick too small to test against"}
    cx = float(np.mean([p[0] for r in rings for p in r]))
    cy = float(np.mean([p[1] for r in rings for p in r]))

    results: List[Dict[str, Any]] = []
    for j, inst in enumerate(installations_near(cx, cy, installations)):
        rt = _release_times(t_sat)
        r = test_release(slick, np.full(rt.size, float(inst["lon"])), np.full(rt.size, float(inst["lat"])),
                         rt, t_sat, field, seed=_seed("installation", inst["name"], inst["lat"], inst["lon"]))
        r.update(kind="installation", name=inst["name"], lat=inst["lat"], lon=inst["lon"],
                 source=inst.get("source"), installation_kind=inst.get("kind"),
                 reference=inst.get("reference"), distance_km=inst["distance_km"])
        results.append(r)

    for j, s in enumerate((suspects or [])[:MAX_VESSELS]):
        rel = vessel_release((s.get("track") or {}).get("samples") or [], t_sat)
        if rel is None:
            continue
        r = test_release(slick, rel[0], rel[1], rel[2], t_sat, field, seed=_seed("vessel", s.get("mmsi")))
        r.update(kind="vessel", name=s.get("name"), mmsi=s.get("mmsi"), rank=s.get("rank"),
                 vessel_type=s.get("type"))
        results.append(r)

    results.sort(key=lambda r: -r["fit"])
    vessels = [r for r in results if r["kind"] == "vessel"]
    shortlist = [r for r in vessels if r["fit"] >= MIN_FIT][:SHORTLIST]
    inst_fit = [r for r in results if r["kind"] == "installation" and r["fit"] >= EXPLAINS_AT]
    if not results:
        verdict, headline = "untested", "No installation or vessel close enough to test"
    elif shortlist:
        verdict = "shortlist"
        headline = ("Shortlist: the %d vessel%s whose released oil best reproduces the slick"
                    % (len(shortlist), "" if len(shortlist) == 1 else "s"))
    elif inst_fit:
        verdict, headline = "installation", "No vessel reproduces it; a fixed installation does"
    else:
        verdict, headline = "unexplained", "No tested source reproduces the slick"

    on_slick = [r for r in results if r["kind"] == "vessel" and r.get("present_at_pass")]
    validation = load_validation()
    return {
        "available": True,
        "verdict": verdict,
        "on_slick_at_pass": [{"name": r["name"], "mmsi": r["mmsi"]} for r in on_slick],
        "headline": headline,
        "detail": _detail(verdict, shortlist, inst_fit, results, validation),
        "shortlist": [{"name": r["name"], "mmsi": r["mmsi"], "fit": r["fit"], "support": r.get("support")}
                      for r in shortlist],
        "installations_fitting": [{"name": r["name"], "fit": r["fit"]} for r in inst_fit],
        "hypotheses": results,
        "slick_area_km2_tested": round(slick.area_km2, 3),
        "validation": validation,
        "method": {
            "hours_tested": HOURS, "release_every_min": STEP_MIN, "members": MEMBERS,
            "fit_is": "ensemble mean of each member's fit; support is the share of members at or above explains_at",
            "hit_km": HIT_KM, "explains_at": EXPLAINS_AT, "shortlist": SHORTLIST, "min_fit": MIN_FIT,
            "vessels_tested_max": MAX_VESSELS, "min_window_min": MIN_WINDOW_MIN,
            "score": "fit = harmonic mean of coverage and precision",
            "installations_within_km": INSTALLATION_RADIUS_KM,
        },
    }


def load_validation() -> Optional[Dict[str, Any]]:
    """The known-answer results from scripts/validate_attribution.py, if run."""
    path = config.DATA_DIR / "validation" / "attribution.json"
    try:
        summary = json.loads(path.read_text(encoding="utf-8")).get("summary") or {}
    except (OSError, ValueError):
        return None
    return summary if summary.get("cases") else None


def _detail(verdict: str, shortlist: List[Dict[str, Any]], inst_fit: List[Dict[str, Any]],
            results: List[Dict[str, Any]], validation: Optional[Dict[str, Any]]) -> str:
    def name(r):
        return r["name"] if r["kind"] == "installation" else "%s (MMSI %s)" % (r["name"], r["mmsi"])

    lead = "Oil was released along the recorded track of every candidate vessel and from every " \
           "nearby installation, and drifted to the pass through the recorded wind and currents."
    if verdict == "shortlist":
        parts = []
        for r in shortlist:
            t = "%s, fit %.2f in %d of %d runs" % (name(r), r["fit"], _runs(r), r.get("members", MEMBERS))
            if r.get("window"):
                t += ", releasing %s to %s UTC" % (r["window"][0][11:16], r["window"][1][11:16])
            parts.append(t)
        s = lead + " These reproduce the slick best: " + "; ".join(parts) + "."
        if inst_fit:
            s += " Installations that fit as well: " + ", ".join("%s %.2f" % (r["name"], r["fit"]) for r in inst_fit[:3]) + "."
        s += " They are the vessels to inspect first, not a finding."
        if validation:
            s += (" In %d known-answer runs on real traffic the ship that released the oil was on this "
                  "shortlist in %d and first in %d." % (validation["cases"], validation.get("shortlist3", 0),
                                                       validation.get("top1", 0)))
        return s
    if verdict == "installation":
        return lead + " No vessel's oil reaches it; %s fits at %.2f." % (inst_fit[0]["name"], inst_fit[0]["fit"])
    if verdict == "unexplained":
        return lead + " None of it reproduces the slick; the source may be outside the AIS or the area tested."
    return "No installation or vessel was close enough to test."


def _runs(r: Dict[str, Any]) -> int:
    return int(round(float(r.get("support") or 0) * int(r.get("members") or MEMBERS)))
