"""The super-resolution network.

A residual convolutional network that works on the 10 m grid and upsamples at
the end with sub-pixel convolution, so almost all of the computation is done on
a quarter-million fewer pixels than a network that upsampled first. It predicts
two things for every 2.5 m pixel and band:

    mean      the reflectance, as a bicubic upsample of the input plus a learned
              correction. The global skip means the network only has to supply
              detail, never to rebuild the scene, which is what keeps the output
              faithful to what the satellite actually measured.
    log scale the spread of the Laplace distribution the true value is expected
              to follow. It is the model's own estimate of how wrong the mean is
              likely to be, and becomes the uncertainty map.

The first design choice is plain regression, not a GAN. A regression network
returns the value most likely to be true; an adversarial one returns a value
that looks plausible, which is how roads that are not there appear. This
project values the first, and measures the second as a risk.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from . import SCALE

# Surface reflectance of land is mostly 0.02 to 0.4; this puts it near 0 to 1.
INPUT_SCALE = 3.0


@dataclass
class SRConfig:
    bands: int = 4
    channels: int = 48
    blocks: int = 12
    scale: int = SCALE
    res_scale: float = 0.1


class ResBlock(nn.Module):
    def __init__(self, c: int, res_scale: float):
        super().__init__()
        self.c1 = nn.Conv2d(c, c, 3, padding=1)
        self.c2 = nn.Conv2d(c, c, 3, padding=1)
        self.k = res_scale

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.k * self.c2(F.relu(self.c1(x)))


class SRNet(nn.Module):
    def __init__(self, cfg: Optional[SRConfig] = None):
        super().__init__()
        self.cfg = cfg or SRConfig()
        c, b = self.cfg.channels, self.cfg.bands
        assert self.cfg.scale == 4, "the network upsamples in two 2x stages"
        self.head = nn.Conv2d(b, c, 3, padding=1)
        self.body = nn.Sequential(*[ResBlock(c, self.cfg.res_scale) for _ in range(self.cfg.blocks)],
                                  nn.Conv2d(c, c, 3, padding=1))
        self.up1 = nn.Conv2d(c, 4 * c, 3, padding=1)
        self.up2 = nn.Conv2d(c, 4 * c, 3, padding=1)
        self.to_mean = nn.Conv2d(c, b, 3, padding=1)
        self.to_logb = nn.Conv2d(c, b, 3, padding=1)
        nn.init.zeros_(self.to_mean.weight)
        nn.init.zeros_(self.to_mean.bias)            # start as exactly the bicubic upsample
        nn.init.zeros_(self.to_logb.weight)
        nn.init.constant_(self.to_logb.bias, -3.0)   # start expecting small errors

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """x: reflectance (N, bands, h, w). Returns mean and log scale, both (N, bands, 4h, 4w),
        in the same reflectance units as x."""
        z = x * INPUT_SCALE
        base = F.interpolate(z, scale_factor=self.cfg.scale, mode="bicubic", align_corners=False)
        f = self.head(z)
        f = f + self.body(f)
        f = F.relu(F.pixel_shuffle(self.up1(f), 2))
        f = F.relu(F.pixel_shuffle(self.up2(f), 2))
        mean = base + self.to_mean(f)
        # the head works in input-scaled units; report the scale in reflectance units
        return mean / INPUT_SCALE, self.to_logb(f) - math.log(INPUT_SCALE)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def bicubic(x: torch.Tensor, scale: int = SCALE) -> torch.Tensor:
    """The baseline every result is compared with."""
    return F.interpolate(x, scale_factor=scale, mode="bicubic", align_corners=False)


def save(path: Path, model: SRNet, meta: Optional[Dict[str, Any]] = None) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "config": asdict(model.cfg), "meta": meta or {}}, str(path))


def load(path: Path, map_location: str = "cpu") -> Tuple[SRNet, Dict[str, Any]]:
    blob = torch.load(str(path), map_location=map_location, weights_only=False)
    model = SRNet(SRConfig(**blob["config"]))
    model.load_state_dict(blob["state_dict"])
    model.eval()
    return model, blob.get("meta", {})
