"""Super-resolution: the network, tiling, georeferencing, metrics and the API.

Everything runs on synthetic imagery, so these tests need neither a download nor
a trained checkpoint, and they pin down the properties the problem statement
cares about: the output sits exactly on a 4x finer grid, it averages back to the
input, tiling does not leave seams, and the uncertainty is scored honestly.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from app.sr import SCALE, infer, metrics
from app.sr import model as sr_model
from app.sr.data import CropSampler, Scene
from app.sr.train import loss_fn


def _small():
    torch.manual_seed(0)
    return sr_model.SRNet(sr_model.SRConfig(channels=8, blocks=2)).eval()


def _scene(h=48, w=48, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.random((4, h, w)).astype(np.float32)
    from scipy import ndimage as ndi
    return np.clip(ndi.gaussian_filter(base, (0, 1.5, 1.5)) * 0.4 + 0.05, 0, 1)


# ----------------------------------------------------------------- network ----
def test_untrained_network_is_exactly_the_bicubic_upsample():
    net = _small()
    x = torch.from_numpy(_scene())[None]
    mean, logb = net(x)
    assert mean.shape == (1, 4, 48 * SCALE, 48 * SCALE) and logb.shape == mean.shape
    assert torch.allclose(mean, sr_model.bicubic(x), atol=1e-5)


def test_checkpoint_round_trips(tmp_path):
    net = _small()
    sr_model.save(tmp_path / "m.pt", net, {"iter": 5})
    loaded, meta = sr_model.load(tmp_path / "m.pt")
    x = torch.from_numpy(_scene())[None]
    assert meta["iter"] == 5
    assert torch.allclose(net(x)[0], loaded(x)[0])


def test_uncertainty_loss_does_not_bend_the_image():
    """Asking the scale head for honest error must send no gradient into the mean."""
    mu = torch.rand(2, 4, 16, 16, requires_grad=True)
    logb = torch.zeros(2, 4, 16, 16, requires_grad=True)
    hr = torch.rand(2, 4, 16, 16)
    loss_fn(mu, logb, hr)["nll"].backward()
    assert mu.grad is None or float(mu.grad.abs().max()) == 0.0
    assert float(logb.grad.abs().max()) > 0.0


# ----------------------------------------------------------------- tiling ----
def test_tiled_inference_matches_one_pass_with_no_seams():
    net = _small()
    lr = _scene(70, 61)
    whole = net(torch.from_numpy(lr)[None])[0][0].detach().numpy()
    tiled = infer.enhance(net, lr, tile=40, overlap=12, project=False)["sr"]
    assert tiled.shape == whole.shape
    # the two treat the outermost pixels differently (the tiler reflects the image at its border, a single
    # pass does not), so compare the interior, where any difference would be a seam between tiles
    m = 8 * SCALE
    assert np.abs(np.clip(whole, 0, 1.5) - tiled)[:, m:-m, m:-m].max() < 1e-4


# ------------------------------------------------------------- consistency ----
def test_projection_makes_the_result_average_back_to_the_input():
    lr = _scene(32, 32)
    rng = np.random.default_rng(1)
    sr = np.clip(np.repeat(np.repeat(lr, SCALE, 1), SCALE, 2) + 0.03 + rng.normal(0, 0.01, (4, 128, 128)), 0, 1.5)
    before = metrics.lr_consistency(sr, lr)
    after = metrics.lr_consistency(metrics.project_consistent(sr, lr), lr)
    assert before > 0.02 and after < 0.003


def test_box_down_is_the_inverse_of_pixel_replication():
    lr = _scene(16, 16)
    up = np.repeat(np.repeat(lr, SCALE, 1), SCALE, 2)
    assert np.allclose(metrics.box_down(up), lr, atol=1e-6)


# ----------------------------------------------------------------- metrics ----
def test_identical_images_score_perfectly():
    a = _scene(64, 64)
    assert metrics.psnr(a, a) == 99.0
    assert metrics.ssim(a, a) == pytest.approx(1.0, abs=1e-6)
    assert metrics.edge_f1(a, a) == pytest.approx(1.0)
    assert metrics.sam(a, a) == pytest.approx(0.0, abs=0.2)


def test_spectral_angle_ignores_brightness_but_not_colour():
    a = _scene(32, 32) + 0.05
    assert metrics.sam(a * 1.3, a) < 0.2                 # same colours, brighter
    shifted = a.copy()
    shifted[3] *= 2.0                                    # the NIR band doubled: a different colour
    assert metrics.sam(shifted, a) > 5.0


def test_blurring_loses_edges_and_sharpness_scores():
    from scipy import ndimage as ndi
    rng = np.random.default_rng(2)
    ref = np.clip(ndi.zoom(rng.random((4, 24, 24)), (1, 8, 8), order=0) * 0.5, 0, 1)    # blocky: hard edges
    blurred = ndi.gaussian_filter(ref, (0, 3, 3))
    assert metrics.edge_f1(blurred, ref) < 0.9
    assert metrics.psnr(ref, ref) > metrics.psnr(blurred, ref)


def test_water_mask_overlap_is_scored_and_absent_water_is_not():
    ref = np.zeros((4, 60, 60), np.float32)
    ref[1], ref[3] = 0.05, 0.30                           # land: green below NIR
    ref[1, :30], ref[3, :30] = 0.10, 0.02                 # water: green above NIR
    assert metrics.water_iou(ref, ref) == pytest.approx(1.0)
    land = np.zeros_like(ref)
    land[1], land[3] = 0.05, 0.30
    assert metrics.water_iou(land, land) is None


def test_uncertainty_that_tracks_error_is_rewarded_and_noise_is_not():
    rng = np.random.default_rng(3)
    scale = rng.uniform(0.005, 0.05, (4, 80, 80))
    ref = np.zeros_like(scale)
    laplace = rng.laplace(0.0, 1.0, scale.shape) * scale
    good = metrics.uncertainty_report(ref + laplace, scale, ref)
    assert good["rank_corr"] > 0.4 and 0.85 < good["coverage_90"] < 0.95 and good["top_vs_bottom"] > 3
    blind = metrics.uncertainty_report(ref + rng.laplace(0, 0.02, scale.shape), scale, ref)
    assert abs(blind["rank_corr"]) < 0.05


# -------------------------------------------------------------------- data ----
def test_training_crops_stay_aligned_through_flips_and_turns():
    lr = _scene(48, 48)
    hr_dn = np.repeat(np.repeat(np.round(lr * 255).astype(np.uint8), SCALE, 1), SCALE, 2)
    sc = Scene("t", "train", np.round(lr * 255) / 255, hr_dn, np.tile(np.array([[1 / 255, 0.0]], np.float32), (4, 1)))
    s = CropSampler([sc], crop=16, seed=4)
    for _ in range(30):
        a, b = s.batch(2)
        assert np.allclose(metrics.box_down(b[0].numpy()), a[0].numpy(), atol=2e-3)   # the reference is stored as float16


def test_anchored_reference_agrees_with_the_input_and_keeps_its_detail():
    lr = _scene(48, 48)
    rng = np.random.default_rng(3)
    detail = rng.normal(0, 0.03, (4, 48 * SCALE, 48 * SCALE)).astype(np.float32)
    # an aerial reference that sits 0.05 away from the satellite value, plus real sub-pixel structure
    ref = np.repeat(np.repeat(lr, SCALE, 1), SCALE, 2) + 0.05 + detail
    hr_dn = np.clip(np.round(ref * 255), 0, 255).astype(np.uint8)
    sc = Scene("t", "train", lr, hr_dn, np.tile(np.array([[1 / 255, 0.0]], np.float32), (4, 1)))
    assert metrics.lr_consistency(sc.hr(raw=True), lr) > 0.04
    assert metrics.lr_consistency(sc.hr(), lr) < 0.003
    # what remains between the two references is smooth, so the fine structure survives
    hi = lambda a: a - np.repeat(np.repeat(metrics.box_down(a), SCALE, 1), SCALE, 2)
    assert np.corrcoef(hi(sc.hr()).ravel(), hi(sc.hr(raw=True)).ravel())[0, 1] > 0.95


# ----------------------------------------------------------------- GeoTIFF ----
def _write_tif(path: Path, arr, res=10.0, crs="EPSG:32643", count=None):
    import rasterio
    from rasterio.transform import from_origin
    count = count or arr.shape[0]
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=count,
                       dtype="float32", crs=crs, transform=from_origin(500000, 3000000, res, res)) as ds:
        ds.write(arr[:count].astype(np.float32))
    return path


def test_output_grid_is_four_times_finer_and_shares_the_corner(tmp_path):
    import rasterio
    lr = _scene(32, 40)
    scene = infer.read_scene(_write_tif(tmp_path / "in.tif", lr))
    net = _small()
    out = infer.enhance(net, scene.lr, tile=40, overlap=12)["sr"]
    infer.write_geotiff(tmp_path / "sr.tif", out, scene.transform, scene.crs)
    with rasterio.open(tmp_path / "sr.tif") as ds:
        assert (ds.width, ds.height) == (40 * SCALE, 32 * SCALE)
        assert abs(ds.res[0] - 2.5) < 1e-9
        assert (ds.transform.c, ds.transform.f) == (500000.0, 3000000.0)
        assert ds.crs.to_string() == "EPSG:32643"


def test_inputs_the_model_was_not_validated_for_are_refused_with_a_reason(tmp_path):
    lr = _scene(32, 32)
    with pytest.raises(infer.InputError, match="30.0 m"):
        infer.read_scene(_write_tif(tmp_path / "a.tif", lr, res=30.0))
    with pytest.raises(infer.InputError, match="3 bands"):
        infer.read_scene(_write_tif(tmp_path / "b.tif", lr[:3]))
    import rasterio
    with rasterio.open(tmp_path / "c.tif", "w", driver="GTiff", height=32, width=32, count=4, dtype="float32") as ds:
        ds.write(lr)
    with pytest.raises(infer.InputError, match="georeference"):
        infer.read_scene(tmp_path / "c.tif")


def test_an_image_too_large_to_hold_the_result_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(infer, "MAX_INPUT_PIXELS", 1000)
    with pytest.raises(infer.InputError, match="at most about 2000 x 2000"):
        infer.read_scene(_write_tif(tmp_path / "big.tif", _scene(40, 40)))


def test_digital_numbers_are_converted_to_reflectance(tmp_path):
    lr = _scene(32, 32)
    dn = lr * 10000 + 1000
    scene = infer.read_scene(_write_tif(tmp_path / "dn.tif", dn), dn_offset=1000.0)
    assert np.allclose(scene.lr, lr, atol=1e-3)


# --------------------------------------------------------------------- API ----
@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    from app.api import sr as sr_api
    from app.main import app
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    sr_model.save(tmp_path / "sr_x4.pt", _small(), {"iter": 1})
    sr_api._MODEL.clear()
    return TestClient(app)


def _post(client, path: Path, **data):
    with path.open("rb") as fh:
        return client.post("/api/sr/enhance", files={"file": (path.name, fh, "image/tiff")}, data=data)


def test_api_enhances_an_upload_and_serves_the_products(client, tmp_path):
    r = _post(client, _write_tif(tmp_path / "scene.tif", _scene(40, 40)))
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["output"]["pixel_m"] == 2.5 and doc["output"]["width"] == 160
    assert doc["quality"]["consistency_rmse"] < 0.01
    assert len(doc["bounds"]) == 2
    for url in doc["downloads"].values():
        got = client.get(url)
        assert got.status_code == 200 and len(got.content) > 1000
    urls = [doc["previews"]["uncertainty"]] + [u for k in ("colour", "ndvi", "water") for u in doc["previews"][k].values()]
    assert len(urls) == 7
    for url in urls:
        assert client.get(url).status_code == 200


def test_api_refuses_a_bad_file_with_a_plain_sentence(client, tmp_path):
    r = _post(client, _write_tif(tmp_path / "coarse.tif", _scene(40, 40), res=30.0))
    assert r.status_code == 422 and "30.0 m" in r.json()["detail"] and "{" not in r.json()["detail"]
    r = _post(client, _write_tif(tmp_path / "ok.tif", _scene(40, 40)), band_order="1,2,3")
    assert r.status_code == 422 and "four" in r.json()["detail"]


def test_api_says_so_when_no_model_is_trained(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    from app.api import sr as sr_api
    from app.main import app
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "empty")
    sr_api._MODEL.clear()
    r = _post(TestClient(app), _write_tif(tmp_path / "s.tif", _scene(40, 40)))
    assert r.status_code == 503 and "train" in r.json()["detail"]
