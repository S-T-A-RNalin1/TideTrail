"""Deep learning super-resolution mapping for Sentinel-2 imagery.

Turns 10 m Sentinel-2 (blue, green, red, near infrared) into 2.5 m imagery
with a per-pixel uncertainty, and measures how far to trust it:

    model.py    the network and its checkpoint format
    data.py     paired Sentinel-2 / NAIP scenes and the training crops cut from them
    metrics.py  fidelity, spectral consistency, feature utility and uncertainty calibration
    infer.py    tiled inference over a GeoTIFF, consistency projection, GeoTIFF output
    train.py    resumable training that fits a time budget
"""
SCALE = 4
BANDS = ("blue", "green", "red", "nir")
