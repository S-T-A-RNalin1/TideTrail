import json
from pathlib import Path

latest_job = sorted(Path("data/jobs").glob("*.json"))[-1]
print("Reading job:", latest_job.name)
with open(latest_job, "r") as f:
    job = json.load(f)

print("Job ID:", job.get("job_id"))
print("Status:", job.get("status"))
print("Scene:", job.get("scene", {}).get("title"))
print("Detector:", job.get("detection", {}).get("metrics", {}).get("detector"))
polys = job.get("detection", {}).get("polygons", [])
print("Detected oil slicks count:", len(polys))
primary = job.get("primary_polygon", {}).get("properties", {})
print("Primary Slick Area:", primary.get("area_km2"), "km2")
print("Primary Slick Length:", primary.get("length_km"), "km")
print("Primary Slick Orientation:", primary.get("orientation_deg"), "deg")
print("Hindcast Origin:", job.get("drift", {}).get("origin"))
print("Age hours proxy:", job.get("age_hours_proxy"))

suspects = job.get("attribution", {}).get("suspects", [])
print(f"\n--- Ranked Culprit Vessels ({len(suspects)}) ---")
for s in suspects:
    print(f"Rank #{s.get('rank')}:")
    print(f"  MMSI: {s.get('mmsi')}")
    print(f"  Name: {s.get('vessel_name')}")
    print(f"  Type: {s.get('vessel_type')}")
    print(f"  Score: {s.get('score')}%")
    print(f"  Confidence: {s.get('track_confidence')}")
    print(f"  Subscores: {s.get('subscores')}")
    print(f"  Closest approach to origin: {s.get('closest_approach_km')} km at {s.get('closest_approach_t')}")
    print(f"  Reasons: {s.get('reasons')}")
