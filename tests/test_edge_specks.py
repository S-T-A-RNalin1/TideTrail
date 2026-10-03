"""Small oil patches at the chip border are dropped; slicks crossing it are kept."""
from __future__ import annotations

import numpy as np

from app import pipeline

PIXEL_KM2 = 1e-4  # 10 m pixels


def test_small_patch_on_the_border_is_dropped_and_counted():
    mask = np.zeros((400, 400), dtype=np.uint8)
    mask[100:120, 0:12] = 2                   # 0.024 km2 touching the left edge
    mask[200:230, 200:230] = 2                # 0.09 km2 in the middle of the chip
    out, dropped = pipeline._drop_edge_specks(mask, PIXEL_KM2)
    assert dropped == 1
    assert not (out[100:120, 0:12] == 2).any()
    assert (out[200:230, 200:230] == 2).all()


def test_large_slick_crossing_the_border_is_kept():
    mask = np.zeros((400, 400), dtype=np.uint8)
    mask[50:150, 0:60] = 2                    # 0.6 km2 running off the edge
    out, dropped = pipeline._drop_edge_specks(mask, PIXEL_KM2)
    assert dropped == 0
    assert (out == mask).all()


def test_look_alikes_are_untouched():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[0:5, 0:5] = 1
    out, dropped = pipeline._drop_edge_specks(mask, PIXEL_KM2)
    assert dropped == 0 and (out == mask).all()
