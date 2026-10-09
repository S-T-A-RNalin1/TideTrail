"""Score the super-resolution model on held-out regions, against plain interpolation.

    python scripts/evaluate_sr.py                    # test split
    python scripts/evaluate_sr.py --split val --tta

Every number the console, the README and the deck quote about accuracy comes
from the file this writes, data/validation/sr_metrics.json. The reference is
NAIP aerial imagery at 2.5 m, which the network never saw: the test sites are in
states that contribute no training data. Bicubic interpolation is the baseline,
because it is what a GIS does when asked to enlarge an image, and the question
is whether the model buys anything over it.

Also writes comparison figures to docs/screenshots/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont                      # noqa: E402

from app import config                                           # noqa: E402
from app.sr import SCALE, infer, metrics                         # noqa: E402
from app.sr import model as sr_model                             # noqa: E402
from app.sr.data import load_scenes                              # noqa: E402

OUT = Path(config.DATA_DIR) / "validation" / "sr_metrics.json"
FIG = ROOT / "docs" / "screenshots"
KEYS = ("psnr", "ssim", "rmse", "sam", "ndvi_mae", "water_iou", "edge_f1", "lr_consistency")


def mean(rows: List[Dict[str, Any]], key: str):
    v = [r[key] for r in rows if r.get(key) is not None]
    return float(np.mean(v)) if v else None


def font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def panel_figure(items: List[Dict[str, Any]], path: Path, crop: int = 40) -> None:
    """Rows of: input at 10 m, bicubic, super-resolved, reference, uncertainty, absolute error."""
    cols = ["Sentinel-2, 10 m", "Bicubic", "Super-resolved", "Aerial reference", "Uncertainty", "Error"]
    cell = crop * SCALE
    pad, head = 8, 34
    W = len(cols) * cell + (len(cols) + 1) * pad
    H = head + len(items) * (cell + 22 + pad) + pad
    img = Image.new("RGB", (W, H), (251, 251, 248))
    d = ImageDraw.Draw(img)
    f, fs = font(17), font(14)
    for i, c in enumerate(cols):
        d.text((pad + i * (cell + pad) + 4, 8), c, fill=(27, 30, 27), font=f)
    for r, it in enumerate(items):
        top = head + r * (cell + 22 + pad)
        lo = hi = None
        tiles = []
        r0, c0 = it["r0"], it["c0"]
        lr = it["lr"][:, r0:r0 + crop, c0:c0 + crop]
        sl = (slice(None), slice(r0 * SCALE, (r0 + crop) * SCALE), slice(c0 * SCALE, (c0 + crop) * SCALE))
        bic, sr, ref, sc = it["bicubic"][sl], it["sr"][sl], it["ref"][sl], it["scale"][sl]
        base, lo, hi = infer.stretch_rgb(ref)
        up = np.repeat(np.repeat(lr, SCALE, axis=1), SCALE, axis=2)
        err = np.abs(sr - ref).mean(axis=0)
        e_img = metrics_gray(err, it["err_top"])
        for arr in (up, bic, sr, ref):
            tiles.append(infer.stretch_rgb(arr, lo, hi)[0])
        tiles.append(infer.uncertainty_rgb(sc, it["unc_top"]))
        tiles.append(e_img)
        for i, t in enumerate(tiles):
            img.paste(Image.fromarray(t), (pad + i * (cell + pad), top))
        d.text((pad + 4, top + cell + 3), "%s  |  PSNR bicubic %.1f, model %.1f dB" % (
            it["label"], it["psnr_bicubic"], it["psnr_sr"]), fill=(74, 80, 75), font=fs)
    img.save(path)


def metrics_gray(err: np.ndarray, top: float) -> np.ndarray:
    t = np.clip(err / (top + 1e-9), 0, 1)[..., None]
    pale, deep = np.array([246, 244, 236], np.float32), np.array([143, 91, 0], np.float32)   # ochre
    return (pale * (1 - t) + deep * t).astype(np.uint8)


def reliability_figure(unc: np.ndarray, err: np.ndarray, path: Path, bins: int = 10) -> Dict[str, Any]:
    """Mean real error in each decile of predicted uncertainty."""
    order = np.argsort(unc)
    chunks = np.array_split(order, bins)
    xs = [float(unc[c].mean()) for c in chunks]
    ys = [float(err[c].mean()) for c in chunks]
    W, H, m = 760, 420, 56
    img = Image.new("RGB", (W, H), (251, 251, 248))
    d = ImageDraw.Draw(img)
    f, fs = font(16), font(13)
    top = max(max(xs), max(ys)) * 1.08
    d.rectangle([m, 30, W - 24, H - m], outline=(205, 209, 200))
    pts = []
    for x, y in zip(xs, ys):
        px = m + (W - 24 - m) * x / top
        py = H - m - (H - m - 30) * y / top
        pts.append((px, py))
    d.line(pts, fill=(45, 106, 79), width=3)
    for p in pts:
        d.ellipse([p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5], fill=(45, 106, 79))
    d.line([m, H - m, W - 24, 30], fill=(176, 20, 109), width=2)
    d.text((m, 6), "Real error against predicted uncertainty, by decile of pixels", fill=(27, 30, 27), font=f)
    d.text((W - 270, H - 38), "predicted uncertainty (reflectance)", fill=(74, 80, 75), font=fs)
    d.text((6, 36), "mean |error|", fill=(74, 80, 75), font=fs)
    d.text((W - 250, 40), "magenta: a perfect estimate", fill=(176, 20, 109), font=fs)
    d.text((W - 250, 58), "green: this model", fill=(45, 106, 79), font=fs)
    img.save(path)
    return {"deciles_predicted": xs, "deciles_actual": ys}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="test", choices=("test", "val", "train"))
    ap.add_argument("--tta", action="store_true", help="average eight flipped and rotated passes")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N scenes (quick runs)")
    args = ap.parse_args()

    torch.set_num_threads(14)
    path = Path(config.MODELS_DIR) / "sr_x4.pt"
    net, meta = sr_model.load(path)
    scenes = load_scenes(args.split)
    if args.limit:
        scenes = scenes[:args.limit]
    if not scenes:
        print("no %s scenes; run scripts/fetch_sr_pairs.py" % args.split)
        return 1
    print("%d %s scenes, model after %s iterations (%.2fM parameters)" % (
        len(scenes), args.split, meta.get("iter"), net.n_params() / 1e6), flush=True)

    rows: List[Dict[str, Any]] = []
    cal_u, cal_e = [], []
    keep: List[Dict[str, Any]] = []
    t0 = time.time()
    for sc in scenes:
        lr, ref = sc.lr, sc.hr()
        bic = sr_model.bicubic(torch.from_numpy(lr)[None])[0].numpy()
        out = infer.enhance(net, lr, tta=args.tta, project=False)
        raw, scale = out["sr"], out["scale"]
        proj = np.clip(metrics.project_consistent(raw, lr), 0.0, 1.5)
        row = {"site": sc.name, "cover": sc.meta.get("cover"), "naip_date": sc.meta.get("naip_date"),
               "s2_date": sc.meta.get("s2_date"), "lat": sc.meta.get("lat"), "lon": sc.meta.get("lon")}
        ref_raw = sc.hr(raw=True)
        for tag, arr in (("bicubic", bic), ("model", raw), ("model_projected", proj)):
            for k, v in metrics.fidelity(arr, ref, lr).items():
                row["%s_%s" % (tag, k)] = v
            row["%s_psnr_unanchored" % tag] = metrics.psnr(arr, ref_raw)
        # the product people receive is the projected one, so that is what the uncertainty is checked against
        row["uncertainty"] = metrics.uncertainty_report(proj, scale, ref)
        rows.append(row)
        sub = np.random.default_rng(0).choice(scale.size, 60000, replace=False)
        cal_u.append(scale.ravel()[sub])
        cal_e.append(np.abs(proj - ref).ravel()[sub])
        # the crop shown in the figure: the one with the most detail in the reference
        lum = ref[[0, 1, 2]].mean(axis=0)
        best, br, bc = -1.0, 0, 0
        for r0 in range(0, 320 - 40, 20):
            for c0 in range(0, 320 - 40, 20):
                v = float(lum[r0 * SCALE:(r0 + 40) * SCALE, c0 * SCALE:(c0 + 40) * SCALE].std())
                if v > best:
                    best, br, bc = v, r0, c0
        keep.append({"label": "%s (%s)" % (sc.name, sc.meta.get("cover")), "lr": lr, "bicubic": bic, "sr": proj, "ref": ref,
                     "scale": scale, "r0": br, "c0": bc, "psnr_bicubic": row["bicubic_psnr"], "psnr_sr": row["model_projected_psnr"],
                     "err_top": float(np.percentile(np.abs(proj - ref).mean(axis=0), 98)), "unc_top": float(np.percentile(scale.mean(axis=0), 98))})
        print("  %-18s bicubic %.2f dB -> model %.2f dB  (SSIM %.3f -> %.3f)" % (
            sc.name, row["bicubic_psnr"], row["model_psnr"], row["bicubic_ssim"], row["model_ssim"]), flush=True)

    def summary(tag: str, subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {k: mean(subset, "%s_%s" % (tag, k)) for k in KEYS}

    summ = {t: summary(t, rows) for t in ("bicubic", "model", "model_projected")}
    for t in summ:
        summ[t]["psnr_unanchored"] = mean(rows, "%s_psnr_unanchored" % t)
    gains = {k: (None if summ["bicubic"][k] is None else summ["model_projected"][k] - summ["bicubic"][k]) for k in KEYS}
    win = {k: int(sum(1 for r in rows if r["model_projected_%s" % k] is not None and r["bicubic_%s" % k] is not None and
                      (r["model_projected_%s" % k] > r["bicubic_%s" % k] if k in ("psnr", "ssim", "water_iou", "edge_f1")
                       else r["model_projected_%s" % k] < r["bicubic_%s" % k]))) for k in KEYS if k != "lr_consistency"}
    psnr_gain = np.array([r["model_projected_psnr"] - r["bicubic_psnr"] for r in rows])
    boot = np.random.default_rng(1).choice(psnr_gain, (4000, len(psnr_gain))).mean(axis=1)
    covers: Dict[str, Any] = {}
    for cv in sorted({r["cover"] for r in rows if r["cover"]}):
        sub = [r for r in rows if r["cover"] == cv]
        covers[cv] = {"scenes": len(sub), "psnr_bicubic": mean(sub, "bicubic_psnr"), "psnr_model": mean(sub, "model_projected_psnr"),
                      "ssim_bicubic": mean(sub, "bicubic_ssim"), "ssim_model": mean(sub, "model_projected_ssim")}
    u_all, e_all = np.concatenate(cal_u), np.concatenate(cal_e)
    unc = {k: float(np.mean([r["uncertainty"][k] for r in rows])) for k in rows[0]["uncertainty"]}
    unc["pooled_rank_corr"] = metrics.spearman(u_all, e_all)

    result = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split": args.split, "tta": args.tta,
        "scenes": len(rows), "regions": sorted({r["site"].split("_")[0].upper() for r in rows}),
        "reference": "NAIP aerial imagery resampled to 2.5 m, registered, radiometrically matched and anchored to the "
                     "Sentinel-2 pixels (its smooth 10 m difference from the input removed, its detail kept)",
        "model": {"checkpoint": path.name, "iterations": meta.get("iter"), "parameters": net.n_params(),
                  "trained_on_scenes": meta.get("trained_on")},
        "baseline": "bicubic interpolation", "output_resolution_m": 10.0 / SCALE,
        "summary": summ, "gain_over_bicubic": gains, "scenes_improved": win,
        "psnr_gain_ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
        "by_cover": covers, "uncertainty": unc, "per_scene": rows, "seconds": round(time.time() - t0, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")

    if not args.no_figures:
        FIG.mkdir(parents=True, exist_ok=True)
        order = sorted(range(len(rows)), key=lambda i: -(rows[i]["model_projected_psnr"] - rows[i]["bicubic_psnr"]))
        pick = [order[0], order[len(order) // 3], order[len(order) // 2], order[-1]] if len(order) >= 4 else order
        panel_figure([keep[i] for i in pick], FIG / "sr_compare.png")
        rel = reliability_figure(u_all, e_all, FIG / "sr_reliability.png")
        result["reliability"] = rel
        Path(args.out).write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")

    s, b = summ["model_projected"], summ["bicubic"]
    print("\n%s: %d scenes in %s" % (args.split, len(rows), ", ".join(result["regions"])))
    for k in KEYS:
        if b[k] is not None:
            print("  %-15s bicubic %.4f   model %.4f" % (k, b[k], s[k]))
    print("  PSNR against the aerial image as is: bicubic %.2f  model %.2f" % (b["psnr_unanchored"], s["psnr_unanchored"]))
    print("  PSNR gain %.2f dB, 95%% CI %.2f to %.2f; improved on %d of %d scenes" % (
        gains["psnr"], *result["psnr_gain_ci95"], win["psnr"], len(rows)))
    print("  uncertainty: rank corr %.2f, 90%% coverage %.2f" % (unc["rank_corr"], unc["coverage_90"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
