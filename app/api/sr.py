"""Super-resolution mapping: Sentinel-2 in, 2.5 m imagery and an uncertainty map out.

    POST /api/sr/enhance            multipart upload of a Sentinel-2 GeoTIFF
    POST /api/sr/demo/{name}        the same on a bundled scene
    GET  /api/sr/demos              the bundled scenes
    GET  /api/sr/jobs/{id}/progress tile progress of a run in flight
    GET  /api/sr/jobs/{id}/download/{sr|uncertainty}.tif
    GET  /api/sr/validation         what the model has been measured to do

A file that cannot be used is refused with a sentence saying why, and nothing is
left half written.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .. import config
from ..sr import SCALE, infer, metrics
from ..sr import model as sr_model

router = APIRouter()

MAX_UPLOAD_MB = 1024
CHUNK = 1 << 20
PREVIEW_MAX = 2600
_PROGRESS: Dict[str, Dict[str, Any]] = {}
_MODEL: Dict[str, Any] = {}
_LOCK = threading.Lock()


def out_root() -> Path:
    d = Path(config.DATA_DIR) / "sr" / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def demo_dir() -> Path:
    return Path(config.DATA_DIR) / "sr" / "demo"


def model_path() -> Path:
    return Path(config.MODELS_DIR) / "sr_x4.pt"


def get_model():
    """The checkpoint, loaded once and reloaded if the file changes."""
    path = model_path()
    if not path.exists():
        raise HTTPException(503, "No super-resolution model is trained yet. Run python -m app.sr.train, or copy "
                                 "a trained models/sr_x4.pt into place.")
    stamp = path.stat().st_mtime
    with _LOCK:
        if _MODEL.get("stamp") != stamp:
            net, meta = sr_model.load(path)
            _MODEL.update(stamp=stamp, net=net, meta=meta)
        return _MODEL["net"], _MODEL["meta"]


def validation_path() -> Path:
    return Path(config.DATA_DIR) / "validation" / "sr_metrics.json"


def load_validation() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(validation_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ the run ----
def run_enhance(path: Path, name: str, band_order, dn_offset: float, tta: bool, project: bool,
                job_id: str) -> Dict[str, Any]:
    net, meta = get_model()
    try:
        scene = infer.read_scene(path, tuple(band_order), dn_offset)
    except infer.InputError as exc:
        raise HTTPException(422, str(exc)) from exc
    lr = scene.lr
    _, h, w = lr.shape
    _PROGRESS[job_id] = {"done": 0, "total": 1, "state": "running"}

    def tick(done: int, total: int) -> None:
        _PROGRESS[job_id] = {"done": done, "total": total, "state": "running"}

    t0 = time.time()
    raw = infer.enhance(net, lr, tta=tta, project=False, progress=tick)
    sr = raw["sr"]
    if project:
        sr = np.clip(metrics.project_consistent(sr, lr), 0.0, 1.5)
    scale = raw["scale"]

    folder = out_root() / job_id
    folder.mkdir(parents=True, exist_ok=True)
    infer.write_geotiff(folder / "sr.tif", sr, scene.transform, scene.crs,
                        descriptions=["blue", "green", "red", "nir"])
    infer.write_geotiff(folder / "uncertainty.tif", scale, scene.transform, scene.crs,
                        descriptions=["blue", "green", "red", "nir"])

    # previews, drawn with one shared colour stretch so the three are comparable
    cache = Path(config.CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)
    k = max(1, int(np.ceil(max(h, w) * SCALE / PREVIEW_MAX)))
    inp_img, lo, hi = infer.stretch_rgb(lr)
    sr_small = sr[:, ::k, ::k] if k > 1 else sr
    sr_img, _, _ = infer.stretch_rgb(sr_small, lo, hi)
    unc_img = infer.uncertainty_rgb(scale[:, ::k, ::k] if k > 1 else scale)
    for tag, img in (("input", inp_img), ("sr", sr_img), ("uncertainty", unc_img)):
        infer.save_png(cache / ("%s_%s.png" % (job_id, tag)), img)
    # the same two images as the problem statement's applications see them: vegetation and open water
    for tag, fn in (("ndvi", infer.ndvi_rgb), ("water", infer.water_rgb)):
        infer.save_png(cache / ("%s_%s_input.png" % (job_id, tag)), fn(lr))
        infer.save_png(cache / ("%s_%s_sr.png" % (job_id, tag)), fn(sr_small))

    # bounds for the map
    from pyproj import Transformer
    ty = scene.transform
    west, north = ty.c, ty.f
    east, south = ty.c + ty.a * w, ty.f + ty.e * h
    tr = Transformer.from_crs(scene.crs, 4326, always_xy=True)
    xs, ys = tr.transform([west, east, west, east], [north, north, south, south])
    bounds = [[float(min(ys)), float(min(xs))], [float(max(ys)), float(max(xs))]]

    cons_raw, cons_out = metrics.lr_consistency(raw["sr"], lr), metrics.lr_consistency(sr, lr)
    mean_refl = float(sr.mean())
    u = scale.mean(axis=0)
    bright = float(((lr > 0.30).all(axis=0)).mean())
    notes = list(scene.notes)
    if bright > 0.05:
        notes.append("%.0f%% of the scene is very bright in every band, which usually means cloud. "
                     "Detail on cloud is invented, not recovered." % (bright * 100))
    notes.append("Where the uncertainty map is dark, the detail was inferred by the model, not observed by the satellite.")
    px_in = abs(scene.transform.a) if not scene.crs.is_geographic else abs(scene.transform.a) * 111320.0
    doc = {
        "job_id": job_id, "name": name, "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input": {"width": w, "height": h, "pixel_m": round(px_in, 2), "crs": str(scene.crs)},
        "output": {"width": w * SCALE, "height": h * SCALE, "pixel_m": round(px_in / SCALE, 2), "scale": SCALE},
        "bounds": bounds,
        "previews": {
            "colour": {"input": "/data/cache/%s_input.png" % job_id, "sr": "/data/cache/%s_sr.png" % job_id},
            "ndvi": {"input": "/data/cache/%s_ndvi_input.png" % job_id, "sr": "/data/cache/%s_ndvi_sr.png" % job_id},
            "water": {"input": "/data/cache/%s_water_input.png" % job_id, "sr": "/data/cache/%s_water_sr.png" % job_id},
            "uncertainty": "/data/cache/%s_uncertainty.png" % job_id,
        },
        "downloads": {"sr": "/api/sr/jobs/%s/download/sr.tif" % job_id,
                      "uncertainty": "/api/sr/jobs/%s/download/uncertainty.tif" % job_id},
        "quality": {
            "consistency_rmse_raw": cons_raw, "consistency_rmse": cons_out,
            "consistency_pct": 100.0 * cons_out / max(1e-9, float(lr.mean())),
            "mean_uncertainty": float(u.mean()), "uncertainty_pct": 100.0 * float(u.mean()) / max(1e-9, mean_refl),
            "high_uncertainty_share": float((u > 2.0 * np.median(u)).mean()),
            "ndvi_mean_input": float(metrics.ndvi(lr).mean()),
            "ndvi_mean_output": float(metrics.ndvi(metrics.box_down(sr)).mean()),
        },
        "settings": {"band_order": list(band_order), "dn_offset": dn_offset, "tta": tta, "consistency_projection": project},
        "notes": notes, "seconds": round(time.time() - t0, 1),
        "model": {"iter": meta.get("iter"), "validation": meta.get("val"), "parameters": net.n_params()},
    }
    (folder / "meta.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    _PROGRESS[job_id] = {"done": 1, "total": 1, "state": "finished"}
    return doc


def _new_id() -> str:
    return "sr_%s_%s" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"), uuid4().hex[:6])


def _parse_bands(text: str) -> List[int]:
    try:
        b = [int(x) for x in text.replace(" ", "").split(",") if x]
    except ValueError as exc:
        raise HTTPException(422, "Band order must be four numbers like 1,2,3,4 (blue, green, red, NIR).") from exc
    if len(b) != 4 or min(b) < 1 or len(set(b)) != 4:
        raise HTTPException(422, "Band order must name four different bands, for example 1,2,3,4 for a file "
                                 "stored as blue, green, red, NIR.")
    return b


async def _stream_to_disk(upload: UploadFile) -> Path:
    tmp = out_root() / ("incoming_%s.tif" % uuid4().hex)
    size = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                block = await upload.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if size > MAX_UPLOAD_MB * CHUNK:
                    raise HTTPException(413, "%s is larger than %d MB." % (upload.filename, MAX_UPLOAD_MB))
                fh.write(block)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, "%s is empty." % (upload.filename or "The file"))
    return tmp


# ---------------------------------------------------------------------- routes ----
@router.post("/api/sr/enhance")
async def enhance_upload(file: UploadFile = File(...), band_order: str = Form("1,2,3,4"),
                         dn_offset: float = Form(0.0), tta: bool = Form(False), project: bool = Form(True),
                         job_id: Optional[str] = Form(None)) -> Dict[str, Any]:
    order = _parse_bands(band_order)
    jid = job_id if job_id and job_id.startswith("sr_") and job_id.isascii() and len(job_id) < 48 else _new_id()
    tmp = await _stream_to_disk(file)
    try:
        return await run_in_threadpool(run_enhance, tmp, Path(file.filename or "upload").stem, order,
                                       dn_offset, tta, project, jid)
    except HTTPException:
        _PROGRESS.pop(jid, None)
        raise
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/api/sr/demos")
def demos() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(demo_dir().glob("*.tif")) if demo_dir().exists() else []:
        info = {}
        try:
            info = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"name": p.stem, "title": info.get("title", p.stem.replace("_", " ")),
                    "note": info.get("note", ""), "date": info.get("date"), "has_reference": bool(info.get("has_reference"))})
    return out


@router.post("/api/sr/demo/{name}")
async def enhance_demo(name: str, tta: bool = Form(False), project: bool = Form(True),
                       job_id: Optional[str] = Form(None)) -> Dict[str, Any]:
    path = demo_dir() / ("%s.tif" % Path(name).name)
    if not path.exists():
        raise HTTPException(404, "No bundled scene called %r." % name)
    jid = job_id if job_id and job_id.startswith("sr_") and job_id.isascii() and len(job_id) < 48 else _new_id()
    return await run_in_threadpool(run_enhance, path, name, [1, 2, 3, 4], 0.0, tta, project, jid)


@router.get("/api/sr/jobs/{job_id}/progress")
def progress(job_id: str) -> Dict[str, Any]:
    return _PROGRESS.get(job_id, {"state": "unknown", "done": 0, "total": 1})


@router.get("/api/sr/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    if kind not in ("sr.tif", "uncertainty.tif") or not job_id.startswith("sr_") or "/" in job_id or ".." in job_id:
        raise HTTPException(404, "Unknown file.")
    path = out_root() / job_id / kind
    if not path.exists():
        raise HTTPException(404, "That run has no %s." % kind)
    return FileResponse(path, media_type="image/tiff", filename="%s_%s" % (job_id, kind))


@router.get("/api/sr/validation")
def validation() -> Dict[str, Any]:
    return {"metrics": load_validation(), "model_present": model_path().exists()}
