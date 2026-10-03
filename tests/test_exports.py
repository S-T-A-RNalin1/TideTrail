"""Tests for export endpoints and Maritime Pollution Attribution Note generation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.api.pipeline import _clean_nans
from app.api.report import compute_dossier_sha256, compute_scene_sha256
from app.jobs import store as job_store
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_job_id():
    listing = job_store.listing()
    # Prefer a real run with suspects if available
    for j in listing:
        if j.get("suspects", 0) > 0:
            return j["job_id"]
    return listing[0]["job_id"]


@pytest.fixture
def clean_job_id():
    listing = job_store.listing()
    for j in listing:
        if j.get("status") in ("clean_water", "no_oil_detected"):
            return j["job_id"]
    return None


def test_export_pdf_contains_telemetry_and_valid_pdf(client, sample_job_id):
    r = client.get(f"/api/jobs/{sample_job_id}/export?format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment;" in r.headers.get("content-disposition", "")
    assert f"attribution_{sample_job_id}.pdf" in r.headers.get("content-disposition", "")
    assert r.content.startswith(b"%PDF-1.")
    assert len(r.content) > 3000


def test_export_html_contains_sha256_and_itemized_telemetry(client, sample_job_id):
    r = client.get(f"/api/jobs/{sample_job_id}/export?format=html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "attachment;" in r.headers.get("content-disposition", "")
    assert f"attribution_{sample_job_id}.html" in r.headers.get("content-disposition", "")

    doc = job_store.load(sample_job_id)
    scene_hash = compute_scene_sha256(doc)
    dossier_hash = compute_dossier_sha256(doc)

    body = r.text
    assert "Maritime Pollution Attribution Note" in body
    assert "not legal proof" in body.lower()
    assert scene_hash in body
    assert dossier_hash in body
    assert "Cryptographic Chain of Custody" in body
    # The digests are computed from the document being exported, so the note can
    # only tell the reader to record them. It must not claim to have verified them.
    assert "DIGESTS COMPUTED AT EXPORT" in body
    assert "VERIFIED UNTAMPERED" not in body
    assert "Source Test" in body
    assert "Satellite Surveillance &amp; Scene Telemetry" in body or "Satellite Surveillance & Scene Telemetry" in body
    assert "Metocean &amp; Drift" in body or "Metocean & Drift" in body
    assert "Pipeline Execution Telemetry" in body

    # Suspect telemetry check if suspects present
    suspects = (doc.get("attribution") or {}).get("suspects", [])
    if suspects:
        s0 = suspects[0]
        assert str(s0.get("mmsi")) in body
        if s0.get("name"):
            assert s0["name"] in body


def test_export_json_attachment(client, sample_job_id):
    r = client.get(f"/api/jobs/{sample_job_id}/export?format=json")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    assert "attachment;" in r.headers.get("content-disposition", "")
    assert f"tidetrail_{sample_job_id}.json" in r.headers.get("content-disposition", "")

    data = r.json()
    assert data["job_id"] == sample_job_id
    assert "detection" in data


def test_export_geojson_attachment(client, sample_job_id):
    r = client.get(f"/api/jobs/{sample_job_id}/export?format=geojson")
    assert r.status_code == 200
    assert "geo+json" in r.headers["content-type"]
    assert "attachment;" in r.headers.get("content-disposition", "")
    assert f"tidetrail_{sample_job_id}.geojson" in r.headers.get("content-disposition", "")

    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    assert "features" in fc
    assert isinstance(fc["features"], list)


def test_export_alias_endpoint(client, sample_job_id):
    for fmt in ("pdf", "html", "json", "geojson"):
        r = client.get(f"/api/export/{sample_job_id}?format={fmt}")
        assert r.status_code == 200


def test_report_endpoints_compatibility(client, sample_job_id):
    # GET /api/report/{id} (inline HTML for browser view)
    r = client.get(f"/api/report/{sample_job_id}")
    assert r.status_code == 200
    assert "content-disposition" not in r.headers
    assert "Maritime Pollution Attribution Note" in r.text

    # GET /api/report/{id}?format=pdf
    r_pdf = client.get(f"/api/report/{sample_job_id}?format=pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.content.startswith(b"%PDF-1.")

    # GET /api/report/{id}?download=1
    r_dl = client.get(f"/api/report/{sample_job_id}?download=1")
    assert r_dl.status_code == 200
    assert "attachment;" in r_dl.headers.get("content-disposition", "")

    # GET /api/report/{id}/pdf
    r_direct_pdf = client.get(f"/api/report/{sample_job_id}/pdf")
    assert r_direct_pdf.status_code == 200
    assert r_direct_pdf.content.startswith(b"%PDF-1.")


def test_download_flag_on_jobs_and_geojson(client, sample_job_id):
    r_job = client.get(f"/api/jobs/{sample_job_id}?download=1")
    assert r_job.status_code == 200
    assert "attachment;" in r_job.headers.get("content-disposition", "")

    r_geo = client.get(f"/api/jobs/{sample_job_id}/geojson?download=1")
    assert r_geo.status_code == 200
    assert "attachment;" in r_geo.headers.get("content-disposition", "")


def test_clean_scene_exports(client, clean_job_id):
    if not clean_job_id:
        pytest.skip("No clean water scene job stored.")
    r_pdf = client.get(f"/api/jobs/{clean_job_id}/export?format=pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.content.startswith(b"%PDF-1.")

    r_html = client.get(f"/api/jobs/{clean_job_id}/export?format=html")
    assert r_html.status_code == 200
    assert "Clean Water" in r_html.text or "clean" in r_html.text.lower()


def test_unsupported_export_format_returns_400(client, sample_job_id):
    r = client.get(f"/api/jobs/{sample_job_id}/export?format=xml")
    assert r.status_code == 400
    assert "Unsupported export format" in r.text


def test_export_nonexistent_job_returns_404(client):
    r = client.get("/api/jobs/job_definitely_nonexistent/export?format=pdf")
    assert r.status_code == 404


def test_sha256_verification_matches_sar_file(sample_job_id):
    doc = job_store.load(sample_job_id)
    scene = doc.get("scene") or {}
    sar_path = scene.get("sar_path")
    if sar_path and Path(sar_path).is_file():
        file_sha256 = hashlib.sha256(Path(sar_path).read_bytes()).hexdigest()
        computed = compute_scene_sha256(doc)
        assert computed == file_sha256
    dossier_sha256 = compute_dossier_sha256(doc)
    assert len(dossier_sha256) == 64
    assert all(c in "0123456789abcdef" for c in dossier_sha256)
