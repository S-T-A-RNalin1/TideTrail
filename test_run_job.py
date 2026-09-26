import urllib.request
import json
import time

url = "http://127.0.0.1:8000/api/run"
payload = {"scene_id": "gom_mc20_chronic_slick"}
print(f"Sending run request for {payload['scene_id']}...")
t0 = time.time()
req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req, timeout=120) as resp:
    job = json.loads(resp.read().decode("utf-8"))

elapsed = time.time() - t0
print(f"Job finished in {elapsed:.2f} s")
print("Job ID:", job.get("job_id"))
print("Status:", job.get("status"))
print("Detector:", job.get("detection", {}).get("metrics", {}).get("detector"))
polys = job.get("detection", {}).get("polygons", [])
print(f"Oil polygons detected: {len(polys)}")
primary = job.get("primary_polygon", {})
props = primary.get("properties", {})
print(f"Primary Slick: Area={props.get('area_km2')} km2, Length={props.get('length_km')} km, Orientation={props.get('orientation_deg')} deg, Centroid={props.get('centroid')}")

drift = job.get("drift") or {}
origin = drift.get("origin", {})
print("Hindcast Origin:", origin)
cone = drift.get("cone", {})
print(f"Forecast Cone: Land Impact={cone.get('land_impact')}, Threatened Area={cone.get('threatened_box')}")

attr = job.get("attribution") or {}
suspects = attr.get("suspects", [])
print(f"Ranked Culprit Vessels ({len(suspects)}):")
for s in suspects[:5]:
    print(f"  Rank #{s.get('rank')}: MMSI={s.get('mmsi')}, Name='{s.get('vessel_name')}', Type='{s.get('vessel_type')}', Score={s.get('score'):.1f}%, Conf={s.get('track_confidence'):.2f}, Reasons={s.get('reasons')}")
