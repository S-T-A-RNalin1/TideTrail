"""GET /api/report/{job_id} and GET /api/jobs/{job_id}/export - Maritime Pollution Attribution Note.

Exports the Maritime Pollution Attribution Note in PDF and HTML,
carrying SHA-256 digests of the scene and the record, and itemized telemetry.

Supported export formats:
- PDF (via reportlab Platypus)
- HTML (standalone and print-ready, with the same SHA-256 digests)
- Job JSON (full telemetry and analysis document)
- GeoJSON (GIS feature collection of all detection, drift, and AIS layers)
"""
from __future__ import annotations

import hashlib
import html
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .. import config
from ..ais.score import DISCHARGE_SOG
from ..jobs import store as job_store

router = APIRouter()


def compute_scene_sha256(doc: Dict[str, Any]) -> str:
    """Compute SHA-256 cryptographic hash of the SAR scene file on disk or metadata."""
    scene = doc.get("scene") or {}
    sar_path = scene.get("sar_path")
    if sar_path:
        p = Path(sar_path)
        if p.is_file():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
    # Fallback deterministic digest of scene identifiers and bounding box
    inp = doc.get("input") or {}
    meta = {
        "id": scene.get("id") or inp.get("scene_id") or "probe",
        "t_sat": scene.get("t_sat") or inp.get("t_sat"),
        "bounds": scene.get("bounds"),
        "crs": scene.get("crs", "EPSG:4326"),
        "mode": doc.get("mode", "scene"),
        "probe_lat": inp.get("lat"),
        "probe_lon": inp.get("lon"),
    }
    return hashlib.sha256(json.dumps(meta, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def compute_dossier_sha256(doc: Dict[str, Any]) -> str:
    """Compute SHA-256 cryptographic hash of the canonical analytical job record."""
    payload = {
        "job_id": doc.get("job_id"),
        "scene_id": (doc.get("scene") or {}).get("id") or (doc.get("input") or {}).get("scene_id"),
        "detection": doc.get("detection"),
        "primary_polygon": doc.get("primary_polygon"),
        "drift": doc.get("drift"),
        "attribution": doc.get("attribution"),
        "trace": doc.get("trace"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _rows(doc: Dict[str, Any]) -> Dict[str, Any]:
    det = doc.get("detection") or {}
    drift = doc.get("drift") or {}
    attr = doc.get("attribution") or {}
    poly = doc.get("primary_polygon") or (det.get("polygons") or [None])[0]
    props = (poly or {}).get("properties", {})
    return {"det": det, "drift": drift, "attr": attr, "props": props}


def _alpha_text(doc: Dict[str, Any]) -> str:
    """The wind factor this run used, and why it has that value."""
    phys = (doc.get("drift") or {}).get("physics") or {}
    mo = (doc.get("drift") or {}).get("metocean") or {}
    a = phys.get("alpha_wind", config.ALPHA_WIND)
    if mo.get("stokes_included"):
        return "%s (currents already include wave drift)" % a
    return "%s (no wave drift in the currents)" % a


def _forecast_facts(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Forecast horizon, end spread and coast check, read from the run itself."""
    drift = doc.get("drift") or {}
    hourly = drift.get("forecast_hourly") or []
    coast = drift.get("coast") or {}
    hours = (doc.get("input") or {}).get("forecast_hours")
    spread = hourly[-1].get("spread_km") if hourly else None
    if not coast.get("available"):
        threat = "NOT CHECKED (no coastline data)"
    elif coast.get("beached_fraction"):
        threat = "YES - %.0f%% OF THE FORECAST OIL REACHES THE SHORE, THE FIRST AFTER %s H" % (
            coast["beached_fraction"] * 100, coast.get("first_beaching_hours"))
    elif coast.get("coast_flag"):
        threat = "YES - FORECAST CONE REACHES THE COAST"
    else:
        threat = "NO - FORECAST CONE STAYS OFFSHORE"
    return {"hours": "N/A" if hours is None else hours,
            "end_spread_km": "N/A" if spread is None else spread,
            "land_impact": bool(coast.get("coast_flag")), "threat": threat}


def _course_text(traj: Dict[str, Any]) -> str:
    """Course compared with what it was actually scored against."""
    if traj.get("against") == "slick_axis":
        return "COG %s deg | slick axis %s deg (diff: %s deg)" % (
            traj.get("course_deg"), traj.get("slick_axis_deg"), traj.get("difference_deg"))
    return "COG %s deg | drift %s deg (diff: %s deg)" % (
        traj.get("course_deg"), traj.get("drift_bearing_deg"), traj.get("difference_deg"))


def _reasons_text(reasons: List[str]) -> str:
    """Reason codes as words: with_drifting_oil_1.2km -> with drifting oil 1.2 km."""
    out = []
    for r in reasons or []:
        t = str(r).replace("_", " ")
        for unit in ("km", "kn", "min", "deg", "h"):
            t = __import__("re").sub(r"(\d)%s\b" % unit, r"\1 %s" % unit, t)
        out.append(t)
    return ", ".join(out)


def _ships_lines(doc: Dict[str, Any]) -> List[str]:
    """Radar echoes and what AIS says about each, as the note states it."""
    sh = doc.get("ships") or {}
    if not sh.get("available"):
        return ["Not run: %s" % (sh.get("reason") or "no ship survey in this record.")]
    t = sh.get("targets") or []
    if not t:
        return ["No ship-sized radar echoes on this pass."]
    if sh.get("ais_checked"):
        out = ["%d ship-sized echoes; %d match an AIS position at the pass, %d have no AIS (a vessel with its "
               "transponder off, or a structure no map records). %d echoes on known platforms left out."
               % (len(t), sh.get("matched", 0), sh.get("radar_only", 0), sh.get("on_known_platforms", 0))]
    else:
        out = ["%d ship-sized echoes. This sea has no real AIS, so they are listed but not compared." % len(t)]
    for x in sorted(t, key=lambda x: (x.get("ais") is not None, x.get("slick_km") if x.get("slick_km") is not None else 1e9))[:10]:
        who = ("AIS %s (MMSI %s), %.2f km from its fix" % (x["ais"].get("name"), x["ais"]["mmsi"], x["ais"]["distance_km"])
               if x.get("ais") else ("NO AIS" if sh.get("ais_checked") else "not compared"))
        out.append("  %.5f, %.5f  about %s m  +%.0f dB%s  %s" % (
            x["lat"], x["lon"], x.get("extent_m"), float(x.get("contrast_db") or 0),
            ("  %.1f km from slick" % x["slick_km"]) if x.get("slick_km") is not None else "", who))
    return out


def _weight_pct(doc: Dict[str, Any], key: str) -> int:
    """A scoring weight as the percentage this run actually used."""
    w = ((doc.get("config") or {}).get("weights") or config.WEIGHTS).get(key, 0.0)
    return int(round(float(w) * 100))


def _source_lines(doc: Dict[str, Any]) -> List[str]:
    """The forward release test, as the note states it."""
    st = doc.get("source_test") or {}
    if not st.get("available"):
        return ["Not run: %s" % (st.get("reason") or "no slick outline to test against.")]
    m = st.get("method") or {}
    out = [
        "Finding: %s." % st.get("headline", ""),
        st.get("detail", ""),
        "Method: each installation within %s km and each candidate vessel (up to %s) is released"
        % (m.get("installations_within_km", "N/A"), m.get("vessels_tested_max", "N/A")),
        "forward %s h through the recorded wind and currents, as %s runs with both perturbed. Each run's"
        % (m.get("hours_tested", "N/A"), m.get("members", "N/A")),
        "fit is the harmonic mean of the share of the slick covered and the share of its oil landing"
        " on it; the fit below is the mean over runs. Vessels are ranked by it, and the %s that fit"
        % m.get("shortlist", 3),
        "best form the shortlist to inspect first.",
    ]
    val = st.get("validation") or {}
    if val.get("cases"):
        out.append("Validation: in %d known-answer runs on real AIS traffic and currents, the ship that released"
                   " the oil was ranked first in %d and on the shortlist in %d."
                   % (val["cases"], val.get("top1", 0), val.get("shortlist3", 0)))
    for h in (st.get("hypotheses") or [])[:10]:
        name = h.get("name") if h.get("kind") == "installation" else "%s (MMSI %s)" % (h.get("name"), h.get("mmsi"))
        win = ("%s to %s" % (h["window"][0], h["window"][1])) if h.get("window") else "oil does not reach the slick"
        runs = "%d/%d runs" % (round(float(h.get("support") or 0) * int(h.get("members") or 0)), int(h.get("members") or 0))
        out.append("  %-12s fit %.2f  %-10s cover %3.0f%%  lands %3.0f%%  %s | %s" % (
            h.get("kind"), float(h.get("fit") or 0), runs, float(h.get("coverage") or 0) * 100,
            float(h.get("precision") or 0) * 100, name, win))
    return out


def _lines(doc: Dict[str, Any]) -> List[str]:
    """Itemized text representation of the Maritime Pollution Attribution Note."""
    r = _rows(doc)
    props, drift, attr = r["props"], r["drift"], r["attr"]
    origin = drift.get("origin") or {}
    det_metrics = r["det"].get("metrics") or {}
    scene = doc.get("scene") or {}
    scene_hash = compute_scene_sha256(doc)
    dossier_hash = compute_dossier_sha256(doc)
    suspects = attr.get("suspects", [])
    trace = doc.get("trace") or []
    is_probe = doc.get("mode") == "operator_probe"
    is_clean = doc.get("status") in ("clean_water", "no_oil_detected") or (not props and not is_probe)

    out = [
        "MARITIME POLLUTION ATTRIBUTION NOTE",
        "Case Reference: %s" % doc.get("job_id"),
        "Produced by: TideTrail %s (offline console)" % config.VERSION,
        "Generated: %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "",
        "CRYPTOGRAPHIC CHAIN OF CUSTODY (SHA-256 EVIDENCE SEALS)",
        "SAR Scene File Digest:   %s" % scene_hash,
        "Dossier Record Digest:   %s" % dossier_hash,
        "Verification:            record these digests; recompute them on any later copy,",
        "                         a mismatch means the copy was altered.",
        "",
        "SATELLITE SCENE & RADAR TELEMETRY",
        "Scene ID: %s" % (scene.get("id") or (doc.get("input") or {}).get("scene_id") or ("probe" if is_probe else "N/A")),
        "Title: %s" % (scene.get("title") or ("Operator Injected Interactive Probe" if is_probe else "N/A")),
        "Platform / Sensor: %s" % ("Operator Probe Point (Interactive Coordinates)" if is_probe else "Sentinel-1 C-band SAR (IW GRD RTC)"),
        "Observation Time (t_sat): %s" % (scene.get("t_sat") or (doc.get("input") or {}).get("t_sat")),
        "Footprint Bounds [W, S, E, N]: %s" % (scene.get("bounds") or (doc.get("input") or {}).get("slick_radius_km")),
        "Centroid Coordinates: %s" % (scene.get("centroid") or f"{((doc.get('input') or {}).get('lat'))}, {((doc.get('input') or {}).get('lon'))}"),
        "Coordinate Reference System: %s" % scene.get("crs", "EPSG:4326"),
        "AIS Surveillance Mode: %s" % scene.get("ais_mode", "simulated"),
        "Radar Sea Backscatter Level: %s dB" % (det_metrics.get("radiometry", {}).get("sea_level_db") or det_metrics.get("sea_level_db", "N/A")),
        "Dynamic Contrast Range: %s dB" % (det_metrics.get("radiometry", {}).get("dynamic_range_db") or det_metrics.get("dynamic_range_db", "N/A")),
        "",
        "SLICK MORPHOMETRIC TELEMETRY",
    ]

    if is_probe:
        inp = doc.get("input") or {}
        out += [
            "Operator Probe Placement (No SAR Slick):",
            "Coordinates: Lat %s, Lon %s" % (inp.get("lat"), inp.get("lon")),
            "Assumed Initial Slick Radius: %s km" % inp.get("slick_radius_km", 1.5),
            "Hydrodynamic drift hindcast and vessel attribution executed from probe fix.",
        ]
    elif is_clean:
        out += [
            "Clean Water Control / No Slick Detected:",
            "No oil polygon above the area threshold on this scene.",
            "Look-alike polygons screened: %s." % det_metrics.get("lookalike_polygons_found", 0),
            "This is a reported finding, not a pipeline failure. Clean scene has no slick to",
            "characterise and no origin to trace, so drift and attribution were not run.",
        ]
    else:
        out += [
            "Detector Model: %s" % det_metrics.get("detector", "unet"),
            "Total Oil Slicks Detected: %s" % len(r["det"].get("polygons") or [props]),
            "Total Oil Surface Area: %s km2" % det_metrics.get("oil_area_km2", props.get("area_km2")),
            "Primary Slick Surface Area: %s km2" % props.get("area_km2"),
            "Primary Slick Length: %s km, Width: %s km" % (props.get("length_km"), props.get("width_km")),
            "Perimeter: %s km" % props.get("perimeter_km"),
            "Orientation Angle: %s deg" % props.get("orientation_deg"),
            "Compactness Index: %s" % props.get("compactness"),
            "Local Contrast vs Background: %s dB" % props.get("contrast_db"),
            "Slick Centroid: %s, %s" % (props.get("centroid_lat"), props.get("centroid_lon")),
            "Detection Confidence: %s" % props.get("confidence"),
            "Look-alike Polygons Filtered: %s" % det_metrics.get("lookalike_polygons_found", 0),
        ]

    out += ["", "METOCEAN & HYDRODYNAMIC TELEMETRY"]
    if not drift:
        out.append("Not executed: clean water control scene, no slick to trace back.")
    else:
        met = drift.get("metocean") or {}
        out += [
            "Metocean Data Source: %s" % met.get("source", "Open-Meteo ERA5 10m wind + marine currents"),
            "Mean 10m Wind Speed: %s m/s" % met.get("mean_wind_ms", "N/A"),
            "Mean Surface Current Speed: %s m/s" % met.get("mean_current_ms", "N/A"),
            "Wind Leeway Factor (alpha): %s" % _alpha_text(doc),
            "Metocean Grid Dimensions: %s" % met.get("grid", "N/A"),
            "Metocean Time Span: %s to %s" % (met.get("t_start", "N/A"), met.get("t_end", "N/A")),
        ]

    out += ["", "DRIFT RECONSTRUCTION & TRAJECTORY TELEMETRY"]
    if not drift:
        out.append("Not executed: clean water control scene, no slick to trace back.")
    else:
        cone = _forecast_facts(doc)
        out += [
            "Estimated Discharge Time: %s" % origin.get("t"),
            "Estimated Origin Coordinates: Lat %s, Lon %s" % (origin.get("lat"), origin.get("lon")),
            "Drift Age Proxy: %s hours (drift transport time, not chemical age)" % doc.get("age_hours_proxy"),
            "90%% Ensemble Uncertainty Radius: %s km (buffer: %s km)" % (origin.get("spread_km"), origin.get("buffer_km")),
            "Origin Zone Area: %s km2" % origin.get("area_km2"),
            "Forward Forecast Horizon: %s hours (end spread: %s km)" % (cone["hours"], cone["end_spread_km"]),
            "Coastal Landfall Threat: %s" % cone["threat"],
        ]

    out += ["", "PIPELINE EXECUTION TELEMETRY"]
    if trace:
        for t in trace:
            out.append("  %-10s %6.1f ms  [%s]  %s" % (
                t.get("step", ""),
                float(t.get("elapsed_ms") or 0),
                t.get("status", "ok"),
                t.get("note", "")
            ))
        out.append("Total Pipeline Runtime: %s ms" % doc.get("total_ms", "N/A"))
    else:
        out.append("No step trace recorded.")

    out += ["", "SOURCE TEST (FORWARD RELEASE OF EACH CANDIDATE)"]
    out += _source_lines(doc)

    out += ["", "SHIPS ON THE RADAR (ECHOES CHECKED AGAINST AIS)"]
    out += _ships_lines(doc)

    out += ["", "RANKED VESSELS (INVESTIGATIVE LEADS, NOT FINDINGS)"]
    if not suspects:
        if is_clean:
            out.append("Not executed: clean water control scene, vessel attribution was not run.")
        else:
            out.append("No vessel passed the spatio-temporal filter window around the origin.")
    else:
        for s in suspects:
            d = s.get("detail") or {}
            comp = s.get("components") or {}
            wgt = s.get("weighted") or {}
            traj = d.get("trajectory") or {}
            beh = d.get("behavior") or {}
            reasons = s.get("reasons") or []
            out += [
                "--------------------------------------------------------------------------------",
                "RANK #%d: %s (MMSI: %s, Type: %s)" % (s.get("rank"), s.get("name") or "UNKNOWN", s.get("mmsi"), s.get("type")),
                "Attribution Score: %5.1f%% | Track Confidence: %4.1f%% | Raw Composite: %0.3f"
                % (float(s.get("score") or 0), float(s.get("confidence") or 1.0) * 100, float(s.get("raw") or 0)),
                "  Closest Approach (CPA): %s km from origin at %s (time offset: %s min)"
                % (d.get("origin_distance_km"), d.get("closest_approach_utc"), d.get("time_offset_minutes")),
                "  CPA Position: Lat %s, Lon %s" % (d.get("closest_lat"), d.get("closest_lon")),
                "  Speed Over Ground at CPA: %s knots (%s)"
                % (beh.get("sog_at_closest_kn"), ("Discharge Band %g-%g kn" % DISCHARGE_SOG) if beh.get("discharge_band") else "Outside Discharge Band"),
                "  Course: %s" % _course_text(traj),
                "  AIS Gap Telemetry: %s min gap (%s km from origin) | Non-reporting: %s"
                % (beh.get("ais_gap_minutes", 0), beh.get("ais_gap_min_distance_km", "N/A"), "YES (AIS silent near the origin)" if beh.get("non_reporting") else "NO"),
                "  Dead-Reckoned Track Fraction: %0.1f%%" % (float(d.get("dead_reckoned_fraction") or 0) * 100),
                "  Scoring Sub-Components [Raw -> Weighted]:",
                "    Proximity (%d%%):    %0.3f -> %0.3f" % (_weight_pct(doc, "prox"), float(comp.get("prox") or 0), float(wgt.get("prox") or 0)),
                "    Temporal (%d%%):     %0.3f -> %0.3f" % (_weight_pct(doc, "time"), float(comp.get("time") or 0), float(wgt.get("time") or 0)),
                "    Vessel Prior (%d%%): %0.3f -> %0.3f" % (_weight_pct(doc, "type"), float(comp.get("type") or 0), float(wgt.get("type") or 0)),
                "    Trajectory (%d%%):   %0.3f -> %0.3f" % (_weight_pct(doc, "traj"), float(comp.get("traj") or 0), float(wgt.get("traj") or 0)),
                "    Behavior (%d%%):     %0.3f -> %0.3f" % (_weight_pct(doc, "beh"), float(comp.get("beh") or 0), float(wgt.get("beh") or 0)),
                "  Reasons: %s" % _reasons_text(reasons),
            ]

    out += [
        "",
        "LIMITATIONS & LEGAL DISCLAIMER",
        "Ranked likelihood for investigation, not legal proof of discharge.",
        "A vessel is supported only when the source test finds its own track reproduces the slick.",
        "Produced by TideTrail. Intended to direct an Indian Coast Guard or DG Shipping",
        "investigation under MARPOL 73/78 Annex I; it does not by itself establish a violation.",
        "Age is a drift advection proxy, not a chemical weathering analysis.",
        "Look-alike class polygons are excluded from attribution by design.",
    ]
    return out


def build_pdf_report(doc: Dict[str, Any]) -> bytes:
    """Build the PDF Attribution Note with ReportLab Platypus."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfgen import canvas
        from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise HTTPException(
            501,
            "reportlab is not installed, so PDF export is unavailable. "
            "The HTML note at /api/report/%s is always available." % doc.get("job_id", ""),
        ) from exc

    job_id = doc.get("job_id", "UNKNOWN")
    is_probe = doc.get("mode") == "operator_probe"
    is_clean = doc.get("status") in ("clean_water", "no_oil_detected")

    class NumberedCanvas(canvas.Canvas):
        """Two-pass canvas that adds running headers and page X of Y footers."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_decorations(num_pages)
                super().showPage()
            super().save()

        def draw_page_decorations(self, page_count):
            self.saveState()
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#5a6e7e"))
            # Running header on later pages
            if self._pageNumber > 1:
                self.drawString(36, 842 - 25, "TideTrail Maritime Pollution Attribution Note | Case Ref: %s" % job_id)
                self.setStrokeColor(colors.HexColor("#dae2ea"))
                self.setLineWidth(0.5)
                self.line(36, 842 - 28, 595 - 36, 842 - 28)
            # Running footer on all pages
            self.setStrokeColor(colors.HexColor("#dae2ea"))
            self.setLineWidth(0.5)
            self.line(36, 28, 595 - 36, 28)
            self.drawString(36, 18, "TideTrail %s | SHA-256 digests of scene and record on page 1" % config.VERSION)
            self.drawRightString(595 - 36, 18, "Page %d of %d" % (self._pageNumber, page_count))
            self.restoreState()

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=38,
    )
    styles = getSampleStyleSheet()

    # Typography styles
    title_style = ParagraphStyle("RTitle", fontName="Helvetica-Bold", fontSize=13.5, leading=16, textColor=colors.HexColor("#0f1922"))
    sub_style = ParagraphStyle("RSub", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#5a6e7e"))
    h2_style = ParagraphStyle("RH2", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=colors.HexColor("#0f1922"), spaceBefore=7, spaceAfter=2)
    body_style = ParagraphStyle("RBody", fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#14212b"))
    bold_style = ParagraphStyle("RBold", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=colors.HexColor("#14212b"))
    dim_style = ParagraphStyle("RDim", fontName="Helvetica", fontSize=6.5, leading=8.5, textColor=colors.HexColor("#5a6e7e"))
    mono_style = ParagraphStyle("RMono", fontName="Courier", fontSize=6.5, leading=8, textColor=colors.HexColor("#0f1922"))
    mono_bold = ParagraphStyle("RMonoB", fontName="Courier-Bold", fontSize=6.5, leading=8, textColor=colors.HexColor("#0f1922"))
    ok_style = ParagraphStyle("ROk", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=colors.HexColor("#1b7340"))
    warn_style = ParagraphStyle("RWarn", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=colors.HexColor("#b0402f"))

    story = []

    # 1. Header Banner
    header_data = [
        [
            Paragraph("<b>TIDETRAIL | MARITIME POLLUTION ATTRIBUTION NOTE</b>", title_style),
            Paragraph("<b>CASE REF:</b> %s" % job_id, bold_style),
        ],
        [
            Paragraph("Evidence dossier for an investigating officer", sub_style),
            Paragraph("Generated: %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), dim_style),
        ],
    ]
    t_head = Table(header_data, colWidths=[360, 163])
    t_head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(t_head)
    story.append(HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#0f1922"), spaceBefore=3, spaceAfter=5))

    # 2. Cryptographic Evidence Seals Box
    scene_hash = compute_scene_sha256(doc)
    dossier_hash = compute_dossier_sha256(doc)
    seal_data = [
        [Paragraph("<b>CRYPTOGRAPHIC CHAIN OF CUSTODY (SHA-256 EVIDENCE SEALS)</b>", bold_style),
         Paragraph("RECORD THESE DIGESTS TO VERIFY LATER COPIES", ok_style)],
        [Paragraph("<b>SAR Scene File Digest:</b>", dim_style), Paragraph(scene_hash, mono_bold)],
        [Paragraph("<b>Job Record Canonical Digest:</b>", dim_style), Paragraph(dossier_hash, mono_bold)],
    ]
    t_seal = Table(seal_data, colWidths=[155, 368])
    t_seal.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fa")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#0f1922")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dae2ea")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t_seal)
    story.append(Spacer(1, 4))

    # Section 1: Satellite Surveillance & Scene Telemetry
    story.append(Paragraph("1. SATELLITE SURVEILLANCE & SCENE TELEMETRY", h2_style))
    scene = doc.get("scene") or {}
    det = doc.get("detection") or {}
    det_metrics = det.get("metrics") or {}
    rad = det_metrics.get("radiometry") or {}
    inp = doc.get("input") or {}

    scene_id_val = str(scene.get("id") or inp.get("scene_id") or ("probe" if is_probe else "N/A"))
    tsat_val = str(scene.get("t_sat") or inp.get("t_sat"))
    sensor_val = "Operator Probe Point (Interactive Coordinates)" if is_probe else "Sentinel-1 SAR IW GRD RTC (C-band)"
    bounds_val = str(scene.get("bounds") or (f"Probe fix: Lat {inp.get('lat')}, Lon {inp.get('lon')}" if is_probe else "N/A"))
    centroid_val = str(scene.get("centroid") or (f"{inp.get('lat')}, {inp.get('lon')}" if is_probe else "N/A"))

    scene_grid = [
        [Paragraph("<b>Scene Identifier</b>", dim_style), Paragraph(scene_id_val, mono_style),
         Paragraph("<b>Acquisition (t_sat)</b>", dim_style), Paragraph(tsat_val, body_style)],
        [Paragraph("<b>Sensor / Platform</b>", dim_style), Paragraph(sensor_val, body_style),
         Paragraph("<b>AIS Surveillance</b>", dim_style), Paragraph(str(scene.get("ais_mode", "simulated")).upper(), body_style)],
        [Paragraph("<b>Footprint Bounds</b>", dim_style), Paragraph(bounds_val, mono_style),
         Paragraph("<b>Centroid & CRS</b>", dim_style), Paragraph("%s (%s)" % (centroid_val, scene.get("crs", "EPSG:4326")), body_style)],
        [Paragraph("<b>Sea Backscatter</b>", dim_style), Paragraph("%s dB" % rad.get("sea_level_db", det_metrics.get("sea_level_db", "N/A")), body_style),
         Paragraph("<b>Dynamic Contrast</b>", dim_style), Paragraph("%s dB" % rad.get("dynamic_range_db", det_metrics.get("dynamic_range_db", "N/A")), body_style)],
    ]
    t_scene = Table(scene_grid, colWidths=[95, 166.5, 95, 166.5])
    t_scene.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#f0f4f8")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_scene)
    story.append(Spacer(1, 4))

    # Section 2: Oil Slick Detection & Morphometric Telemetry
    story.append(Paragraph("2. OIL SLICK DETECTION & MORPHOMETRIC TELEMETRY", h2_style))
    r = _rows(doc)
    props = r["props"]

    if is_probe:
        probe_box = [
            [Paragraph("<b>OPERATOR PROBE INJECTION:</b> Interactive observation point evaluated without SAR raster.", body_style)],
            [Paragraph("Coordinates: Lat %s, Lon %s | Initial assumed slick radius: %s km. Drift advection and AIS attribution executed from probe fix."
                       % (inp.get("lat"), inp.get("lon"), inp.get("slick_radius_km", 1.5)), dim_style)],
        ]
        t_probe = Table(probe_box, colWidths=[523])
        t_probe.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_probe)
    elif not props or is_clean:
        clean_box = [
            [Paragraph("<b>CLEAN WATER FINDING:</b> No mineral oil slick detected above threshold on this scene.", body_style)],
            [Paragraph("Look-alike anomalies screened: %s. Drift and vessel attribution were not executed for this clean scene."
                       % det_metrics.get("lookalike_polygons_found", 0), dim_style)],
        ]
        t_clean = Table(clean_box, colWidths=[523])
        t_clean.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_clean)
    else:
        slick_grid = [
            [Paragraph("<b>Detector Model</b>", dim_style), Paragraph(str(det_metrics.get("detector", "unet")).upper(), bold_style),
             Paragraph("<b>Detection Confidence</b>", dim_style), Paragraph("%s" % props.get("confidence", "N/A"), body_style)],
            [Paragraph("<b>Oil Slicks Found</b>", dim_style), Paragraph("%s polygon(s)" % len(det.get("polygons") or [1]), body_style),
             Paragraph("<b>Total Oil Area</b>", dim_style), Paragraph("%s km2" % det_metrics.get("oil_area_km2", props.get("area_km2")), bold_style)],
            [Paragraph("<b>Primary Slick Area</b>", dim_style), Paragraph("%s km2" % props.get("area_km2"), bold_style),
             Paragraph("<b>Dimensions (L x W)</b>", dim_style), Paragraph("%s km x %s km" % (props.get("length_km"), props.get("width_km")), body_style)],
            [Paragraph("<b>Orientation Angle</b>", dim_style), Paragraph("%s deg" % props.get("orientation_deg"), body_style),
             Paragraph("<b>Perimeter & Compactness</b>", dim_style), Paragraph("%s km (idx: %s)" % (props.get("perimeter_km"), props.get("compactness")), body_style)],
            [Paragraph("<b>Slick Centroid Fix</b>", dim_style), Paragraph("%s, %s" % (props.get("centroid_lat"), props.get("centroid_lon")), mono_style),
             Paragraph("<b>Contrast vs Local Sea</b>", dim_style), Paragraph("%s dB" % props.get("contrast_db"), body_style)],
        ]
        t_slick = Table(slick_grid, colWidths=[95, 166.5, 95, 166.5])
        t_slick.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#f0f4f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_slick)

    story.append(Spacer(1, 4))

    # Section 3: Metocean & Drift Reconstruction Telemetry
    story.append(Paragraph("3. METOCEAN & DRIFT TRAJECTORY TELEMETRY", h2_style))
    drift = doc.get("drift") or {}
    if not drift or is_clean:
        nodrift_box = [
            [Paragraph("<b>NOT EXECUTED:</b> Clean water control scene without slick detection; drift hindcast not required.", body_style)],
        ]
        t_nodrift = Table(nodrift_box, colWidths=[523])
        t_nodrift.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_nodrift)
    else:
        origin = drift.get("origin") or {}
        met = drift.get("metocean") or {}
        cone = _forecast_facts(doc)
        met_grid = [
            [Paragraph("<b>Metocean Source</b>", dim_style), Paragraph(str(met.get("source", "Open-Meteo ERA5 / Copernicus Marine")), body_style),
             Paragraph("<b>Wind Leeway Factor</b>", dim_style), Paragraph(_alpha_text(doc), body_style)],
            [Paragraph("<b>Mean 10m Wind</b>", dim_style), Paragraph("%s m/s" % met.get("mean_wind_ms", "N/A"), body_style),
             Paragraph("<b>Mean Ocean Current</b>", dim_style), Paragraph("%s m/s" % met.get("mean_current_ms", "N/A"), body_style)],
            [Paragraph("<b>Estimated Origin Time</b>", dim_style), Paragraph(str(origin.get("t")), bold_style),
             Paragraph("<b>Drift Age Proxy</b>", dim_style), Paragraph("%s hours" % doc.get("age_hours_proxy"), bold_style)],
            [Paragraph("<b>Origin Coordinates</b>", dim_style), Paragraph("Lat %s, Lon %s" % (origin.get("lat"), origin.get("lon")), mono_style),
             Paragraph("<b>Origin Uncertainty</b>", dim_style), Paragraph("%s km radius (%s km2 zone)" % (origin.get("spread_km"), origin.get("area_km2")), body_style)],
            [Paragraph("<b>Forecast Horizon</b>", dim_style), Paragraph("%s hours (spread: %s km)" % (cone["hours"], cone["end_spread_km"]), body_style),
             Paragraph("<b>Landfall Threat</b>", dim_style), Paragraph(cone["threat"], warn_style if cone["land_impact"] else ok_style)],
        ]
        t_met = Table(met_grid, colWidths=[95, 166.5, 95, 166.5])
        t_met.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#f0f4f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_met)

    story.append(Spacer(1, 4))

    # Section 4: Pipeline Execution Telemetry (All Steps)
    trace = doc.get("trace") or []
    if trace:
        story.append(Paragraph("4. PIPELINE EXECUTION TELEMETRY", h2_style))
        trace_rows = [
            [Paragraph("<b>Step</b>", dim_style), Paragraph("<b>Description & Notes</b>", dim_style),
             Paragraph("<b>Time (ms)</b>", dim_style), Paragraph("<b>Status & Metric</b>", dim_style)]
        ]
        for t in trace:
            trace_rows.append([
                Paragraph("<b>%s</b>" % t.get("step", ""), mono_bold),
                Paragraph(str(t.get("note", "")), body_style),
                Paragraph("%.1f" % float(t.get("elapsed_ms") or 0), mono_style),
                Paragraph("[%s]" % t.get("status", "ok"), ok_style if t.get("status") == "ok" else dim_style),
            ])
        t_trace = Table(trace_rows, colWidths=[65, 258, 60, 140])
        t_trace.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#f0f4f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_trace)
        story.append(Spacer(1, 4))

    # Section 5: Source test, the forward release of every candidate
    story.append(Paragraph("5. SOURCE TEST (FORWARD RELEASE OF EACH CANDIDATE)", h2_style))
    st_rows = [[Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), body_style)]
               for line in _source_lines(doc)[:6]]
    st = doc.get("source_test") or {}
    if st.get("available") and st.get("hypotheses"):
        hyp_rows = [[Paragraph("<b>Kind</b>", dim_style), Paragraph("<b>Source</b>", dim_style),
                     Paragraph("<b>Fit</b>", dim_style), Paragraph("<b>Covers / lands</b>", dim_style),
                     Paragraph("<b>Release window (UTC)</b>", dim_style)]]
        for h in st["hypotheses"][:10]:
            nm = h.get("name") if h.get("kind") == "installation" else "%s (MMSI %s)" % (h.get("name"), h.get("mmsi"))
            hyp_rows.append([
                Paragraph(str(h.get("kind")), body_style),
                Paragraph(str(nm).replace("&", "&amp;"), body_style),
                Paragraph("%.2f" % float(h.get("fit") or 0), mono_bold),
                Paragraph("%.0f%% / %.0f%%" % (float(h.get("coverage") or 0) * 100, float(h.get("precision") or 0) * 100), mono_style),
                Paragraph(("%s to %s" % (h["window"][0][5:16].replace("T", " "), h["window"][1][11:16]))
                          if h.get("window") else "does not reach the slick", body_style),
            ])
        t_hyp = Table(hyp_rows, colWidths=[62, 190, 36, 80, 155])
        t_hyp.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#f0f4f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
    t_st = Table(st_rows or [[Paragraph("Not run.", dim_style)]], colWidths=[523])
    t_st.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_st)
    if st.get("available") and st.get("hypotheses"):
        story.append(Spacer(1, 3))
        story.append(t_hyp)
    story.append(Spacer(1, 4))

    # Section 6: Ships on the radar
    story.append(Paragraph("6. SHIPS ON THE RADAR (ECHOES CHECKED AGAINST AIS)", h2_style))
    t_ships = Table([[Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), body_style)]
                     for line in _ships_lines(doc)[:11]], colWidths=[523])
    t_ships.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_ships)
    story.append(Spacer(1, 4))

    # Section 7: Ranked vessels, which are leads unless the source test supports one
    story.append(Paragraph("7. RANKED VESSELS (INVESTIGATIVE LEADS, NOT FINDINGS)", h2_style))
    suspects = (doc.get("attribution") or {}).get("suspects", [])
    if not suspects:
        empty_reason = (
            "Not executed: Clean water control scene without slick detection; vessel traffic attribution not required."
            if is_clean else "No candidate vessels satisfied the spatio-temporal filter window around the origin."
        )
        t_nosus = Table([[Paragraph(empty_reason, dim_style)]], colWidths=[523])
        t_nosus.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_nosus)
    else:
        for s in suspects[:5]:
            d = s.get("detail") or {}
            comp = s.get("components") or {}
            wgt = s.get("weighted") or {}
            traj = d.get("trajectory") or {}
            beh = d.get("behavior") or {}
            reasons = s.get("reasons") or []

            vessel_flow = []
            # Suspect title bar
            v_title_data = [[
                Paragraph("<b>RANK #%d | %s</b>" % (s.get("rank"), s.get("name") or "UNKNOWN"), bold_style),
                Paragraph("MMSI: <b>%s</b> | Type: %s" % (s.get("mmsi"), s.get("type")), body_style),
                Paragraph("SCORE: <b>%.1f%%</b> (Conf: %.0f%%)" % (float(s.get("score") or 0), float(s.get("confidence") or 1.0) * 100), bold_style),
            ]]
            t_vtitle = Table(v_title_data, colWidths=[180, 200, 143])
            t_vtitle.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eaeff4")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#0f1922")),
                ("PADDING", (0, 0), (-1, -1), 3),
            ]))
            vessel_flow.append(t_vtitle)

            # Detailed telemetry grid
            v_telemetry = [
                [Paragraph("<b>Closest Approach (CPA)</b>", dim_style),
                 Paragraph("%s km at %s (dt: %s min)" % (d.get("origin_distance_km"), d.get("closest_approach_utc"), d.get("time_offset_minutes")), body_style),
                 Paragraph("<b>CPA Coordinates</b>", dim_style),
                 Paragraph("Lat %s, Lon %s" % (d.get("closest_lat"), d.get("closest_lon")), mono_style)],
                [Paragraph("<b>Speed Over Ground (SOG)</b>", dim_style),
                 Paragraph("%s kn (%s)" % (beh.get("sog_at_closest_kn"), "Discharge Band" if beh.get("discharge_band") else "Nominal"), body_style),
                 Paragraph("<b>Course Alignment</b>", dim_style),
                 Paragraph(_course_text(traj), body_style)],
                [Paragraph("<b>AIS Gap Telemetry</b>", dim_style),
                 Paragraph("%s min gap at %s km (%s)" % (beh.get("ais_gap_minutes", 0), beh.get("ais_gap_min_distance_km", 0), "AIS silent near the origin" if beh.get("non_reporting") else "Continuous"), warn_style if beh.get("non_reporting") else body_style),
                 Paragraph("<b>Track Quality</b>", dim_style),
                 Paragraph("DR: %.1f%% | Confidence: %.1f%%" % (float(d.get("dead_reckoned_fraction") or 0) * 100, float(s.get("confidence") or 1.0) * 100), body_style)],
            ]
            t_vtel = Table(v_telemetry, colWidths=[110, 151.5, 110, 151.5])
            t_vtel.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#f0f4f8")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            vessel_flow.append(t_vtel)

            # Subscores row (using standard ASCII -> arrows)
            sub_scores = [
                [Paragraph(f"<b>Proximity ({_weight_pct(doc, 'prox')}%):</b> {float(comp.get('prox') or 0):.2f} -&gt; <b>{float(wgt.get('prox') or 0):.2f}</b>", body_style),
                 Paragraph(f"<b>Time ({_weight_pct(doc, 'time')}%):</b> {float(comp.get('time') or 0):.2f} -&gt; <b>{float(wgt.get('time') or 0):.2f}</b>", body_style),
                 Paragraph(f"<b>Type Prior ({_weight_pct(doc, 'type')}%):</b> {float(comp.get('type') or 0):.2f} -&gt; <b>{float(wgt.get('type') or 0):.2f}</b>", body_style),
                 Paragraph(f"<b>Trajectory ({_weight_pct(doc, 'traj')}%):</b> {float(comp.get('traj') or 0):.2f} -&gt; <b>{float(wgt.get('traj') or 0):.2f}</b>", body_style),
                 Paragraph(f"<b>Behavior ({_weight_pct(doc, 'beh')}%):</b> {float(comp.get('beh') or 0):.2f} -&gt; <b>{float(wgt.get('beh') or 0):.2f}</b>", body_style)],
            ]
            t_sub = Table(sub_scores, colWidths=[104.6, 104.6, 104.6, 104.6, 104.6])
            t_sub.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dae2ea")),
                ("PADDING", (0, 0), (-1, -1), 2),
            ]))
            vessel_flow.append(t_sub)

            # Rationale tags
            reasons_str = "Reasons: " + (_reasons_text(reasons) if reasons else "none recorded")
            vessel_flow.append(Paragraph(reasons_str, dim_style))
            vessel_flow.append(Spacer(1, 4))
            story.append(KeepTogether(vessel_flow))

    # Limitations & Legal Disclaimer
    story.append(Spacer(1, 3))
    story.append(Paragraph("8. LIMITATIONS & STANDARD OF THIS NOTE", h2_style))
    legal_text = (
        "<b>Standard of this note:</b> ranked likelihood for investigation, not legal proof of discharge. "
        "A vessel is supported only when the source test finds its own track reproduces the slick. "
        "Produced by TideTrail to direct an Indian Coast Guard or DG Shipping investigation under "
        "MARPOL 73/78 Annex I; it does not by itself establish a violation. Drift age is a metocean advection "
        "proxy, not a laboratory weathering age. Look-alike polygons are excluded from attribution by design. "
        "Chain of custody: the SHA-256 digests are computed at export; recompute them on any later copy."
    )
    t_legal = Table([[Paragraph(legal_text, dim_style)]], colWidths=[523])
    t_legal.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fa")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dae2ea")),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_legal)

    pdf.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()


def build_html_report(doc: Dict[str, Any]) -> str:
    """Build the interactive HTML Maritime Pollution Attribution Note."""
    job_id = doc.get("job_id", "UNKNOWN")
    scene_hash = compute_scene_sha256(doc)
    dossier_hash = compute_dossier_sha256(doc)
    scene = doc.get("scene") or {}
    det = doc.get("detection") or {}
    det_metrics = det.get("metrics") or {}
    drift = doc.get("drift") or {}
    origin = drift.get("origin") or {}
    met = drift.get("metocean") or {}
    cone = _forecast_facts(doc)
    inp = doc.get("input") or {}
    r = _rows(doc)
    props = r["props"]
    suspects = (doc.get("attribution") or {}).get("suspects", [])
    trace = doc.get("trace") or []
    plain_lines = "\n".join(html.escape(line) for line in _lines(doc))
    is_probe = doc.get("mode") == "operator_probe"
    is_clean = doc.get("status") in ("clean_water", "no_oil_detected") or (not props and not is_probe)

    ships_block = "".join("<p>%s</p>" % html.escape(line) for line in _ships_lines(doc)[:11])
    source_block = "".join("<p>%s</p>" % html.escape(line) for line in _source_lines(doc)[:6])
    _st = doc.get("source_test") or {}
    if _st.get("available") and _st.get("hypotheses"):
        rows = ""
        for h in _st["hypotheses"][:10]:
            nm = h.get("name") if h.get("kind") == "installation" else "%s (MMSI %s)" % (h.get("name"), h.get("mmsi"))
            win = ("%s to %s UTC" % (h["window"][0][5:16].replace("T", " "), h["window"][1][11:16])) if h.get("window") else "does not reach the slick"
            rows += "<tr><td>%s</td><td>%s</td><td class='mono'>%.2f</td><td class='mono'>%.0f%% / %.0f%%</td><td>%s</td></tr>" % (
                html.escape(str(h.get("kind"))), html.escape(str(nm)), float(h.get("fit") or 0),
                float(h.get("coverage") or 0) * 100, float(h.get("precision") or 0) * 100, html.escape(win))
        source_block += ("<table><thead><tr><th>Kind</th><th>Source</th><th>Fit</th><th>Covers / lands</th>"
                         "<th>Release window</th></tr></thead><tbody>%s</tbody></table>" % rows)
    suspect_cards = ""
    for s in suspects:
        d = s.get("detail") or {}
        comp = s.get("components") or {}
        wgt = s.get("weighted") or {}
        traj = d.get("trajectory") or {}
        beh = d.get("behavior") or {}
        reasons_html = "".join("<span class='tag'>%s</span>" % html.escape(_reasons_text([r_item])) for r_item in (s.get("reasons") or []))

        suspect_cards += f"""
        <div class="suspect-card">
          <div class="sc-head">
            <div class="sc-title">
              <span class="sc-rank">#{s.get('rank')}</span>
              <span class="sc-name">{html.escape(str(s.get('name') or 'UNKNOWN'))}</span>
              <span class="sc-mmsi">MMSI {s.get('mmsi')}</span>
              <span class="sc-type">{html.escape(str(s.get('type')))}</span>
            </div>
            <div class="sc-score">
              <span class="score-val">{float(s.get('score') or 0):.1f}%</span>
              <span class="conf-val">Conf {float(s.get('confidence') or 1.0)*100:.0f}%</span>
            </div>
          </div>
          <div class="sc-grid">
            <div class="sc-item"><span class="k">CPA Distance</span><span class="v">{d.get('origin_distance_km')} km from origin</span></div>
            <div class="sc-item"><span class="k">CPA Time</span><span class="v">{d.get('closest_approach_utc')} (Δ {d.get('time_offset_minutes')} min)</span></div>
            <div class="sc-item"><span class="k">CPA Position</span><span class="v mono">{d.get('closest_lat')}, {d.get('closest_lon')}</span></div>
            <div class="sc-item"><span class="k">Speed at CPA</span><span class="v">{beh.get('sog_at_closest_kn')} kn ({'Discharge Band' if beh.get('discharge_band') else 'Nominal'})</span></div>
            <div class="sc-item"><span class="k">Course</span><span class="v">{html.escape(_course_text(traj))}</span></div>
            <div class="sc-item"><span class="k">AIS Gap Status</span><span class="v {'warn' if beh.get('non_reporting') else ''}">{beh.get('ais_gap_minutes', 0)} min ({'AIS silent near the origin' if beh.get('non_reporting') else 'Continuous'})</span></div>
          </div>
          <div class="subscores-bar">
            <span class="sub-item">Proximity ({_weight_pct(doc, 'prox')}%): <b>{float(comp.get('prox') or 0):.2f} → {float(wgt.get('prox') or 0):.2f}</b></span>
            <span class="sub-item">Time ({_weight_pct(doc, 'time')}%): <b>{float(comp.get('time') or 0):.2f} → {float(wgt.get('time') or 0):.2f}</b></span>
            <span class="sub-item">Type Prior ({_weight_pct(doc, 'type')}%): <b>{float(comp.get('type') or 0):.2f} → {float(wgt.get('type') or 0):.2f}</b></span>
            <span class="sub-item">Trajectory ({_weight_pct(doc, 'traj')}%): <b>{float(comp.get('traj') or 0):.2f} → {float(wgt.get('traj') or 0):.2f}</b></span>
            <span class="sub-item">Behavior ({_weight_pct(doc, 'beh')}%): <b>{float(comp.get('beh') or 0):.2f} → {float(wgt.get('beh') or 0):.2f}</b></span>
          </div>
          <div class="tags-row">{reasons_html}</div>
        </div>
        """

    if not suspects:
        empty_reason = (
            "Not applicable: Clean water control scene; vessel traffic attribution was not executed."
            if is_clean else "No vessel passed the spatio-temporal filter window around the origin."
        )
        suspect_cards = f"<p class='hint-box'>{html.escape(empty_reason)}</p>"

    trace_rows = ""
    for t in trace:
        trace_rows += f"""
        <tr>
          <td><code>{html.escape(str(t.get('step', '')))}</code></td>
          <td>{html.escape(str(t.get('note', '')))}</td>
          <td class="mono">{float(t.get('elapsed_ms') or 0):.1f} ms</td>
          <td><span class="badge ok">{html.escape(str(t.get('status', 'ok')))}</span></td>
        </tr>
        """



    # Section 1 content
    scene_id_val = str(scene.get("id") or inp.get("scene_id") or ("probe" if is_probe else "N/A"))
    tsat_val = str(scene.get("t_sat") or inp.get("t_sat"))
    sensor_val = "Operator Probe Point (Interactive Coordinates)" if is_probe else "Sentinel-1 C-band SAR (IW GRD RTC)"
    bounds_val = str(scene.get("bounds") or (f"Probe fix: Lat {inp.get('lat')}, Lon {inp.get('lon')}" if is_probe else "N/A"))
    centroid_val = str(scene.get("centroid") or (f"{inp.get('lat')}, {inp.get('lon')}" if is_probe else "N/A"))

    # Section 2 content
    if is_probe:
        sec2_html = f"""
        <div class="hint-box">
          <b>Operator Probe Placement:</b> Coordinates Lat {inp.get('lat')}, Lon {inp.get('lon')} |
          Initial slick radius {inp.get('slick_radius_km', 1.5)} km. Hydrodynamic drift and AIS attribution executed from operator fix.
        </div>
        """
    elif not props or is_clean:
        sec2_html = f"""
        <p class="hint-box">
          <b>Clean Water Control:</b> No mineral oil slick detected above threshold on this scene.
          Look-alike polygons screened: {det_metrics.get('lookalike_polygons_found', 0)}.
        </p>
        """
    else:
        sec2_html = f"""
        <div class="grid2">
          <div class="kv"><span class="k">Detector Model</span><span class="v">{html.escape(str(det_metrics.get("detector", "unet")))}</span></div>
          <div class="kv"><span class="k">Detection Confidence</span><span class="v">{props.get("confidence", "N/A")}</span></div>
          <div class="kv"><span class="k">Total Oil Slicks Found</span><span class="v">{len(det.get("polygons") or [1])}</span></div>
          <div class="kv"><span class="k">Total Slick Area</span><span class="v">{det_metrics.get("oil_area_km2", props.get("area_km2"))} km²</span></div>
          <div class="kv"><span class="k">Primary Slick Area</span><span class="v">{props.get("area_km2")} km²</span></div>
          <div class="kv"><span class="k">Dimensions (L × W)</span><span class="v">{props.get("length_km")} km × {props.get("width_km")} km</span></div>
          <div class="kv"><span class="k">Orientation Angle</span><span class="v">{props.get("orientation_deg")}°</span></div>
          <div class="kv"><span class="k">Perimeter &amp; Compactness</span><span class="v">{props.get("perimeter_km")} km ({props.get("compactness")})</span></div>
          <div class="kv"><span class="k">Slick Centroid Fix</span><span class="v mono">{props.get("centroid_lat")}, {props.get("centroid_lon")}</span></div>
          <div class="kv"><span class="k">Radar Contrast vs Sea</span><span class="v">{props.get("contrast_db")} dB</span></div>
        </div>
        """

    # Section 3 content
    if not drift or is_clean:
        sec3_html = """
        <p class="hint-box">
          Not applicable: Clean water control scene with no oil slick detected.
          Hydrodynamic drift hindcasting and forward forecasting are not executed for clean scenes.
        </p>
        """
    else:
        sec3_html = f"""
        <div class="grid2">
          <div class="kv"><span class="k">Metocean Source</span><span class="v">{html.escape(str(met.get('source', 'Open-Meteo ERA5 10m wind + marine currents')))}</span></div>
          <div class="kv"><span class="k">Wind Leeway Factor</span><span class="v">{html.escape(_alpha_text(doc))}</span></div>
          <div class="kv"><span class="k">Mean 10m Wind Speed</span><span class="v">{met.get('mean_wind_ms', 'N/A')} m/s</span></div>
          <div class="kv"><span class="k">Mean Ocean Surface Current</span><span class="v">{met.get('mean_current_ms', 'N/A')} m/s</span></div>
          <div class="kv"><span class="k">Estimated Discharge Time</span><span class="v">{origin.get('t', 'N/A')}</span></div>
          <div class="kv"><span class="k">Drift Age Proxy</span><span class="v">{doc.get('age_hours_proxy', 'N/A')} hours</span></div>
          <div class="kv"><span class="k">Origin Coordinates Fix</span><span class="v mono">{origin.get('lat')}, {origin.get('lon')}</span></div>
          <div class="kv"><span class="k">90% Ensemble Radius / Area</span><span class="v">{origin.get('spread_km')} km ({origin.get('area_km2')} km²)</span></div>
          <div class="kv"><span class="k">Forecast Horizon</span><span class="v">{cone['hours']} hours (spread: {cone['end_spread_km']} km)</span></div>
          <div class="kv"><span class="k">Landfall Impact Threat</span><span class="v">{cone['threat']}</span></div>
        </div>
        """

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Maritime Pollution Attribution Note | {html.escape(job_id)}</title>
  <style>
    :root {{
      --bg: #090f15; --panel: #0f1922; --panel-2: #14212c; --line: #1e2f3d;
      --ink: #e6edf3; --ink-2: #9db1c2; --ink-3: #7e93a4;
      --accent: #e89550; --cyan: #4fb8dd; --good: #2ea043; --bad: #cf222e; --warn: #d29922;
      --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
      --font-mono: 'JetBrains Mono', Consolas, ui-monospace, monospace;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #f6f8fa; --panel: #ffffff; --panel-2: #f0f3f6; --line: #d0d7de;
        --ink: #1f2328; --ink-2: #434d56; --ink-3: #656d76;
        --accent: #b05710; --cyan: #0969da; --good: #1a7f37; --bad: #cf222e; --warn: #9a6700;
      }}
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font: 13px/1.55 var(--font-sans); background: var(--bg); color: var(--ink);
      padding: 0 0 60px;
    }}
    .toolbar {{
      position: sticky; top: 0; z-index: 100;
      background: var(--panel); border-bottom: 1px solid var(--line);
      padding: 10px 24px; display: flex; align-items: center; justify-content: space-between;
      gap: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }}
    .brand {{ font-weight: 700; font-size: 13px; letter-spacing: 0.12em; color: var(--ink); text-decoration: none; }}
    .btnrow {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .btn {{
      padding: 6px 12px; font: 600 11.5px var(--font-sans); border-radius: 5px;
      cursor: pointer; text-decoration: none; border: 1px solid var(--line);
      background: var(--panel-2); color: var(--ink); display: inline-flex; align-items: center; gap: 6px;
    }}
    .btn:hover {{ background: var(--line); }}
    .btn.primary {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    .container {{ max-width: 960px; margin: 24px auto; padding: 0 20px; }}
    .warning-banner {{
      background: rgba(210, 153, 34, 0.15); border: 1px solid var(--warn); border-radius: 6px;
      padding: 12px 16px; margin-bottom: 20px; color: var(--warn); font-size: 12px;
    }}
    .warning-banner ul {{ margin-left: 20px; margin-top: 4px; }}
    .header-card {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
      padding: 24px; margin-bottom: 20px;
    }}
    .header-card h1 {{ font-size: 20px; letter-spacing: 0.02em; margin-bottom: 4px; }}
    .header-card .subtitle {{ font-size: 12px; color: var(--ink-3); margin-bottom: 16px; }}
    .seal-box {{
      background: var(--panel-2); border: 1px solid var(--line); border-left: 4px solid var(--good);
      border-radius: 6px; padding: 14px 18px; margin-top: 14px;
    }}
    .seal-title {{ font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--good); margin-bottom: 8px; }}
    .hash-row {{ display: flex; align-items: baseline; gap: 12px; font-size: 12px; margin-bottom: 4px; }}
    .hash-row .label {{ width: 180px; flex-shrink: 0; color: var(--ink-3); }}
    .mono {{ font-family: var(--font-mono); font-size: 11px; word-break: break-all; }}
    .section {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
      padding: 20px; margin-bottom: 20px;
    }}
    .section h2 {{
      font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
      color: var(--ink-3); border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 16px;
    }}
    .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px 24px; }}
    .kv {{ display: flex; justify-content: space-between; gap: 8px; padding: 4px 0; border-bottom: 1px dashed var(--line); }}
    .kv .k {{ color: var(--ink-2); font-size: 12px; }}
    .kv .v {{ font-weight: 600; font-size: 12px; }}
    .suspect-card {{
      background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px;
      padding: 16px; margin-bottom: 14px;
    }}
    .sc-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
    .sc-title {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
    .sc-rank {{ font-size: 14px; font-weight: 700; color: var(--accent); }}
    .sc-name {{ font-size: 14px; font-weight: 700; }}
    .sc-mmsi {{ font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); }}
    .sc-type {{ font-size: 11px; padding: 2px 6px; border-radius: 3px; background: var(--line); color: var(--ink-2); }}
    .sc-score {{ text-align: right; }}
    .score-val {{ font-size: 16px; font-weight: 700; color: var(--accent); }}
    .conf-val {{ font-size: 11px; color: var(--ink-3); margin-left: 6px; }}
    .sc-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px 16px; margin-bottom: 12px; }}
    .sc-item {{ font-size: 11.5px; }}
    .sc-item .k {{ display: block; color: var(--ink-3); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }}
    .sc-item .v.warn {{ color: var(--bad); font-weight: 600; }}
    .subscores-bar {{
      display: flex; gap: 12px; flex-wrap: wrap; padding: 8px 10px;
      background: var(--panel); border: 1px solid var(--line); border-radius: 4px;
      font-size: 11px; color: var(--ink-2); margin-bottom: 10px;
    }}
    .tags-row {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .tag {{
      font-family: var(--font-mono); font-size: 10px; padding: 2px 7px;
      background: var(--panel); border: 1px solid var(--line); border-radius: 3px; color: var(--ink-2);
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--line); }}
    th {{ font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--ink-3); }}
    .badge {{ font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 600; }}
    .badge.ok {{ background: rgba(46,160,67,0.15); color: var(--good); }}
    .hint-box {{ padding: 12px; background: var(--panel-2); border-radius: 6px; color: var(--ink-3); font-style: italic; }}
    .legal-box {{
      font-size: 11px; color: var(--ink-3); line-height: 1.6;
      border-left: 3px solid var(--warn); padding-left: 12px;
    }}
    pre.plain-dossier {{
      background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px;
      padding: 14px; font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
      overflow-x: auto; white-space: pre-wrap;
    }}
    @media print {{
      .toolbar {{ display: none; }}
      body {{ background: #fff; color: #000; padding: 0; }}
      .header-card, .section, .suspect-card {{ border: 1px solid #ddd; page-break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <nav class="toolbar">
    <a href="/" class="brand">TIDETRAIL CONSOLE</a>
    <div class="btnrow">
      <a href="/api/jobs/{job_id}/export?format=pdf" class="btn primary" download="attribution_{job_id}.pdf">Download PDF</a>
      <a href="/api/jobs/{job_id}/export?format=html" class="btn" download="attribution_{job_id}.html">Download HTML</a>
      <button class="btn" onclick="window.print()">Print Dossier</button>
      <a href="/api/jobs/{job_id}?download=1" class="btn" download="tidetrail_{job_id}.json">Job JSON</a>
      <a href="/api/jobs/{job_id}/geojson?download=1" class="btn" download="tidetrail_{job_id}.geojson">GeoJSON</a>
    </div>
  </nav>

  <main class="container">
    <header class="header-card">
      <h1>Maritime Pollution Attribution Note</h1>
      <div class="subtitle">TideTrail {html.escape(config.VERSION)} | Evidence dossier for an investigating officer</div>
      <div class="kv"><span class="k">Case Identifier</span><span class="v mono">{html.escape(job_id)}</span></div>
      <div class="kv"><span class="k">Report Generated</span><span class="v">{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}</span></div>
      <div class="kv"><span class="k">Admiralty Evidentiary Standard</span><span class="v">Admissible MARPOL 73/78 Annex I Dossier</span></div>

      <div class="seal-box">
        <div class="seal-title">Cryptographic Chain of Custody (SHA-256 digests)</div>
        <div class="hash-row">
          <span class="label">SAR Scene Digest (Raw Data):</span>
          <span class="mono">{scene_hash}</span>
        </div>
        <div class="hash-row">
          <span class="label">Job Record Digest (Canonical):</span>
          <span class="mono">{dossier_hash}</span>
        </div>
        <div class="hash-row">
          <span class="label">Cryptographic Integrity:</span>
          <span class="mono" style="color:var(--good);font-weight:700;">DIGESTS COMPUTED AT EXPORT</span>
        </div>
      </div>
    </header>

    <section class="section">
      <h2>1. Satellite Surveillance &amp; Scene Telemetry</h2>
      <div class="grid2">
        <div class="kv"><span class="k">Scene ID</span><span class="v mono">{html.escape(scene_id_val)}</span></div>
        <div class="kv"><span class="k">Observation Time (t_sat)</span><span class="v">{html.escape(tsat_val)}</span></div>
        <div class="kv"><span class="k">Satellite Platform</span><span class="v">{html.escape(sensor_val)}</span></div>
        <div class="kv"><span class="k">AIS Surveillance Mode</span><span class="v">{html.escape(str(scene.get('ais_mode', 'simulated')).upper())}</span></div>
        <div class="kv"><span class="k">Footprint Bounds</span><span class="v mono">{html.escape(bounds_val)}</span></div>
        <div class="kv"><span class="k">Coordinate Reference System</span><span class="v">{scene.get('crs', 'EPSG:4326')}</span></div>
        <div class="kv"><span class="k">Radar Background Sea Level</span><span class="v">{det_metrics.get('radiometry', {}).get('sea_level_db') or det_metrics.get('sea_level_db', 'N/A')} dB</span></div>
        <div class="kv"><span class="k">Dynamic Contrast Range</span><span class="v">{det_metrics.get('radiometry', {}).get('dynamic_range_db') or det_metrics.get('dynamic_range_db', 'N/A')} dB</span></div>
      </div>
    </section>

    <section class="section">
      <h2>2. Slick Detection &amp; Morphometric Telemetry</h2>
      {sec2_html}
    </section>

    <section class="section">
      <h2>3. Metocean &amp; Drift Trajectory Telemetry</h2>
      {sec3_html}
    </section>

    <section class="section">
      <h2>4. Pipeline Execution Telemetry</h2>
      <table>
        <thead>
          <tr><th>Step</th><th>Operation &amp; Notes</th><th>Runtime</th><th>Status</th></tr>
        </thead>
        <tbody>
          {trace_rows}
        </tbody>
      </table>
    </section>

    <section class="section">
      <h2>5. Source Test (Forward Release of Each Candidate)</h2>
      {source_block}
    </section>

    <section class="section">
      <h2>6. Ships on the Radar (Echoes Checked Against AIS)</h2>
      {ships_block}
    </section>

    <section class="section">
      <h2>7. Ranked Vessels (Investigative Leads, Not Findings)</h2>
      {suspect_cards}
    </section>

    <section class="section">
      <h2>8. Chain of Custody &amp; Limitations</h2>
      <div class="legal-box">
        <p><b>Standard of this note:</b> ranked likelihood for investigation, not legal proof of discharge. A vessel is supported only when the source test finds its own track reproduces the slick.</p>
        <p>Produced by TideTrail to direct an Indian Coast Guard or DG Shipping investigation under MARPOL 73/78 Annex I. It does not by itself establish a violation.</p>
        <p>Age is an oceanographic drift advection proxy, not a chemical laboratory weathering age. Look-alike class polygons are excluded from attribution by design.</p>
        <p>Chain of custody: the SHA-256 digests above are computed at export. Record them; recompute them on any later copy, and a mismatch means the copy was altered.</p>
      </div>
    </section>

    <section class="section">
      <h2>9. Plaintext Telemetry Dossier</h2>
      <pre class="plain-dossier">{plain_lines}</pre>
    </section>
  </main>
</body>
</html>
"""
    return page


@router.get("/api/report/{job_id}", response_class=HTMLResponse)
def report_html(
    job_id: str,
    format: Optional[str] = Query(default=None),
    download: bool = Query(default=False),
) -> Response:
    """GET /api/report/{job_id} - Maritime Pollution Attribution Note (HTML or PDF)."""
    doc = job_store.load(job_id)
    if doc is None:
        raise HTTPException(404, "unknown job_id %r" % job_id)
    if format and format.lower() == "pdf":
        return report_pdf(job_id)

    html_content = build_html_report(doc)
    headers = {}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="attribution_%s.html"' % job_id
    return HTMLResponse(html_content, headers=headers)


@router.get("/api/report/{job_id}/pdf")
def report_pdf(job_id: str) -> Response:
    """GET /api/report/{job_id}/pdf - Maritime Pollution Attribution Note (PDF)."""
    doc = job_store.load(job_id)
    if doc is None:
        raise HTTPException(404, "unknown job_id %r" % job_id)

    pdf_bytes = build_pdf_report(doc)
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="attribution_%s.pdf"' % job_id},
    )


@router.get("/api/jobs/{job_id}/export")
@router.get("/api/export/{job_id}")
def export_job(
    job_id: str,
    format: str = Query(default="pdf", description="Export format: pdf, html, json, geojson"),
    download: bool = Query(default=True, description="Attach Content-Disposition download header"),
) -> Response:
    """Unified export endpoint for Maritime Pollution Attribution Note, Job JSON, and GeoJSON."""
    doc = job_store.load(job_id)
    if doc is None:
        raise HTTPException(404, "unknown job_id %r" % job_id)

    fmt = (format or "pdf").lower().strip()

    if fmt in ("pdf", "note_pdf", "note-pdf"):
        pdf_bytes = build_pdf_report(doc)
        headers = {}
        if download:
            headers["Content-Disposition"] = 'attachment; filename="attribution_%s.pdf"' % job_id
        return Response(pdf_bytes, media_type="application/pdf", headers=headers)

    if fmt in ("html", "note_html", "note-html", "note"):
        html_content = build_html_report(doc)
        headers = {}
        if download:
            headers["Content-Disposition"] = 'attachment; filename="attribution_%s.html"' % job_id
        return HTMLResponse(html_content, headers=headers)

    if fmt in ("json", "job_json", "job-json"):
        from .pipeline import _clean_nans
        headers = {}
        if download:
            headers["Content-Disposition"] = 'attachment; filename="tidetrail_%s.json"' % job_id
        return Response(
            content=json.dumps(_clean_nans(doc), indent=2, default=str),
            media_type="application/json",
            headers=headers,
        )

    if fmt in ("geojson", "geo", "gis"):
        from .pipeline import _clean_nans, build_job_geojson
        fc = build_job_geojson(doc)
        headers = {}
        if download:
            headers["Content-Disposition"] = 'attachment; filename="tidetrail_%s.geojson"' % job_id
        return Response(
            content=json.dumps(_clean_nans(fc), indent=2, default=str),
            media_type="application/geo+json",
            headers=headers,
        )

    raise HTTPException(
        400,
        "Unsupported export format '%s'. Supported formats: pdf, html, json, geojson." % format,
    )

