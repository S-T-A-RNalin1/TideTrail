"""Train the super-resolution network on cached Sentinel-2 / NAIP pairs.

    python -m app.sr.train --iters 6000 --budget-min 8        # run, then run again to continue
    python -m app.sr.train --iters 6000 --budget-min 8 --fresh

Training stops when the time budget is spent, writes its full state, and the
next run picks up from there, so a long job can be done in short sittings on a
laptop. The learning-rate schedule is a function of the iteration number, not of
elapsed time, so stopping and resuming does not change what is learned.

Loss, in input-scaled reflectance units:

    L1 on the mean              fidelity to the reference
    L1 on image gradients       keeps edges from being averaged away (weight 0.5)
    Laplace NLL on the scale    teaches the uncertainty head the size of the
                                error; the mean is detached there, so asking
                                for honest uncertainty never bends the image
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import torch

from .. import config
from . import SCALE, metrics
from .data import CropSampler, Scene, load_scenes
from .infer import enhance
from .model import INPUT_SCALE, SRConfig, SRNet, bicubic, save

MODEL_PATH = Path(config.MODELS_DIR) / "sr_x4.pt"
STATE_PATH = Path(config.MODELS_DIR) / "sr_x4.state.pt"
HISTORY = Path(config.DATA_DIR) / "sr" / "train_history.jsonl"


def grad_l1(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    return ((a[..., :, 1:] - a[..., :, :-1]) - (b[..., :, 1:] - b[..., :, :-1])).abs().mean() + \
           ((a[..., 1:, :] - a[..., :-1, :]) - (b[..., 1:, :] - b[..., :-1, :])).abs().mean()


def loss_fn(mu: torch.Tensor, logb: torch.Tensor, hr: torch.Tensor) -> Dict[str, torch.Tensor]:
    k = INPUT_SCALE
    l1 = (mu - hr).abs().mean() * k
    gr = grad_l1(mu, hr) * k
    err = (hr - mu.detach()).abs() * k
    b = torch.exp(logb + math.log(k))                 # scale in input-scaled units
    nll = (err / b + torch.log(b)).mean()
    return {"total": l1 + 0.5 * gr + 0.1 * nll, "l1": l1, "grad": gr, "nll": nll}


def evaluate(model: SRNet, scenes: List[Scene], crop: int = 128) -> Dict[str, float]:
    """PSNR of the network and of bicubic on the centre of each scene, no projection."""
    model.eval()
    ps, pb, ss, sb = [], [], [], []
    for sc in scenes:
        _, h, w = sc.lr.shape
        r0, c0 = (h - crop) // 2, (w - crop) // 2
        lr = sc.lr[:, r0:r0 + crop, c0:c0 + crop]
        ref = sc.hr((r0, c0, crop, crop))
        out = enhance(model, lr, project=False)["sr"]
        base = bicubic(torch.from_numpy(lr)[None])[0].numpy()
        ps.append(metrics.psnr(out, ref)); pb.append(metrics.psnr(base, ref))
        ss.append(metrics.ssim(out, ref)); sb.append(metrics.ssim(base, ref))
    model.train()
    return {"psnr": float(np.mean(ps)), "psnr_bicubic": float(np.mean(pb)),
            "ssim": float(np.mean(ss)), "ssim_bicubic": float(np.mean(sb))}


def save_state(model: SRNet, opt: torch.optim.Optimizer, it: int, best: float) -> None:
    """Everything needed to carry on: weights, optimiser, iteration, best score."""
    tmp = STATE_PATH.with_suffix(".tmp")
    torch.save({"model": model.state_dict(), "config": model.cfg.__dict__, "opt": opt.state_dict(),
                "iter": it, "best": best}, str(tmp))
    tmp.replace(STATE_PATH)


def lr_at(it: int, total: int, peak: float, floor: float = 1e-5, warm: int = 100) -> float:
    if it < warm:
        return peak * (it + 1) / warm
    t = (it - warm) / max(1, total - warm)
    return floor + 0.5 * (peak - floor) * (1 + math.cos(math.pi * min(1.0, t)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iters", type=int, default=6000, help="total iterations the schedule is built for")
    ap.add_argument("--budget-min", type=float, default=8.0, help="stop after this many minutes")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--crop", type=int, default=32, help="low-resolution crop side in pixels")
    ap.add_argument("--lr", type=float, default=4e-4)
    ap.add_argument("--channels", type=int, default=48)
    ap.add_argument("--blocks", type=int, default=12)
    ap.add_argument("--eval-every", type=int, default=400)
    ap.add_argument("--threads", type=int, default=14)
    ap.add_argument("--fresh", action="store_true", help="ignore a saved state and start again")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    train, val = load_scenes("train"), load_scenes("val")
    val = val or train[:2]
    print("scenes: %d train, %d val" % (len(train), len(val)), flush=True)
    sampler = CropSampler(train, args.crop, seed=args.seed)

    model = SRNet(SRConfig(channels=args.channels, blocks=args.blocks))
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    it, best, hist = 0, -1.0, []
    if STATE_PATH.exists() and not args.fresh:
        st = torch.load(str(STATE_PATH), map_location="cpu", weights_only=False)
        model = SRNet(SRConfig(**st["config"]))
        model.load_state_dict(st["model"])
        opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
        opt.load_state_dict(st["opt"])
        it, best = st["iter"], st["best"]
        sampler.rng = np.random.default_rng(args.seed + it)
        print("resuming at iteration %d, best val PSNR %.3f" % (it, best), flush=True)
    model.train()
    print("parameters: %.2fM" % (model.n_params() / 1e6), flush=True)

    start = it
    t0, run = time.time(), {"l1": 0.0, "grad": 0.0, "nll": 0.0, "n": 0}
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    while it < args.iters and (time.time() - t0) < args.budget_min * 60:
        for g in opt.param_groups:
            g["lr"] = lr_at(it, args.iters, args.lr)
        lr, hr = sampler.batch(args.batch)
        mu, logb = model(lr)
        L = loss_fn(mu, logb, hr)
        opt.zero_grad()
        L["total"].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        it += 1
        for k in ("l1", "grad", "nll"):
            run[k] += float(L[k].detach())
        run["n"] += 1
        if it % args.eval_every == 0 or it == args.iters:
            ev = evaluate(model, val)
            row = {"iter": it, "l1": run["l1"] / run["n"], "grad": run["grad"] / run["n"], "nll": run["nll"] / run["n"],
                   "lr": opt.param_groups[0]["lr"], **ev, "minutes": round((time.time() - t0) / 60, 1)}
            run = {"l1": 0.0, "grad": 0.0, "nll": 0.0, "n": 0}
            with HISTORY.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            flag = ""
            if ev["psnr"] > best:
                best = ev["psnr"]
                save(MODEL_PATH, model, {"iter": it, "val": ev, "trained_on": len(train)})
                flag = "  saved"
            print("it %5d  L1 %.4f  val PSNR %.2f (bicubic %.2f)  SSIM %.3f (bicubic %.3f)%s" % (
                it, row["l1"], ev["psnr"], ev["psnr_bicubic"], ev["ssim"], ev["ssim_bicubic"], flag), flush=True)
        elif it % 50 == 0:
            print("it %5d  L1 %.4f  %.2fs/it" % (it, float(L["l1"].detach()), (time.time() - t0) / max(1, it - start)), flush=True)
        if it % 200 == 0:
            save_state(model, opt, it, best)
        if it % 500 == 0:
            # pairs are still arriving from the download script: pick them up
            now = load_scenes("train")
            if len(now) != len(train):
                print("training scenes: %d -> %d" % (len(train), len(now)), flush=True)
                train, sampler.scenes = now, now
                val = load_scenes("val") or train[:2]
    save_state(model, opt, it, best)
    print("stopped at iteration %d of %d after %.1f min" % (it, args.iters, (time.time() - t0) / 60), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
