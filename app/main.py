"""TideTrail SRM FastAPI application.

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Serves the super-resolution console at / and the API under /api. Preview images
are served read only under /data/cache. Nothing here reaches the network; the
scripts under scripts/ are the only code that does, and they write into data/.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import sr as sr_routes

app = FastAPI(
    title=config.UI_TITLE,
    version=config.VERSION,
    description=("Deep learning super-resolution of 10 m Sentinel-2 imagery to 2.5 m, with a per-pixel "
                 "uncertainty map. %s, NTRO, Space Technology." % config.SIH_ID),
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.include_router(sr_routes.router)

app.mount("/data/cache", StaticFiles(directory=str(config.CACHE_DIR)), name="data_cache")
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": config.VERSION, "sih_id": config.SIH_ID,
            "model_present": sr_routes.model_path().exists(),
            "validation_present": sr_routes.validation_path().exists()}


def _asset_stamp() -> str:
    """A cache key that changes when the page assets change, so a browser never pairs old script with new markup."""
    h = hashlib.sha256(config.VERSION.encode("utf-8"))
    for name in ("sr.js", "sr.css", "sr.html", "style.css"):
        try:
            h.update((Path(config.STATIC_DIR) / name).read_bytes())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:12]


@app.get("/", include_in_schema=False)
@app.get("/sr", include_in_schema=False)
def console():
    page = Path(config.STATIC_DIR) / "sr.html"
    if not page.exists():
        return JSONResponse({"error": "static/sr.html is missing"}, status_code=500)
    html = page.read_text(encoding="utf-8").replace("__V__", _asset_stamp())
    return HTMLResponse(html, headers={"Cache-Control": "no-store, must-revalidate"})
