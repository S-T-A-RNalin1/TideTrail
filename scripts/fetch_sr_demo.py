"""Cut a Sentinel-2 scene for the super-resolution console to enhance.

Writes a 4-band GeoTIFF (blue, green, red, NIR reflectance, 10 m) and a sidecar
.json into data/sr/demo/, which the console lists as a bundled scene. Indian
scenes have no free aerial reference, so what the console shows for them is the
enhancement and the model's own uncertainty, not a measured accuracy; the
sidecar says so.

Usage:
    python scripts/fetch_sr_demo.py --name jaipur --title "Jaipur city" \\
        --lat 26.9124 --lon 75.7873 --km 5 --from 2025-01-01 --to 2025-12-31
    python scripts/fetch_sr_demo.py --from-pair co_denver     # a held-out US scene, with its reference
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_sr_pairs as F                               # noqa: E402
from app import config                                   # noqa: E402

OUT = Path(config.DATA_DIR) / "sr" / "demo"


def write(name: str, lr: np.ndarray, transform, crs, info: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / ("%s.tif" % name)
    with rasterio.open(path, "w", driver="GTiff", height=lr.shape[1], width=lr.shape[2], count=4, dtype="float32",
                       crs=crs, transform=transform, compress="deflate", predictor=3) as dst:
        dst.write(lr.astype(np.float32))
        for i, d in enumerate(("blue", "green", "red", "nir"), 1):
            dst.set_band_description(i, d)
    path.with_suffix(".json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("wrote %s (%d x %d, %.1f MB)" % (path, lr.shape[2], lr.shape[1], path.stat().st_size / 1e6))
    return path


def from_pair(site: str) -> None:
    from affine import Affine
    from app.sr.data import PAIR_DIR, load_scene
    hits = list(PAIR_DIR.rglob("%s.npz" % site))
    if not hits:
        raise SystemExit("no cached pair called %s" % site)
    sc = load_scene(hits[0])
    m = sc.meta
    info = {"title": "%s, USA (held out)" % site.replace("_", " ").title(), "date": m["s2_date"][:10],
            "note": "scene the model never trained on", "has_reference": True}
    write("us_%s" % site, sc.lr, Affine(*m["transform_lr"]), m["crs"], info)


def from_web(a) -> None:
    point = {"type": "Point", "coordinates": [a.lon, a.lat]}
    side = int(a.km * 1000 / 10)
    mlat = (side * 10.0 / 2 + 400.0) / 111000.0
    import math
    mlon = mlat / math.cos(math.radians(a.lat))
    items = F._post({"collections": ["sentinel-2-l2a"], "intersects": point, "datetime": "%s/%s" % (a.date_from, a.date_to),
                     "limit": 30, "query": {"eo:cloud_cover": {"lt": 5}},
                     "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]})["features"]
    items = [c for c in items if c["bbox"][0] + mlon <= a.lon <= c["bbox"][2] - mlon and c["bbox"][1] + mlat <= a.lat <= c["bbox"][3] - mlat]
    token = F._token("sentinel-2-l2a")
    for it in items[:6]:
        got = F.read_s2(it, a.lon, a.lat, side, token)
        if got is None:
            continue
        lr, clear, tf, crs = got
        if clear < 0.99:
            continue
        info = {"title": a.title or a.name, "date": it["properties"]["datetime"][:10], "note": a.note,
                "has_reference": False, "s2": it["id"], "cloud": it["properties"]["eo:cloud_cover"]}
        write(a.name, lr, tf, crs, info)
        return
    raise SystemExit("no clear Sentinel-2 scene found for that place and period")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name")
    ap.add_argument("--title")
    ap.add_argument("--note", default="no aerial reference exists for this place")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--km", type=float, default=5.0)
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2025-12-31")
    ap.add_argument("--from-pair")
    a = ap.parse_args()
    if a.from_pair:
        from_pair(a.from_pair)
    else:
        if not (a.name and a.lat is not None and a.lon is not None):
            ap.error("give --name, --lat and --lon, or --from-pair")
        from_web(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
