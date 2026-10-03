"""Cache offshore installations around every scene, for offline source checks.

Queries OpenStreetMap through Overpass for platforms tagged
man_made=offshore_platform or seamark:type=platform in a box around each
scene, and writes data/infrastructure/<scene_id>.json. The runtime reads those
files and data/infrastructure/known_sources.json; it never queries OSM itself.

    python scripts/fetch_infrastructure.py            # every indexed scene
    python scripts/fetch_infrastructure.py --margin 0.5

OSM data is (c) OpenStreetMap contributors, available under the ODbL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, scenes as scenes_mod  # noqa: E402

URL = "https://overpass-api.de/api/interpreter"
UA = "TideTrail-prep/1.0 (offline oil spill attribution prototype)"
OUT = config.DATA_DIR / "infrastructure"


def query(bbox):
    w, s, e, n = bbox
    box = "%.4f,%.4f,%.4f,%.4f" % (s, w, n, e)
    q = ("[out:json][timeout:90];("
         'node["man_made"="offshore_platform"](%s);way["man_made"="offshore_platform"](%s);'
         'node["seamark:type"="platform"](%s);way["seamark:type"="platform"](%s););'
         "out center tags;" % (box, box, box, box))
    data = urllib.parse.urlencode({"data": q}).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            wait = 15 * (attempt + 1)
            print("  overpass busy (%s), retrying in %d s" % (type(exc).__name__, wait))
            time.sleep(wait)
    raise SystemExit("Overpass did not answer for %s" % (bbox,))


def parse(doc):
    out, seen = [], set()
    for el in doc.get("elements", []):
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c.get("lat") is None:
            continue
        key = (round(c["lat"], 5), round(c["lon"], 5))
        if key in seen:
            continue
        seen.add(key)
        t = el.get("tags", {})
        name = t.get("name") or t.get("seamark:name") or t.get("ref") or t.get("operator") or "unnamed platform"
        out.append({
            "name": name,
            "kind": "offshore_platform",
            "lat": round(float(c["lat"]), 6),
            "lon": round(float(c["lon"]), 6),
            "operator": t.get("operator"),
            "osm": "%s/%s" % (el.get("type"), el.get("id")),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--margin", type=float, default=0.4, help="degrees added around each scene")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for sc in scenes_mod.all_scenes():
        w, s, e, n = sc.bounds
        bbox = (w - args.margin, s - args.margin, e + args.margin, n + args.margin)
        print("%s  %s" % (sc.id, bbox))
        items = parse(query(bbox))
        path = OUT / ("%s.json" % sc.id)
        path.write_text(json.dumps({
            "scene_id": sc.id,
            "bbox": [round(v, 4) for v in bbox],
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "OpenStreetMap via Overpass (man_made=offshore_platform, seamark:type=platform)",
            "license": "(c) OpenStreetMap contributors, ODbL 1.0",
            "installations": items,
        }, indent=1), encoding="utf-8")
        print("  %d installations -> %s" % (len(items), path))
        time.sleep(5)


if __name__ == "__main__":
    main()
