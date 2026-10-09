"""Paired Sentinel-2 / NAIP scenes, and the crops a network trains on.

scripts/fetch_sr_pairs.py caches each pair as one .npz:

    lr    uint16 (4, h, w)       Sentinel-2 reflectance x 10000, blue green red NIR
    hr    uint8  (4, 4h, 4w)     NAIP digital numbers on the 2.5 m grid, registered
    coef  float32 (4, 2)         per-band slope and intercept that turn the NAIP
                                 numbers into reflectance consistent with the input
    meta  json                   site, dates, shift removed, fit quality, geolocation

The aerial reference and the Sentinel-2 pixel never agree exactly: the two were
taken weeks apart, at different sun angles, by different sensors. Averaged back
to 10 m the reference sits about 0.05 reflectance from the satellite value, half
the contrast of the scene. A network trained on it would learn to imitate that
disagreement. So the reference used for training and for scoring is anchored: its
smooth difference from the measured pixels is removed, which keeps all of the
sub-10 m detail and makes the reference average back to the input exactly. What
is left to predict, and to score, is the detail inside each Sentinel-2 pixel.

Splits are by region (see the fetch script), so scoring on `test` measures how
the model does on land cover and climate it has never seen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

from .. import config
from . import SCALE
from .metrics import project_consistent

PAIR_DIR = Path(config.DATA_DIR) / "sr" / "pairs"


@dataclass
class Scene:
    name: str
    split: str
    lr: np.ndarray                  # float32 reflectance (4, h, w)
    hr_dn: np.ndarray               # uint8 (4, 4h, 4w)
    coef: np.ndarray                # (4, 2)
    meta: Dict[str, Any] = field(default_factory=dict)
    _anchored: Optional[np.ndarray] = field(default=None, repr=False, compare=False)

    def raw_reference(self) -> np.ndarray:
        """Aerial reflectance (4, H, W) after the per-band fit, with its own disagreement with the input."""
        out = self.hr_dn.astype(np.float32) * self.coef[:, 0, None, None] + self.coef[:, 1, None, None]
        return np.clip(out, 0.0, 1.5)

    def hr(self, window: Optional[Tuple[int, int, int, int]] = None, raw: bool = False) -> np.ndarray:
        """Reference reflectance (4, H, W), anchored to the input unless raw. Optional low-resolution window (r0, c0, h, w)."""
        if raw:
            full = self.raw_reference()
        else:
            if self._anchored is None:
                self._anchored = np.clip(project_consistent(self.raw_reference(), self.lr), 0.0, 1.5).astype(np.float16)
            full = self._anchored
        if window is not None:
            r0, c0, h, w = window
            full = full[:, r0 * SCALE:(r0 + h) * SCALE, c0 * SCALE:(c0 + w) * SCALE]
        return full.astype(np.float32)


def load_scene(path: Path) -> Scene:
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["meta"]))
    return Scene(Path(path).stem, meta.get("split", Path(path).parent.name),
                 z["lr"].astype(np.float32) / 10000.0, z["hr"], z["coef"].astype(np.float32), meta)


def load_scenes(split: str, root: Optional[Path] = None) -> List[Scene]:
    folder = Path(root or PAIR_DIR) / split
    return [load_scene(p) for p in sorted(folder.glob("*.npz"))]


class CropSampler:
    """Random aligned crops with flips and quarter turns, as float32 torch tensors."""

    def __init__(self, scenes: List[Scene], crop: int = 32, seed: int = 0):
        if not scenes:
            raise ValueError("no training scenes; run scripts/fetch_sr_pairs.py first")
        self.scenes, self.crop = scenes, crop
        self.rng = np.random.default_rng(seed)

    def _one(self) -> Tuple[np.ndarray, np.ndarray]:
        s = self.scenes[int(self.rng.integers(len(self.scenes)))]
        _, h, w = s.lr.shape
        r0 = int(self.rng.integers(0, h - self.crop + 1))
        c0 = int(self.rng.integers(0, w - self.crop + 1))
        lr = s.lr[:, r0:r0 + self.crop, c0:c0 + self.crop]
        hr = s.hr((r0, c0, self.crop, self.crop))
        k = int(self.rng.integers(4))
        flip = bool(self.rng.integers(2))
        lr, hr = np.rot90(lr, k, (1, 2)), np.rot90(hr, k, (1, 2))
        if flip:
            lr, hr = lr[:, :, ::-1], hr[:, :, ::-1]
        return np.ascontiguousarray(lr), np.ascontiguousarray(hr)

    def batch(self, n: int) -> Tuple[torch.Tensor, torch.Tensor]:
        pairs = [self._one() for _ in range(n)]
        return (torch.from_numpy(np.stack([p[0] for p in pairs])),
                torch.from_numpy(np.stack([p[1] for p in pairs])))
