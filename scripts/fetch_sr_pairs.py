"""Build paired training data for Sentinel-2 super-resolution.

Problem statement 26142 asks for 10 m Sentinel-2 imagery to be turned into a
product finer than 4 m, trained on paired data and validated against
high-resolution references. The pair built here is:

    input      Sentinel-2 L2A, bands B2 B3 B4 B8 (blue, green, red, NIR), 10 m
    reference  NAIP aerial imagery, bands B G R NIR, resampled to a 2.5 m grid
               that sits exactly on the Sentinel-2 grid (four reference pixels
               to one input pixel), so a 4x model has a pixel-for-pixel target

NAIP (US National Agriculture Imagery Program) is public domain and carries a
near-infrared band like Sentinel-2, which is why it is the reference. Both
sources are read from Microsoft Planetary Computer, a free STAC service,
so no account is needed. Run this once while online;
everything downstream reads the cached pairs.

Three problems stand between two such images and a trustworthy training pair,
and each is handled and recorded:

  registration   the two products are geolocated independently, so one is shifted
                 against the other by a fraction of a Sentinel-2 pixel. The shift
                 is measured by phase correlation and removed, and a scene whose
                 shift is large or unclear is dropped.
  radiometry     NAIP is stretched to 8 bits and Sentinel-2 is surface
                 reflectance. A per-band linear map is fitted so the reference,
                 averaged back to 10 m, agrees with the input. The fit's
                 correlation is the quality score, and weak scenes are dropped.
  time           the two were not taken together. Sentinel-2 is chosen within
                 DAYS of the NAIP flight, cloud free over the whole window.

Sites are split by region, not by tile: the test sites are in states that
contribute nothing to training, so the result measures generalisation to new
land cover and climate rather than memorisation of nearby fields.

Usage:
    python scripts/fetch_sr_pairs.py --limit 3          # try three sites
    python scripts/fetch_sr_pairs.py                    # every site, resumable
    python scripts/fetch_sr_pairs.py --split test
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# set before rasterio opens anything: fewer, larger range requests
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_CHUNK_SIZE", "1048576")
os.environ.setdefault("VSI_CACHE", "TRUE")

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config                                  # noqa: E402

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/%s"
UA = {"User-Agent": "TideTrail-SR/1.0 (academic project)", "Content-Type": "application/json"}

OUT_DIR = Path(config.DATA_DIR) / "sr" / "pairs"
LOG = Path(config.DATA_DIR) / "sr" / "fetch_log.jsonl"

SCALE = 4                    # 10 m -> 2.5 m
LR_PX = 320                  # scene side in Sentinel-2 pixels, 3.2 km
MARGIN = 8                   # extra Sentinel-2 pixels fetched, then cut after registration
DAYS = 35                    # largest gap between the NAIP flight and the Sentinel-2 pass
CLOUD_MAX = 10.0
S2_BANDS = ("B02", "B03", "B04", "B08")          # blue, green, red, NIR
NAIP_ORDER = (2, 1, 0, 3)                        # NAIP is R G B NIR; canonical is B G R NIR
SCL_BAD = (0, 1, 3, 8, 9, 10, 11)                # no data, saturated, shadow, cloud, cirrus, snow
MAX_SHIFT_PX = 2.0
MIN_CORR = 0.70

# name, lat, lon, land cover, split.  Test sites are in states with no training site.
SITES: List[Tuple[str, float, float, str, str]] = [
    # California
    ("ca_davis", 38.55, -121.75, "agriculture", "train"), ("ca_sacramento", 38.58, -121.49, "urban", "train"),
    ("ca_fresno", 36.74, -119.79, "agriculture", "train"), ("ca_losangeles", 34.05, -118.25, "urban", "train"),
    ("ca_sandiego", 32.75, -117.20, "coast", "train"), ("ca_salinas", 36.62, -121.60, "agriculture", "train"),
    ("ca_modesto", 37.64, -120.99, "mixed", "train"), ("ca_oakland", 37.80, -122.27, "urban", "train"),
    ("ca_sierra", 37.80, -119.50, "forest", "train"), ("ca_bakersfield", 35.37, -119.02, "mixed", "val"),
    # Pacific northwest
    ("or_portland", 45.52, -122.67, "urban", "train"), ("or_willamette", 44.95, -123.04, "agriculture", "train"),
    ("or_cascades", 44.20, -122.00, "forest", "train"), ("wa_seattle", 47.60, -122.33, "urban", "train"),
    ("wa_yakima", 46.60, -120.50, "agriculture", "val"),
    # Southwest
    ("az_phoenix", 33.45, -112.07, "urban", "train"), ("az_tucson", 32.22, -110.97, "urban", "train"),
    ("az_yuma", 32.70, -114.60, "agriculture", "train"), ("ut_saltlake", 40.76, -111.89, "urban", "val"),
    ("id_boise", 43.62, -116.20, "mixed", "val"),
    # Texas and Plains
    ("tx_houston", 29.76, -95.37, "urban", "train"), ("tx_dallas", 32.78, -96.80, "urban", "train"),
    ("tx_lubbock", 33.58, -101.85, "agriculture", "train"), ("tx_austin", 30.27, -97.74, "mixed", "train"),
    ("tx_rgv", 26.20, -98.20, "agriculture", "val"), ("ne_omaha", 41.26, -95.93, "urban", "val"),
    ("ks_wheat", 38.00, -98.50, "agriculture", "train"), ("ok_okc", 35.47, -97.52, "urban", "val"),
    ("ks_wichita", 37.69, -97.34, "urban", "train"),
    # Midwest
    ("ia_desmoines", 41.59, -93.62, "urban", "train"), ("ia_corn", 42.00, -93.60, "agriculture", "train"),
    ("il_chicago", 41.88, -87.63, "urban", "train"), ("il_champaign", 40.10, -88.20, "agriculture", "val"),
    ("in_indianapolis", 39.77, -86.16, "urban", "train"), ("in_fortwayne", 41.00, -85.10, "agriculture", "val"),
    # Southeast
    ("fl_miami", 25.77, -80.19, "urban", "train"), ("fl_orlando", 28.54, -81.38, "mixed", "train"),
    ("fl_everglades", 25.50, -80.90, "wetland", "train"), ("fl_tampa", 27.95, -82.46, "urban", "train"),
    ("fl_gainesville", 29.65, -82.32, "forest", "train"), ("ga_atlanta", 33.75, -84.39, "urban", "train"),
    ("ga_savannah", 32.08, -81.09, "coast", "val"), ("al_birmingham", 33.52, -86.80, "mixed", "val"),
    # Northeast
    ("ny_nyc", 40.71, -74.00, "urban", "train"), ("ny_buffalo", 42.89, -78.88, "urban", "train"),
    ("pa_lancaster", 40.04, -76.30, "agriculture", "val"), ("pa_philadelphia", 39.95, -75.17, "urban", "train"),
    # held-out regions: nothing from these states is used for training
    ("co_denver", 39.74, -104.99, "urban", "test"), ("co_fortcollins", 40.58, -105.08, "agriculture", "test"),
    ("co_rockies", 39.50, -106.00, "forest", "test"),
    ("mn_minneapolis", 44.98, -93.27, "urban", "test"), ("mn_farm", 44.50, -94.50, "agriculture", "test"),
    ("mn_lakes", 47.00, -94.00, "forest", "test"),
    ("nc_raleigh", 35.78, -78.64, "urban", "test"), ("nc_charlotte", 35.23, -80.84, "urban", "test"),
    ("nc_outerbanks", 35.25, -75.55, "coast", "test"),
    ("la_neworleans", 29.95, -90.07, "urban", "test"), ("la_atchafalaya", 30.00, -91.60, "wetland", "test"),
    ("nv_lasvegas", 36.17, -115.14, "urban", "test"), ("nv_reno", 39.53, -119.81, "mixed", "test"),
    ("tn_nashville", 36.16, -86.78, "urban", "test"), ("tn_memphis", 35.15, -90.05, "mixed", "test"),
    ("va_richmond", 37.54, -77.43, "urban", "test"), ("me_forest", 45.30, -69.00, "forest", "test"),
    ("mt_wheat", 47.50, -111.30, "agriculture", "test"), ("wi_farm", 43.50, -89.50, "agriculture", "test"),
]


# --------------------------------------------------------------------- web ----
def _post(body: dict, tries: int = 3) -> dict:
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(STAC, data=json.dumps(body).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as exc:                    # network is the flaky part
            last = exc
            time.sleep(2 * (k + 1))
    raise RuntimeError("STAC request failed: %s" % last)


_TOKENS: Dict[str, Tuple[float, str]] = {}
_TOKEN_FILE = Path(config.DATA_DIR) / "sr" / ".tokens.json"


def _token(collection: str) -> str:
    """A read token for a collection, shared by every worker and every process.

    Kept for 20 minutes in memory and in a small file, since several workers
    asking at once is what the service pushes back on."""
    now = time.time()
    held = _TOKENS.get(collection)
    if held and now - held[0] < 1200:
        return held[1]
    try:
        disk = json.loads(_TOKEN_FILE.read_text(encoding="utf-8")).get(collection)
        if disk and now - disk[0] < 1200:
            _TOKENS[collection] = (disk[0], disk[1])
            return disk[1]
    except (OSError, ValueError):
        pass
    last = None
    for k in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(SAS % collection, headers=UA), timeout=60) as r:
                tok = json.load(r)["token"]
            _TOKENS[collection] = (time.time(), tok)
            try:
                try:
                    cur = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cur = {}
                cur[collection] = [time.time(), tok]
                _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                _TOKEN_FILE.write_text(json.dumps(cur), encoding="utf-8")
            except OSError:
                pass
            return tok
        except Exception as exc:
            last = exc
            time.sleep(4 * (k + 1))
    raise RuntimeError("could not get a read token for %s: %s" % (collection, last))


def _href(item: dict, key: str, token: str) -> str:
    return "/vsicurl/%s?%s" % (item["assets"][key]["href"], token)


# ------------------------------------------------------------- registration ----
def phase_shift(ref: np.ndarray, mov: np.ndarray) -> Tuple[float, float, float]:
    """(dy, dx, peak) so that shifting `mov` by (dy, dx) lays it on `ref`.

    Phase correlation ignores differences in brightness and contrast between
    the two images, which is exactly the mismatch between NAIP and Sentinel-2.
    The peak is refined to a fraction of a pixel by fitting a parabola through
    it and its neighbours.
    """
    h, w = ref.shape
    win = np.outer(np.hanning(h), np.hanning(w))
    a = (ref - ref.mean()) * win
    b = (mov - mov.mean()) * win
    cross = np.fft.fft2(a) * np.conj(np.fft.fft2(b))
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.ifft2(cross).real
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak = float(corr[py, px])

    def refine(c0, c1, c2):
        d = c0 - 2 * c1 + c2
        return 0.0 if abs(d) < 1e-12 else 0.5 * (c0 - c2) / d

    sub_y = refine(corr[(py - 1) % h, px], corr[py, px], corr[(py + 1) % h, px])
    sub_x = refine(corr[py, (px - 1) % w], corr[py, px], corr[py, (px + 1) % w])
    dy, dx = py + sub_y, px + sub_x
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return float(dy), float(dx), peak


def box_down(a: np.ndarray, k: int = SCALE) -> np.ndarray:
    c, h, w = a.shape
    return a.reshape(c, h // k, k, w // k, k).mean(axis=(2, 4))


def register_and_harmonise(lr: np.ndarray, hr: np.ndarray) -> Optional[Dict[str, Any]]:
    """Remove the shift between the pair and map the reference onto reflectance.

    lr is (4, H, W) reflectance; hr is (4, 4H, 4W) NAIP digital numbers on the
    same ground. Returns the registered hr, its cut-down lr, and the quality
    numbers, or None when the pair cannot be trusted.
    """
    lo = box_down(hr.astype(np.float32))
    shifts, peaks = [], []
    for k in range(4):
        dy, dx, pk = phase_shift(lr[k], lo[k])
        shifts.append((dy, dx))
        peaks.append(pk)
    dy = float(np.median([s[0] for s in shifts]))
    dx = float(np.median([s[1] for s in shifts]))
    if max(abs(dy), abs(dx)) > MAX_SHIFT_PX or float(np.median(peaks)) < 0.05:
        return {"ok": False, "why": "shift %.1f, %.1f px, peak %.2f" % (dy, dx, float(np.median(peaks)))}
    moved = np.stack([ndi.shift(hr[k].astype(np.float32), (dy * SCALE, dx * SCALE), order=1, mode="nearest")
                      for k in range(4)])
    m = MARGIN
    lr_c = lr[:, m:-m, m:-m]
    hr_c = moved[:, m * SCALE:-m * SCALE, m * SCALE:-m * SCALE]
    lo_c = box_down(hr_c)
    coef, corr = [], []
    for k in range(4):
        x, y = lo_c[k].ravel(), lr_c[k].ravel()
        keep = (y > np.percentile(y, 1)) & (y < np.percentile(y, 99))
        a, b = np.polyfit(x[keep], y[keep], 1)
        coef.append((a, b))
        corr.append(float(np.corrcoef(x[keep], y[keep])[0, 1]))
    return {"ok": True, "lr": lr_c, "hr": hr_c, "coef": np.array(coef, np.float32), "corr": corr,
            "shift_px": (dy, dx), "peak": float(np.median(peaks))}


# --------------------------------------------------------------------- read ----
def _retry(fn):
    """Range reads over HTTP fail now and then; try a read three times before giving up."""
    def wrapped(*args, **kwargs):
        last = None
        for k in range(3):
            try:
                return fn(*args, **kwargs)
            except rasterio.errors.RasterioIOError as exc:
                last = exc
                time.sleep(3 * (k + 1))
        raise last
    wrapped.__name__ = fn.__name__
    return wrapped


@_retry
def read_s2(item: dict, lon: float, lat: float, side: int, token: str):
    """Reflectance (4, side, side) and a clear-sky fraction, on the item's own 10 m grid."""
    with rasterio.open(_href(item, S2_BANDS[2], token)) as ref:
        x, y = Transformer.from_crs(4326, ref.crs, always_xy=True).transform(lon, lat)
        row, col = ref.index(x, y)
        r0, c0 = int(row - side // 2), int(col - side // 2)
        if r0 < 0 or c0 < 0 or r0 + side > ref.height or c0 + side > ref.width:
            return None
        win = Window(c0, r0, side, side)
        transform = ref.window_transform(win)
        crs = ref.crs
    bands = []
    for b in S2_BANDS:
        with rasterio.open(_href(item, b, token)) as ds:
            bands.append(ds.read(1, window=win).astype(np.float32))
    # the classification layer is 20 m: select its pixels by ground bounds, not by the 10 m indices
    bounds = (transform.c, transform.f + transform.e * side, transform.c + transform.a * side, transform.f)
    with rasterio.open(_href(item, "SCL", token)) as ds:
        scl = ds.read(1, window=rasterio.windows.from_bounds(*bounds, transform=ds.transform),
                      out_shape=(side, side), resampling=Resampling.nearest, boundless=True, fill_value=0)
    baseline = str(item["properties"].get("s2:processing_baseline", "0"))
    offset = 1000.0 if baseline >= "04.00" else 0.0
    stack = np.stack(bands)
    nodata = (stack == 0).any(axis=0)
    refl = np.clip((stack - offset) / 10000.0, 0.0, 1.5)
    bad = np.isin(scl, SCL_BAD) | nodata
    return refl, 1.0 - float(bad.mean()), transform, crs


@_retry
def read_naip(item: dict, transform: Affine, crs, side: int, token: str) -> Optional[np.ndarray]:
    """NAIP on a 2.5 m grid aligned to the Sentinel-2 window, as (4, 4*side, 4*side) in B G R NIR."""
    with rasterio.open(_href(item, "image", token)) as ds:
        res = abs(ds.res[0])
        levels = [1] + list(ds.overviews(1))
        usable = [l for l in levels if l * res <= 2.5 + 1e-6]
        if not usable:
            return None
        lvl = max(usable)
        bounds = (transform.c, transform.f + transform.e * side, transform.c + transform.a * side, transform.f)
        w, s, e, n = transform_bounds(crs, ds.crs, *bounds, densify_pts=21)
        pad = 3 * res * lvl
        win = rasterio.windows.from_bounds(w - pad, s - pad, e + pad, n + pad, ds.transform)
        c0 = int(math.floor(win.col_off / lvl) * lvl)
        r0 = int(math.floor(win.row_off / lvl) * lvl)
        c1 = int(math.ceil((win.col_off + win.width) / lvl) * lvl)
        r1 = int(math.ceil((win.row_off + win.height) / lvl) * lvl)
        if c0 < 0 or r0 < 0 or c1 > ds.width or r1 > ds.height:
            return None
        arr = ds.read(window=Window(c0, r0, c1 - c0, r1 - r0),
                      out_shape=(ds.count, (r1 - r0) // lvl, (c1 - c0) // lvl),
                      resampling=Resampling.average).astype(np.float32)
        src_tf = ds.transform * Affine.translation(c0, r0) * Affine.scale(lvl, lvl)
        src_crs = ds.crs
    if arr.shape[0] < 4 or (arr[:4].sum(axis=0) == 0).mean() > 0.01:
        return None
    dst_tf = Affine(transform.a / SCALE, 0, transform.c, 0, transform.e / SCALE, transform.f)
    out = np.zeros((4, side * SCALE, side * SCALE), np.float32)
    reproject(arr[list(NAIP_ORDER)], out, src_transform=src_tf, src_crs=src_crs, dst_transform=dst_tf,
              dst_crs=crs, resampling=Resampling.average)
    return out


# ---------------------------------------------------------------------- site ----
def build_site(site: Tuple[str, float, float, str, str], force: bool = False) -> Dict[str, Any]:
    name, lat, lon, cover, split = site
    out = OUT_DIR / split / ("%s.npz" % name)
    if out.exists() and not force:
        return {"site": name, "status": "cached"}
    t0 = time.time()
    point = {"type": "Point", "coordinates": [lon, lat]}
    naip_items = _post({"collections": ["naip"], "intersects": point, "limit": 50})["features"]
    naip_items = [f for f in naip_items if f["properties"]["datetime"] >= "2019"]
    naip_items.sort(key=lambda f: f["properties"]["datetime"], reverse=True)
    if not naip_items:
        return {"site": name, "status": "skipped", "why": "no NAIP since 2019"}
    tk_naip, tk_s2 = _token("naip"), _token("sentinel-2-l2a")
    side = LR_PX + 2 * MARGIN
    why: List[str] = []
    naip_cache: Dict[Any, Optional[np.ndarray]] = {}
    mlat = (side * 10.0 / 2 + 400.0) / 111000.0           # half the window plus a margin, in degrees
    for naip in naip_items[:6]:
        w, s_, e, n = naip["bbox"]
        mlon = mlat / max(0.2, math.cos(math.radians(lat)))
        if e - w < 2 * mlon or n - s_ < 2 * mlat:
            continue
        # keep the window inside this flight quad: slide the centre in from the edge if need be
        clon = min(max(lon, w + mlon), e - mlon)
        clat = min(max(lat, s_ + mlat), n - mlat)
        d0 = datetime.fromisoformat(naip["properties"]["datetime"].replace("Z", "+00:00"))
        rng = "%s/%s" % ((d0 - timedelta(days=DAYS)).strftime("%Y-%m-%d"), (d0 + timedelta(days=DAYS)).strftime("%Y-%m-%d"))
        cands = _post({"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [clon, clat]},
                       "datetime": rng, "limit": 20, "query": {"eo:cloud_cover": {"lt": CLOUD_MAX}},
                       "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]})["features"]
        cands = [c for c in cands if c["bbox"][0] + mlon <= clon <= c["bbox"][2] - mlon
                 and c["bbox"][1] + mlat <= clat <= c["bbox"][3] - mlat]
        if not cands:
            why.append("no clear Sentinel-2 in %s" % rng)
            continue
        for s2 in cands[:3]:
            try:
                got = read_s2(s2, clon, clat, side, tk_s2)
                if got is None:
                    why.append("window off tile")
                    continue
                lr, clear, tf, crs = got
                if clear < 0.98:
                    why.append("clear %.2f" % clear)
                    continue
                gkey = (naip["id"], tuple(round(v, 3) for v in list(tf)[:6]), str(crs))
                if gkey not in naip_cache:
                    naip_cache[gkey] = read_naip(naip, tf, crs, side, tk_naip)
                hr = naip_cache[gkey]
                if hr is None:
                    why.append("naip window")
                    continue
                res = register_and_harmonise(lr, hr)
                if not res["ok"]:
                    why.append(res["why"])
                    continue
                if min(res["corr"]) < MIN_CORR:
                    why.append("corr %s" % ["%.2f" % c for c in res["corr"]])
                    continue
                if float(np.std(res["lr"][3])) < 0.01:
                    why.append("flat scene")
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                hr8 = np.clip(np.rint(res["hr"]), 0, 255).astype(np.uint8)
                lr16 = np.clip(np.rint(res["lr"] * 10000.0), 0, 65535).astype(np.uint16)
                tf_c = tf * Affine.translation(MARGIN, MARGIN)
                meta = {
                    "site": name, "split": split, "cover": cover, "lat": clat, "lon": clon, "crs": str(crs),
                    "transform_lr": list(tf_c)[:6], "naip": naip["id"], "naip_date": naip["properties"]["datetime"],
                    "naip_gsd": naip["properties"].get("gsd"), "s2": s2["id"], "s2_date": s2["properties"]["datetime"],
                    "s2_cloud": s2["properties"]["eo:cloud_cover"], "clear_fraction": round(clear, 4),
                    "shift_px": [round(v, 3) for v in res["shift_px"]], "peak": round(res["peak"], 3),
                    "corr": [round(c, 3) for c in res["corr"]], "seconds": round(time.time() - t0, 1),
                }
                np.savez_compressed(out, lr=lr16, hr=hr8, coef=res["coef"], meta=np.array(json.dumps(meta)))
                return {"site": name, "status": "ok", **{k: meta[k] for k in ("naip_date", "s2_date", "shift_px", "corr", "seconds")}}
            except Exception as exc:
                why.append("%s: %s" % (type(exc).__name__, str(exc)[:80]))
    return {"site": name, "status": "skipped", "why": "; ".join(why[:6])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=("train", "val", "test"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--reverse", action="store_true", help="work the list from the end, to share it with another run")
    args = ap.parse_args()

    sites = [s for s in SITES if (not args.split or s[4] == args.split) and (not args.only or s[0] in args.only)]
    if args.reverse:
        sites = sites[::-1]
    if args.limit:
        sites = sites[:args.limit]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex, LOG.open("a", encoding="utf-8") as log:
        futs = {ex.submit(build_site, s, args.force): s for s in sites}
        for f in as_completed(futs):
            s = futs[f]
            try:
                r = f.result()
            except Exception as exc:
                r = {"site": s[0], "status": "error", "why": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
            done += 1
            log.write(json.dumps(r, default=str) + "\n")
            log.flush()
            print("[%d/%d %4.0fs] %-18s %-8s %s" % (done, len(sites), time.time() - t0, r["site"], r["status"],
                  r.get("why") or ("corr %s shift %s" % (r.get("corr"), r.get("shift_px")) if r["status"] == "ok" else "")),
                  flush=True)
    n = sum(1 for _ in OUT_DIR.rglob("*.npz"))
    print("pairs on disk: %d" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
