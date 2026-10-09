"""Paths and identity for TideTrail SRM (SIH26142).

Everything is a plain path under the repository. Two environment variables move
the data and the checkpoint elsewhere: TIDETRAIL_DATA and TIDETRAIL_MODELS.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str):
    """A setting read as TIDETRAIL_* first and the project's earlier TIDETRACE_* name second."""
    new = os.environ.get("TIDETRAIL_" + name)
    return new if new is not None else os.environ.get("TIDETRACE_" + name)


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = Path(_env("DATA") or ROOT_DIR / "data")
CACHE_DIR = DATA_DIR / "cache"
MODELS_DIR = Path(_env("MODELS") or ROOT_DIR / "models")
STATIC_DIR = APP_DIR / "static"

for _d in (DATA_DIR, CACHE_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SIH_ID = "SIH26142"
UI_TITLE = "TideTrail Super-resolution Mapping"
VERSION = "2.0.0"
