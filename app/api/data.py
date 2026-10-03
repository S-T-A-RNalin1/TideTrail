"""Operator data in: radar scenes, AIS files, optical chips.

Every route streams the file to disk with a size cap, hands it to
app/userdata.py for checking, and answers 422 with a plain sentence when the
file cannot be used. Nothing half-written is left behind.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import userdata

router = APIRouter()

MAX_SAR_MB = 1024
MAX_AIS_MB = 1024
MAX_OPTICAL_MB = 512
CHUNK = 1 << 20


async def _save(upload: UploadFile, max_mb: int) -> Path:
    tmp = userdata.upload_dir() / ("incoming_%s" % uuid4().hex)
    size = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                block = await upload.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if size > max_mb * CHUNK:
                    raise HTTPException(413, "%s is larger than %d MB." % (upload.filename, max_mb))
                fh.write(block)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, "%s is empty." % (upload.filename or "The file"))
    return tmp


def _fail(exc: Exception, tmp: Optional[Path] = None):
    if tmp is not None:
        tmp.unlink(missing_ok=True)
    raise HTTPException(422, str(exc)) from exc


@router.post("/api/data/sar")
async def add_sar(file: UploadFile = File(...), t_sat: Optional[str] = Form(None),
                  title: Optional[str] = Form(None), optical: Optional[UploadFile] = File(None),
                  optical_time: Optional[str] = Form(None)) -> Dict[str, Any]:
    tmp = await _save(file, MAX_SAR_MB)
    try:
        out = userdata.register_sar(tmp, file.filename or "upload.tif", t_sat, title)
    except userdata.UploadError as exc:
        _fail(exc, tmp)
    if optical is not None and (optical.filename or ""):
        otmp = await _save(optical, MAX_OPTICAL_MB)
        try:
            out["optical"] = userdata.register_optical(otmp, optical.filename, out["scene"]["id"], optical_time)
        except userdata.UploadError as exc:
            out.setdefault("notes", []).append("Optical not added: %s" % exc)
        finally:
            otmp.unlink(missing_ok=True)
    return out


@router.post("/api/data/ais")
async def add_ais(file: UploadFile = File(...)) -> Dict[str, Any]:
    tmp = await _save(file, MAX_AIS_MB)
    try:
        return userdata.ingest_ais(tmp, file.filename or "ais.csv")
    except userdata.UploadError as exc:
        _fail(exc)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/api/data/optical")
async def add_optical(scene_id: str = Form(...), file: UploadFile = File(...),
                      acquired: Optional[str] = Form(None)) -> Dict[str, Any]:
    tmp = await _save(file, MAX_OPTICAL_MB)
    try:
        return userdata.register_optical(tmp, file.filename or "optical.tif", scene_id, acquired)
    except userdata.UploadError as exc:
        _fail(exc)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/api/data/metocean/{scene_id}")
def fetch_metocean(scene_id: str) -> Dict[str, Any]:
    try:
        return userdata.fetch_metocean(scene_id)
    except userdata.UploadError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/data/scenes/{scene_id}")
def remove_scene(scene_id: str) -> Dict[str, Any]:
    try:
        userdata.remove_scene(scene_id)
    except userdata.UploadError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"removed": scene_id}
