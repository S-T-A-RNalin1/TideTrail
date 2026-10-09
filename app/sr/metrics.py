"""How good is a super-resolved image, and how far can it be trusted?

Four families of measurement, because no single number answers the problem
statement's question ("preserving geospatial and spectral consistency", "clearly
accounting for uncertainty and error"):

  fidelity       PSNR, SSIM, RMSE and spectral angle against the high-resolution
                 reference. How close is the output to what an aircraft saw?
  consistency    the output averaged back to 10 m against the input. A faithful
                 enhancement may add detail but must not change what the
                 satellite measured. Needs no reference, so it also runs on
                 imagery that has none.
  utility        what the problem statement wants it for: vegetation (NDVI), water
                 edges (NDWI mask) and feature visibility (edges). Measured on the
                 reference grid, so the gain over plain interpolation is explicit.
  uncertainty    whether the predicted error is a real error estimate: ranked
                 against the actual error, and checked for coverage.

Everything takes arrays shaped (bands, H, W) in blue, green, red, NIR order.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
from scipy import ndimage as ndi

from . import SCALE

B, G, R, N = 0, 1, 2, 3


def box_down(a: np.ndarray, k: int = SCALE) -> np.ndarray:
    """Average k x k blocks: the SR image as a 10 m sensor would see it."""
    c, h, w = a.shape
    h, w = h - h % k, w - w % k
    return a[:, :h, :w].reshape(c, h // k, k, w // k, k).mean(axis=(2, 4))


# ----------------------------------------------------------------- fidelity ----
def psnr(pred: np.ndarray, ref: np.ndarray, peak: float = 1.0) -> float:
    mse = float(np.mean((pred - ref) ** 2))
    return 99.0 if mse <= 1e-12 else 10.0 * math.log10(peak ** 2 / mse)


def rmse(pred: np.ndarray, ref: np.ndarray) -> float:
    return float(np.sqrt(np.mean((pred - ref) ** 2)))


def _gauss(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    ax = np.arange(size) - size // 2
    k = np.exp(-(ax ** 2) / (2 * sigma ** 2))
    return k / k.sum()


def ssim(pred: np.ndarray, ref: np.ndarray, peak: float = 1.0) -> float:
    """Mean structural similarity over bands, Gaussian 11 x 11 window."""
    k = _gauss()
    c1, c2 = (0.01 * peak) ** 2, (0.03 * peak) ** 2
    vals = []
    for b in range(pred.shape[0]):
        x, y = pred[b].astype(np.float64), ref[b].astype(np.float64)
        f = lambda a: ndi.convolve1d(ndi.convolve1d(a, k, axis=0, mode="reflect"), k, axis=1, mode="reflect")
        mx, my = f(x), f(y)
        sxx, syy, sxy = f(x * x) - mx * mx, f(y * y) - my * my, f(x * y) - mx * my
        s = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
        vals.append(float(s[5:-5, 5:-5].mean()))
    return float(np.mean(vals))


def sam(pred: np.ndarray, ref: np.ndarray) -> float:
    """Mean spectral angle in degrees: how much the colour of a pixel is rotated.
    Zero means every pixel has the right spectral shape, whatever its brightness."""
    p = pred.reshape(pred.shape[0], -1)
    r = ref.reshape(ref.shape[0], -1)
    num = (p * r).sum(axis=0)
    den = np.linalg.norm(p, axis=0) * np.linalg.norm(r, axis=0) + 1e-12
    return float(np.degrees(np.arccos(np.clip(num / den, -1.0, 1.0))).mean())


# -------------------------------------------------------------- consistency ----
def lr_consistency(sr: np.ndarray, lr: np.ndarray) -> float:
    """RMSE between the SR image averaged to the input grid and the input itself."""
    return rmse(box_down(sr), lr)


def project_consistent(sr: np.ndarray, lr: np.ndarray, iterations: int = 3) -> np.ndarray:
    """Nudge the SR image until it averages back to the input.

    The network is trained to match a reference, and a reference never agrees
    with the Sentinel-2 pixel exactly, so its output drifts a little in
    brightness. This adds back the smooth difference, a few times, which moves
    every 4 x 4 block onto the measured value and leaves the detail inside it.
    """
    out = sr.copy()
    for _ in range(iterations):
        diff = lr - box_down(out)
        out += ndi.zoom(diff, (1, SCALE, SCALE), order=3, mode="nearest", grid_mode=True)[:, :out.shape[1], :out.shape[2]]
    return out


# ------------------------------------------------------------------ utility ----
def ndvi(a: np.ndarray) -> np.ndarray:
    return (a[N] - a[R]) / (a[N] + a[R] + 1e-6)


def ndwi(a: np.ndarray) -> np.ndarray:
    return (a[G] - a[N]) / (a[G] + a[N] + 1e-6)


def ndvi_mae(pred: np.ndarray, ref: np.ndarray) -> float:
    return float(np.abs(ndvi(pred) - ndvi(ref)).mean())


def water_iou(pred: np.ndarray, ref: np.ndarray) -> Optional[float]:
    """Overlap of the open-water masks (NDWI above zero). None where the scene has no water."""
    a, b = ndwi(pred) > 0.0, ndwi(ref) > 0.0
    union = (a | b).sum()
    if (b.sum() < 200) or union == 0:
        return None
    return float((a & b).sum() / union)


def _edges(a: np.ndarray, level: float) -> np.ndarray:
    lum = a[[B, G, R]].mean(axis=0)
    gx, gy = ndi.sobel(lum, axis=1), ndi.sobel(lum, axis=0)
    return np.hypot(gx, gy) > level


def edge_f1(pred: np.ndarray, ref: np.ndarray, tolerance: int = 1) -> float:
    """F1 of strong edges: the 10% steepest brightness changes of the reference.

    A reconstructed field boundary or road edge that lies within `tolerance`
    pixels of the true one counts as found. A blurred image misses edges; an
    image with invented texture adds edges that are not there; both lower this.
    """
    lum = ref[[B, G, R]].mean(axis=0)
    level = float(np.percentile(np.hypot(ndi.sobel(lum, axis=1), ndi.sobel(lum, axis=0)), 90))
    pe, re_ = _edges(pred, level), _edges(ref, level)
    st = np.ones((2 * tolerance + 1, 2 * tolerance + 1), bool)
    prec = (pe & ndi.binary_dilation(re_, st)).sum() / max(1, pe.sum())
    rec = (re_ & ndi.binary_dilation(pe, st)).sum() / max(1, re_.sum())
    return float(0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec))


def fidelity(pred: np.ndarray, ref: np.ndarray, lr: Optional[np.ndarray] = None) -> Dict[str, Optional[float]]:
    out = {
        "psnr": psnr(pred, ref), "ssim": ssim(pred, ref), "rmse": rmse(pred, ref), "sam": sam(pred, ref),
        "ndvi_mae": ndvi_mae(pred, ref), "water_iou": water_iou(pred, ref), "edge_f1": edge_f1(pred, ref),
    }
    if lr is not None:
        out["lr_consistency"] = lr_consistency(pred, lr)
    return out


# -------------------------------------------------------------- uncertainty ----
LAPLACE_90 = math.log(1.0 / (1.0 - 0.90))      # |error| <= b * LAPLACE_90 holds 90% of the time


def spearman(a: np.ndarray, b: np.ndarray, sample: int = 200000, seed: int = 0) -> float:
    a, b = a.ravel(), b.ravel()
    if a.size > sample:
        idx = np.random.default_rng(seed).choice(a.size, sample, replace=False)
        a, b = a[idx], b[idx]
    ra, rb = a.argsort().argsort().astype(np.float64), b.argsort().argsort().astype(np.float64)
    ra -= ra.mean()
    rb -= rb.mean()
    return float((ra * rb).sum() / (np.sqrt((ra ** 2).sum() * (rb ** 2).sum()) + 1e-12))


def uncertainty_report(pred: np.ndarray, scale: np.ndarray, ref: np.ndarray) -> Dict[str, float]:
    """Is the predicted scale a usable error estimate?

    rank_corr       Spearman correlation of predicted scale and real absolute error
    coverage_90     share of pixels whose error is inside the 90% interval (ideal 0.90)
    top_vs_bottom   mean real error where the model is least sure over where it is
                    most sure, by quintile (above 1 means the map points at the errors)
    """
    err = np.abs(pred - ref)
    s = scale.ravel()
    e = err.ravel()
    lo, hi = np.percentile(s, 20), np.percentile(s, 80)
    return {
        "rank_corr": spearman(s, e),
        "coverage_90": float((err <= scale * LAPLACE_90).mean()),
        "top_vs_bottom": float(e[s >= hi].mean() / max(1e-9, e[s <= lo].mean())),
        "mean_scale": float(s.mean()), "mean_abs_error": float(e.mean()),
    }
