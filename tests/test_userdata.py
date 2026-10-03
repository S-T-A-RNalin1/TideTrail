"""Operator uploads: a radar pass, an AIS file and an optical chip, end to end.

Everything runs against a throwaway data directory, so the shipped scenes and
the AIS store are never touched.
"""
from __future__ import annotations

import csv
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app import config, scenes as scenes_mod, userdata
from app.drift import land
from app.geo import raster as raster_mod

T_SAT = "2025-05-28T00:41:25Z"
W, N = 76.20, 9.60          # chip corner, a few km off the Kerala coast
PX = 0.0001                 # about 11 m


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    real = Path(config.DATA_DIR)
    d = tmp_path / "data"
    (d / "land").mkdir(parents=True)
    shutil.copy(real / "land" / "world_land_detail.json", d / "land" / "world_land_detail.json")
    monkeypatch.setattr(config, "DATA_DIR", d)
    monkeypatch.setattr(config, "SAR_DIR", d / "sar")
    monkeypatch.setattr(config, "SCENES_INDEX", d / "sar" / "scenes.json")
    monkeypatch.setattr(config, "METOCEAN_DIR", d / "metocean")
    monkeypatch.setattr(config, "AIS_SQLITE", d / "ais" / "ais.sqlite")
    for sub in ("sar", "metocean", "ais"):
        (d / sub).mkdir()
    land.reload()
    yield d
    land.reload()


def _chip(path: Path, georef: bool = True, size: int = 512) -> Path:
    rng = np.random.default_rng(3)
    arr = (-22.0 + rng.normal(0, 1.5, (1, size, size))).astype(np.float32)
    if georef:
        raster_mod.write_geotiff(path, arr, (PX, 0.0, W, 0.0, -PX, N), "EPSG:4326")
    else:
        from PIL import Image
        Image.fromarray(((arr[0] + 30) * 8).clip(0, 255).astype(np.uint8)).save(path)
    return path


def _ais_csv(path: Path, columns=("MMSI", "BaseDateTime", "LAT", "LON", "SOG", "COG", "VesselName")) -> Path:
    t0 = datetime(2025, 5, 27, 20, 0, tzinfo=timezone.utc)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for k in range(60):
            t = t0 + timedelta(minutes=5 * k)
            row = {"MMSI": 419000001, "BaseDateTime": t.strftime("%Y-%m-%dT%H:%M:%S"),
                   "LAT": N - 0.02, "LON": W + 0.001 * k, "SOG": 11.0, "COG": 90.0, "VesselName": "TEST TANKER"}
            w.writerow([row[c] for c in columns])
    return path


def test_radar_needs_a_pass_time(data_dir, tmp_path):
    with pytest.raises(userdata.UploadError, match="pass time"):
        userdata.register_sar(_chip(tmp_path / "a.tif"), "a.tif", None)


def test_radar_without_georeference_is_refused(data_dir, tmp_path):
    with pytest.raises(userdata.UploadError, match="georeference"):
        userdata.register_sar(_chip(tmp_path / "flat.tif", georef=False), "flat.tif", T_SAT)


def test_radar_is_registered_apart_from_the_prepared_scenes(data_dir, tmp_path):
    out = userdata.register_sar(_chip(tmp_path / "pass.tif"), "pass.tif", T_SAT, "Kerala pass")
    sc = scenes_mod.get(out["scene"]["id"])
    assert sc is not None and sc.title == "Kerala pass"
    assert sc.t_sat == T_SAT and sc.ais_mode == "none"
    assert Path(sc.sar_path).parent == data_dir / "uploads"
    assert not (data_dir / "sar" / "scenes.json").exists()
    assert any("No AIS" in n for n in out["notes"])
    # the coast is masked from the bundled world file, and the chip is not all land
    assert any("1:50m" in n for n in out["notes"])
    assert land.on_land(76.50, 9.55)[0] and not land.on_land(76.10, 9.55)[0]


def test_ais_upload_turns_the_scene_to_recorded(data_dir, tmp_path):
    out = userdata.register_sar(_chip(tmp_path / "pass.tif"), "pass.tif", T_SAT)
    res = userdata.ingest_ais(_ais_csv(tmp_path / "feed.csv"), "feed.csv")
    assert res["rows"] == 60 and res["vessels"] == 1
    assert scenes_mod.get(out["scene"]["id"]).ais_mode == "real"
    # loading the same file again replaces it rather than doubling it
    assert userdata.ingest_ais(_ais_csv(tmp_path / "feed.csv"), "feed.csv")["rows"] == 60


def test_ais_without_positions_is_refused(data_dir, tmp_path):
    with pytest.raises(userdata.UploadError, match="LAT, LON"):
        userdata.ingest_ais(_ais_csv(tmp_path / "bad.csv", columns=("MMSI", "BaseDateTime", "SOG")), "bad.csv")


def test_optical_is_resampled_onto_the_scene_and_removed_with_it(data_dir, tmp_path):
    out = userdata.register_sar(_chip(tmp_path / "pass.tif"), "pass.tif", T_SAT)
    sid = out["scene"]["id"]
    rgb = np.stack([np.full((600, 600), v, np.float32) for v in (40, 80, 120)])
    raster_mod.write_geotiff(tmp_path / "s2.tif", rgb, (PX, 0.0, W - 0.005, 0.0, -PX, N + 0.005), "EPSG:4326")
    meta = userdata.register_optical(tmp_path / "s2.tif", "s2.tif", sid, "2025-05-28T05:20:00Z")
    assert meta["status"] == "ok" and abs(meta["offset_hours"] - 4.64) < 0.05
    assert (data_dir / "optical" / ("%s.png" % sid)).exists()
    userdata.remove_scene(sid)
    assert scenes_mod.get(sid) is None
    assert not (data_dir / "optical" / ("%s.png" % sid)).exists()


def test_prepared_scenes_cannot_be_removed(data_dir):
    with pytest.raises(userdata.UploadError):
        userdata.remove_scene("gom_mc20_chronic_slick")
