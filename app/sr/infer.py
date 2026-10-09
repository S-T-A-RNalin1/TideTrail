"""Run the network over a whole scene and write georeferenced results.

Sentinel-2 scenes are far larger than anything that fits through a network in
one piece, so the image is cut into overlapping tiles. Each tile carries more
context than it keeps: the outer ring of every output tile is thrown away, so
a seam between tiles is made of pixels that each saw both sides of it.

The product is three georeferenced layers on the 2.5 m grid, which sits exactly
on the input's 10 m grid (four output pixels per input pixel in each direction):

    sr            reflectance, blue green red NIR
    uncertainty   the model's own error estimate per pixel, in reflectance units
    consistency   how far the result, averaged back to 10 m, is from the input
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import torch

from . import SCALE
from . import metrics
from .model import SRNet


MAX_INPUT_PIXELS = 4_000_000     # the output holds 16x as many values, in 4 bands


class InputError(ValueError):
    """An input the model cannot be trusted with, with the reason in plain words."""


# ------------------------------------------------------------------ tiling ----
@torch.no_grad()
def _predict(model: SRNet, x: np.ndarray, tta: bool) -> Tuple[np.ndarray, np.ndarray]:
    t = torch.from_numpy(np.ascontiguousarray(x))[None]
    views = [(False, 0)] if not tta else [(f, k) for f in (False, True) for k in (0, 1, 2, 3)]
    mus, lbs = [], []
    for flip, k in views:
        v = t.flip(-1) if flip else t
        v = torch.rot90(v, k, (2, 3))
        mu, lb = model(v)
        mu, lb = torch.rot90(mu, -k, (2, 3)), torch.rot90(lb, -k, (2, 3))
        if flip:
            mu, lb = mu.flip(-1), lb.flip(-1)
        mus.append(mu)
        lbs.append(lb)
    mean = torch.stack(mus).mean(0)[0]
    # test-time views also disagree with each other; that spread adds to the stated error
    scale = torch.stack([l.exp() for l in lbs]).mean(0)[0]
    if tta:
        scale = scale + torch.stack(mus).std(0)[0]
    return mean.numpy(), scale.numpy()


def enhance(model: SRNet, lr: np.ndarray, tile: int = 96, overlap: int = 24, tta: bool = False,
            project: bool = True, progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, np.ndarray]:
    """Super-resolve a (4, h, w) reflectance array. Returns sr and scale at (4, 4h, 4w).

    progress(done, total) is called after each tile, for a progress bar."""
    model.eval()
    c, h, w = lr.shape
    step = tile - 2 * overlap
    ph, pw = (-h) % step, (-w) % step
    padded = np.pad(lr, ((0, 0), (overlap, overlap + ph), (overlap, overlap + pw)), mode="reflect")
    H, W = h + ph, w + pw
    sr = np.zeros((c, H * SCALE, W * SCALE), np.float32)
    sc = np.zeros_like(sr)
    total, done = (H // step) * (W // step), 0
    for r in range(0, H, step):
        for q in range(0, W, step):
            done += 1
            if progress:
                progress(done, total)
            tl = padded[:, r:r + tile, q:q + tile]
            mean, scale = _predict(model, tl, tta)
            o = overlap * SCALE
            keep = (slice(None), slice(o, o + step * SCALE), slice(o, o + step * SCALE))
            sr[:, r * SCALE:(r + step) * SCALE, q * SCALE:(q + step) * SCALE] = mean[keep]
            sc[:, r * SCALE:(r + step) * SCALE, q * SCALE:(q + step) * SCALE] = scale[keep]
    sr, sc = sr[:, :h * SCALE, :w * SCALE], sc[:, :h * SCALE, :w * SCALE]
    sr = np.clip(sr, 0.0, 1.5)
    if project:
        sr = np.clip(metrics.project_consistent(sr, lr), 0.0, 1.5)
    return {"sr": sr, "scale": sc}


# ----------------------------------------------------------------- GeoTIFF ----
@dataclass
class Scene:
    lr: np.ndarray              # float32 reflectance (4, h, w)
    transform: Any              # affine of the input grid
    crs: Any
    notes: list


def read_scene(path: Path, band_order: Tuple[int, int, int, int] = (1, 2, 3, 4),
               dn_offset: float = 0.0, scale_hint: Optional[float] = None) -> Scene:
    """Read a Sentinel-2 GeoTIFF as (blue, green, red, NIR) reflectance.

    band_order gives the 1-based band numbers of blue, green, red and NIR in the
    file. Values above 2 are taken as digital numbers (reflectance x 10000, less
    dn_offset), anything else as reflectance already.
    """
    import rasterio
    try:
        ds = rasterio.open(path)
    except Exception as exc:
        raise InputError("Could not read the file as a GeoTIFF: %s" % exc) from exc
    with ds:
        if ds.crs is None or ds.transform.is_identity:
            raise InputError("The file has no georeference (CRS and transform). Export it from GIS software as a "
                             "georeferenced GeoTIFF so the result can be placed on the ground.")
        if ds.count < max(band_order):
            raise InputError("The file has %d band%s but band %d was asked for. The model needs blue, green, red "
                             "and near infrared (Sentinel-2 B2, B3, B4, B8)." % (ds.count, "" if ds.count == 1 else "s", max(band_order)))
        px = abs(ds.res[0]) if not ds.crs.is_geographic else abs(ds.res[0]) * 111320.0
        if not 8.5 <= px <= 11.5:
            raise InputError("The pixels are %.1f m. The model was trained and checked on 10 m Sentinel-2, and "
                             "enhancing other resolutions has not been validated; resample to 10 m first or use "
                             "Sentinel-2 B2, B3, B4 and B8." % px)
        if ds.height < 24 or ds.width < 24:
            raise InputError("The image is only %d x %d pixels; at least 24 x 24 is needed." % (ds.width, ds.height))
        if ds.height * ds.width > MAX_INPUT_PIXELS:
            raise InputError("The image is %d x %d pixels. The result is sixteen times larger, so crop it to the "
                             "area of interest, at most about 2000 x 2000 pixels (20 km), so it fits in memory and "
                             "finishes in minutes." % (ds.width, ds.height))
        arr = np.stack([ds.read(b).astype(np.float32) for b in band_order])
        nodata = ds.nodata
        transform, crs = ds.transform, ds.crs
    notes = []
    valid = np.isfinite(arr).all(axis=0)
    if nodata is not None:
        valid &= (arr != nodata).all(axis=0)
    if valid.mean() < 0.5:
        raise InputError("More than half of the image has no data.")
    top = float(np.nanpercentile(arr[:, valid], 99.5))
    if top > 2.0:
        arr = (arr - dn_offset) / 10000.0
        notes.append("Values read as digital numbers (reflectance x 10000, offset %g)." % dn_offset)
    else:
        notes.append("Values read as reflectance.")
    arr = np.where(valid[None], arr, 0.0)
    return Scene(np.clip(arr, 0.0, 1.5), transform, crs, notes)


def write_geotiff(path: Path, arr: np.ndarray, transform, crs, scale_factor: int = SCALE,
                  as_uint16: bool = True, descriptions: Optional[list] = None) -> None:
    """Write bands on the grid refined by scale_factor, keeping the top-left corner fixed."""
    import rasterio
    from affine import Affine
    t = Affine(transform.a / scale_factor, transform.b, transform.c, transform.d, transform.e / scale_factor, transform.f)
    data = np.clip(np.rint(arr * 10000.0), 0, 65535).astype(np.uint16) if as_uint16 else arr.astype(np.float32)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                       dtype=data.dtype, crs=crs, transform=t, compress="deflate", tiled=True,
                       blockxsize=256, blockysize=256) as dst:
        dst.write(data)
        if descriptions:
            for i, d in enumerate(descriptions, 1):
                dst.set_band_description(i, d)
        dst.update_tags(scale_factor=str(1 / 10000.0) if as_uint16 else "1", product="TideTrail super-resolution")


# --------------------------------------------------------------- quicklooks ----
def stretch_rgb(arr: np.ndarray, lo: Optional[np.ndarray] = None, hi: Optional[np.ndarray] = None):
    """True colour 8-bit image from (blue, green, red, ...) with a shared stretch."""
    rgb = arr[[2, 1, 0]]
    if lo is None:
        lo = np.percentile(rgb, 2, axis=(1, 2))
        hi = np.percentile(rgb, 98, axis=(1, 2))
    out = np.clip((rgb - lo[:, None, None]) / (hi - lo + 1e-6)[:, None, None], 0, 1)
    return (out.transpose(1, 2, 0) * 255).astype(np.uint8), lo, hi


def uncertainty_rgb(scale: np.ndarray, top: Optional[float] = None) -> np.ndarray:
    """Pale where the model is sure, deep magenta where it is not. No blues."""
    u = scale.mean(axis=0)
    top = float(np.percentile(u, 98)) if top is None else top
    t = np.clip(u / (top + 1e-9), 0, 1)[..., None]
    pale, deep = np.array([246, 244, 236], np.float32), np.array([176, 20, 109], np.float32)
    return (pale * (1 - t) + deep * t).astype(np.uint8)


def ndvi_rgb(arr: np.ndarray) -> np.ndarray:
    """Vegetation index as colour: sand where there is none, deep green where it is dense."""
    v = np.clip(metrics.ndvi(arr), -0.1, 0.9)
    t = ((v + 0.1) / 1.0)[..., None].astype(np.float32)
    mid = np.clip(t * 2.0, 0, 1)
    sand, leaf, deep = (np.array(c, np.float32) for c in ((231, 222, 190), (125, 170, 90), (26, 82, 56)))
    low = sand * (1 - mid) + leaf * mid
    hi = np.clip(t * 2.0 - 1.0, 0, 1)
    return (low * (1 - hi) + deep * hi).astype(np.uint8)


def water_rgb(arr: np.ndarray) -> np.ndarray:
    """Open water (NDWI above zero) in dark ink on pale paper, so its edge reads as a line."""
    wet = (metrics.ndwi(arr) > 0.0)[..., None]
    paper, ink = np.array([246, 244, 236], np.uint8), np.array([40, 52, 44], np.uint8)
    return np.where(wet, ink, paper).astype(np.uint8)


def save_png(path: Path, img: np.ndarray) -> None:
    from PIL import Image
    Image.fromarray(img).save(path)
