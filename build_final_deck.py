"""Generate the official 6-slide SIH 2026 presentation for TideTrace (PS 26143).

Strictly compliant with SIH2026-IDEA-Presentation-Format.pdf and SIH Winners Playbook:
- Exactly 6 slides (Slide 1 Title to Slide 6 Research & References)
- Clean, modern, light-themed maritime intelligence aesthetic (pure white, deep navy, ocean blue, marine teal)
- No dark theme. No AI-slop gradient boxes.
- Crisp vector flowcharts, authentic prototype screenshots with un-stretched aspect ratios.
- Strict inclusion of official SIH watermark/footer markers:
  '@SIH Idea submission- Template' | 'Your Team Name' | 'Slide X of 6'
- Complete coverage of all mandated clauses: SAR detection & morphology (Clause a),
  Lagrangian drift physics & origin inversion (Clause b), AIS forensic attribution & dark vessels (Clause c).
"""
from pathlib import Path
from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

# Initialize Presentation in 16:9 widescreen
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
blank_layout = prs.slide_layouts[6]

# Color Palette (Crisp Light Maritime Intelligence Theme)
C_BG = RGBColor(255, 255, 255)            # Pure White
C_CARD_BG = RGBColor(248, 250, 252)       # Soft Slate #F8FAFC
C_CARD_BORDER = RGBColor(203, 213, 225)   # Border #CBD5E1
C_NAVY = RGBColor(10, 37, 64)             # Deep Navy #0A2540
C_NAVY_LIGHT = RGBColor(15, 41, 66)       # Navy Light #0F2942
C_BLUE = RGBColor(2, 132, 199)            # Ocean Blue #0284C7
C_BLUE_LIGHT = RGBColor(224, 242, 254)    # Ice Blue #E0F2FE
C_TEAL = RGBColor(13, 148, 136)           # Marine Teal #0D9488
C_TEAL_LIGHT = RGBColor(240, 253, 244)    # Soft Mint #F0FDF4
C_AMBER = RGBColor(217, 119, 6)           # Amber #D97706
C_AMBER_LIGHT = RGBColor(254, 243, 199)   # Soft Amber #FEF3C7
C_TEXT_MAIN = RGBColor(30, 41, 59)        # Slate 800 #1E293B
C_TEXT_MUTED = RGBColor(100, 116, 139)    # Slate 500 #64748B
C_WHITE = RGBColor(255, 255, 255)

FONT_HEAD = "Trebuchet MS"
FONT_BODY = "Calibri"

def set_slide_background(slide):
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = C_BG

def add_header(slide, badge_text, title_text, subtitle_text, slide_num):
    # Top Badge
    badge = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6), Inches(0.32), Inches(3.6), Inches(0.30))
    badge.fill.solid()
    badge.fill.fore_color.rgb = C_BLUE_LIGHT
    badge.line.color.rgb = C_BLUE
    badge.line.width = Pt(1.0)
    badge.adjustments[0] = 0.5
    tf_b = badge.text_frame
    tf_b.word_wrap = True
    tf_b.margin_left = tf_b.margin_right = tf_b.margin_top = tf_b.margin_bottom = 0
    p_b = tf_b.paragraphs[0]
    p_b.text = badge_text.upper()
    p_b.font.name = FONT_HEAD
    p_b.font.size = Pt(9.0)
    p_b.font.bold = True
    p_b.font.color.rgb = C_BLUE
    p_b.alignment = PP_ALIGN.CENTER

    # Slide Number & Tracker
    tracker = slide.shapes.add_textbox(Inches(9.5), Inches(0.32), Inches(3.23), Inches(0.30))
    tf_tr = tracker.text_frame
    tf_tr.margin_left = tf_tr.margin_right = tf_tr.margin_top = tf_tr.margin_bottom = 0
    p_tr = tf_tr.paragraphs[0]
    p_tr.text = f"Smart India Hackathon 2026 · Slide {slide_num} of 6"
    p_tr.font.name = FONT_BODY
    p_tr.font.size = Pt(9.5)
    p_tr.font.bold = True
    p_tr.font.color.rgb = C_TEXT_MUTED
    p_tr.alignment = PP_ALIGN.RIGHT

    # Slide Main Title
    tb_t = slide.shapes.add_textbox(Inches(0.6), Inches(0.68), Inches(12.13), Inches(0.48))
    tf_t = tb_t.text_frame
    tf_t.word_wrap = True
    tf_t.margin_left = tf_t.margin_right = tf_t.margin_top = tf_t.margin_bottom = 0
    p_t = tf_t.paragraphs[0]
    p_t.text = title_text
    p_t.font.name = FONT_HEAD
    p_t.font.size = Pt(21)
    p_t.font.bold = True
    p_t.font.color.rgb = C_NAVY

    # Subtitle
    tb_s = slide.shapes.add_textbox(Inches(0.6), Inches(1.18), Inches(12.13), Inches(0.32))
    tf_s = tb_s.text_frame
    tf_s.word_wrap = True
    tf_s.margin_left = tf_s.margin_right = tf_s.margin_top = tf_s.margin_bottom = 0
    p_s = tf_s.paragraphs[0]
    p_s.text = subtitle_text
    p_s.font.name = FONT_BODY
    p_s.font.size = Pt(10.5)
    p_s.font.color.rgb = C_TEXT_MUTED

def add_footer(slide, slide_num):
    """Add mandatory official SIH watermark template footer."""
    tb_f = slide.shapes.add_textbox(Inches(0.6), Inches(7.18), Inches(12.13), Inches(0.25))
    tf_f = tb_f.text_frame
    tf_f.word_wrap = True
    tf_f.margin_left = tf_f.margin_right = tf_f.margin_top = tf_f.margin_bottom = 0
    p = tf_f.paragraphs[0]
    
    r1 = p.add_run()
    r1.text = "@SIH Idea submission- Template          "
    r1.font.name = FONT_BODY
    r1.font.size = Pt(8.5)
    r1.font.bold = True
    r1.font.color.rgb = C_TEXT_MUTED
    
    r2 = p.add_run()
    r2.text = "TideTrace · Team registered on SIH Portal          "
    r2.font.name = FONT_BODY
    r2.font.size = Pt(8.5)
    r2.font.color.rgb = C_TEXT_MUTED

    r3 = p.add_run()
    r3.text = f"Slide {slide_num} of 6"
    r3.font.name = FONT_BODY
    r3.font.size = Pt(8.5)
    r3.font.bold = True
    r3.font.color.rgb = C_BLUE

def add_card(slide, left, top, width, height, title=None, bg_color=C_CARD_BG, border_color=C_CARD_BORDER, border_width=Pt(1.2)):
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    card.fill.solid()
    card.fill.fore_color.rgb = bg_color
    card.line.color.rgb = border_color
    card.line.width = border_width
    card.adjustments[0] = 0.04
    if title:
        tb = slide.shapes.add_textbox(Inches(left + 0.15), Inches(top + 0.10), Inches(width - 0.3), Inches(0.32))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEAD
        p.font.size = Pt(11.5)
        p.font.bold = True
        p.font.color.rgb = C_NAVY
    return card

def add_picture_fit(slide, img_path, left, top, max_width, max_height):
    """Add picture preserving original aspect ratio, centered inside target bounding box."""
    if not Path(img_path).exists():
        return None
    with Image.open(img_path) as im:
        iw, ih = im.size
    aspect = iw / ih
    box_aspect = max_width / max_height

    if aspect > box_aspect:
        w = max_width
        h = max_width / aspect
        x = left
        y = top + (max_height - h) / 2.0
    else:
        h = max_height
        w = max_height * aspect
        y = top
        x = left + (max_width - w) / 2.0

    return slide.shapes.add_picture(str(Path(img_path).resolve()), Inches(x), Inches(y), width=Inches(w), height=Inches(h))

# ==============================================================================
# SLIDE 1: TITLE PAGE
# ==============================================================================
slide1 = prs.slides.add_slide(blank_layout)
set_slide_background(slide1)
add_footer(slide1, 1)

add_header(slide1, "SMART INDIA HACKATHON 2026 · TITLE PAGE", 
           "TideTrace: Satellite SAR Oil Spill Detection & Forensic AIS Attribution",
           "An automated intelligence pipeline for satellite slick segmentation, 2D Lagrangian drift hindcasting, and vessel correlation.",
           1)

# Mandatory SIH Format Meta Card
add_card(slide1, 0.6, 1.55, 12.13, 1.50, bg_color=C_CARD_BG, border_color=C_BLUE, border_width=Pt(1.5))

# Left Sub-Column of Meta Box
tb_m1 = slide1.shapes.add_textbox(Inches(0.8), Inches(1.65), Inches(6.8), Inches(1.3))
tf_m1 = tb_m1.text_frame
tf_m1.word_wrap = True
tf_m1.margin_left = tf_m1.margin_right = tf_m1.margin_top = tf_m1.margin_bottom = 0

p1 = tf_m1.paragraphs[0]
r = p1.add_run()
r.text = "• Problem Statement ID: "
r.font.bold = True
r.font.size = Pt(10)
r.font.color.rgb = C_BLUE
r2 = p1.add_run()
r2.text = "26143"
r2.font.bold = True
r2.font.size = Pt(10.5)
r2.font.color.rgb = C_NAVY

p2 = tf_m1.add_paragraph()
r = p2.add_run()
r.text = "• Problem Statement Title: "
r.font.bold = True
r.font.size = Pt(10)
r.font.color.rgb = C_BLUE
r2 = p2.add_run()
r2.text = "Leveraging satellite imagery to determine Oil spills at sea along with AIS data correlations to identify vessel responsible for the spill."
r2.font.size = Pt(9.5)
r2.font.color.rgb = C_TEXT_MAIN

p3 = tf_m1.add_paragraph()
r = p3.add_run()
r.text = "• Organization & Dept: "
r.font.bold = True
r.font.size = Pt(10)
r.font.color.rgb = C_BLUE
r2 = p3.add_run()
r2.text = "National Technical Research Organisation (NTRO)"
r2.font.size = Pt(9.5)
r2.font.color.rgb = C_TEXT_MAIN

# Right Sub-Column of Meta Box
tb_m2 = slide1.shapes.add_textbox(Inches(7.8), Inches(1.65), Inches(4.7), Inches(1.3))
tf_m2 = tb_m2.text_frame
tf_m2.word_wrap = True
tf_m2.margin_left = tf_m2.margin_right = tf_m2.margin_top = tf_m2.margin_bottom = 0

p4 = tf_m2.paragraphs[0]
r = p4.add_run()
r.text = "• Theme: "
r.font.bold = True
r.font.size = Pt(10)
r.font.color.rgb = C_BLUE
r2 = p4.add_run()
r2.text = "Disaster Management / Space Technology"
r2.font.size = Pt(9.5)
r2.font.color.rgb = C_TEXT_MAIN

p5 = tf_m2.add_paragraph()
r = p5.add_run()
r.text = "• PS Category: "
r.font.bold = True
r.font.size = Pt(10)
r.font.color.rgb = C_BLUE
r2 = p5.add_run()
r2.text = "Software"
r2.font.bold = True
r2.font.size = Pt(9.5)
r2.font.color.rgb = C_NAVY

p6 = tf_m2.add_paragraph()
r = p6.add_run()
r.text = "• Team ID & Name: "
r.font.bold = True
r.font.size = Pt(10)
r.font.color.rgb = C_BLUE
r2 = p6.add_run()
r2.text = "[Your Team ID]  |  [Your Team Name] (Registered on portal)"
r2.font.size = Pt(9.5)
r2.font.color.rgb = C_TEXT_MAIN

# 3 Core Mandated Capability Pillars (Clauses a, b, c)
# Card 1: Clause a
add_card(slide1, 0.6, 3.15, 3.85, 2.75, "Clause (a): SAR Detection & Morphology", bg_color=C_CARD_BG, border_color=C_BLUE)
tb_c1 = slide1.shapes.add_textbox(Inches(0.75), Inches(3.55), Inches(3.55), Inches(2.25))
tf_c1 = tb_c1.text_frame
tf_c1.word_wrap = True
tf_c1.margin_left = tf_c1.margin_right = tf_c1.margin_top = tf_c1.margin_bottom = 0
items_c1 = [
    ("Sentinel-1 C-Band Dual-Pol: ", "Ingests 2048x2048 VV/VH backscatter GeoTIFFs, standardizing capillary wave damping."),
    ("Deep Learning UNet++: ", "Trained on Zenodo benchmark (1,164 tiles) with timm-efficientnet-b0; achieves 0.8891 IoU."),
    ("Look-Alike & Water Gate: ", "Multi-class discrimination rejects biogenic films. Strict <3.0 dB floor flags clean water with 0 false slicks."),
    ("Geodetic Telemetry: ", "Local AEQD projection calculates metric area (km²), perimeter, max Feret length, and PCA orientation.")
]
for bold_prefix, text in items_c1:
    p = tf_c1.add_paragraph() if tf_c1.paragraphs[0].text else tf_c1.paragraphs[0]
    p.space_after = Pt(3.5)
    run_b = p.add_run()
    run_b.text = "• " + bold_prefix
    run_b.font.name = FONT_BODY
    run_b.font.bold = True
    run_b.font.size = Pt(9.2)
    run_b.font.color.rgb = C_NAVY
    run_t = p.add_run()
    run_t.text = text
    run_t.font.name = FONT_BODY
    run_t.font.size = Pt(8.8)
    run_t.font.color.rgb = C_TEXT_MAIN

# Card 2: Clause b
add_card(slide1, 4.74, 3.15, 3.85, 2.75, "Clause (b): Lagrangian Drift Physics", bg_color=C_CARD_BG, border_color=C_TEAL)
tb_c2 = slide1.shapes.add_textbox(Inches(4.89), Inches(3.55), Inches(3.55), Inches(2.25))
tf_c2 = tb_c2.text_frame
tf_c2.word_wrap = True
tf_c2.margin_left = tf_c2.margin_right = tf_c2.margin_top = tf_c2.margin_bottom = 0
items_c2 = [
    ("Metocean Vector Fusion: ", "Integrates hourly ERA5 10m wind with Copernicus Marine (CMEMS) hydrodynamic surface currents."),
    ("Governing Advection: ", "V = U_curr + 0.03*R(15°)*U_wind. Integrates Runge-Kutta 2nd-order (RK2) midpoint scheme (dt=1h)."),
    ("50-Particle Inversion: ", "Monte Carlo ensemble traces back from slick contour; frozen origin threshold triggers when spread R >= 8 km."),
    ("36h Dispersion Cone: ", "Forward forecast cone cross-checks Natural Earth 1:10m coastline for automated landfall threat alerts.")
]
for bold_prefix, text in items_c2:
    p = tf_c2.add_paragraph() if tf_c2.paragraphs[0].text else tf_c2.paragraphs[0]
    p.space_after = Pt(3.5)
    run_b = p.add_run()
    run_b.text = "• " + bold_prefix
    run_b.font.name = FONT_BODY
    run_b.font.bold = True
    run_b.font.size = Pt(9.2)
    run_b.font.color.rgb = C_NAVY
    run_t = p.add_run()
    run_t.text = text
    run_t.font.name = FONT_BODY
    run_t.font.size = Pt(8.8)
    run_t.font.color.rgb = C_TEXT_MAIN

# Card 3: Clause c
add_card(slide1, 8.88, 3.15, 3.85, 2.75, "Clause (c): AIS Forensic Attribution", bg_color=C_CARD_BG, border_color=C_AMBER)
tb_c3 = slide1.shapes.add_textbox(Inches(9.03), Inches(3.55), Inches(3.55), Inches(2.25))
tf_c3 = tb_c3.text_frame
tf_c3.word_wrap = True
tf_c3.margin_left = tf_c3.margin_right = tf_c3.margin_top = tf_c3.margin_bottom = 0
items_c3 = [
    ("Spatio-Temporal Funnel: ", "Ingests MarineCadastre AIS into SQLite; filters traffic within radius (R_zone + 10km) and time [T_orig ± 3h]."),
    ("1-Min Geodesic Track: ", "Resamples vessel pings using spherical great-circle geodesics with heading unwrapping."),
    ("Multi-Factor Scoring: ", "S = 0.30*prox + 0.20*time + 0.25*beh + 0.15*type + 0.10*traj. Proximity measured to origin point, not broad zone."),
    ("Dark Vessel Detection: ", "Flags AIS blackout gaps (>= 30 min) passing near origin (S_beh=0.95); confidence-clamps ghost ships.")
]
for bold_prefix, text in items_c3:
    p = tf_c3.add_paragraph() if tf_c3.paragraphs[0].text else tf_c3.paragraphs[0]
    p.space_after = Pt(3.5)
    run_b = p.add_run()
    run_b.text = "• " + bold_prefix
    run_b.font.name = FONT_BODY
    run_b.font.bold = True
    run_b.font.size = Pt(9.2)
    run_b.font.color.rgb = C_NAVY
    run_t = p.add_run()
    run_t.text = text
    run_t.font.name = FONT_BODY
    run_t.font.size = Pt(8.8)
    run_t.font.color.rgb = C_TEXT_MAIN

# Bottom KPI Bar (4 Proven Metric Badges)
kpis = [
    ("0.8891 IoU", "Zenodo Benchmark Model"),
    ("< 10s Execution", "High-Throughput GPU / 30s CPU"),
    ("100% Explainable", "Transparent Scored Leaderboard"),
    ("126 Test Suites", "Rigorous Test-Driven Architecture")
]
for i, (metric, label) in enumerate(kpis):
    kx = 0.6 + i * 3.09
    card_k = slide1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(kx), Inches(6.00), Inches(2.85), Inches(0.95))
    card_k.fill.solid()
    card_k.fill.fore_color.rgb = C_WHITE
    card_k.line.color.rgb = C_CARD_BORDER
    card_k.line.width = Pt(1.0)
    card_k.adjustments[0] = 0.08
    tb_k = slide1.shapes.add_textbox(Inches(kx), Inches(6.08), Inches(2.85), Inches(0.78))
    tf_k = tb_k.text_frame
    tf_k.margin_left = tf_k.margin_right = tf_k.margin_top = tf_k.margin_bottom = 0
    p_m = tf_k.paragraphs[0]
    p_m.text = metric
    p_m.font.name = FONT_HEAD
    p_m.font.size = Pt(15)
    p_m.font.bold = True
    p_m.font.color.rgb = C_BLUE
    p_m.alignment = PP_ALIGN.CENTER
    p_l = tf_k.add_paragraph()
    p_l.text = label
    p_l.font.name = FONT_BODY
    p_l.font.size = Pt(8.5)
    p_l.font.color.rgb = C_TEXT_MUTED
    p_l.alignment = PP_ALIGN.CENTER

# ==============================================================================
# SLIDE 2: PROPOSED SOLUTION
# ==============================================================================
slide2 = prs.slides.add_slide(blank_layout)
set_slide_background(slide2)
add_footer(slide2, 2)

add_header(slide2, "PROPOSED SOLUTION (IDEA / SOLUTION / PROTOTYPE)",
           "TideTrace: Automated Oil Spill Detection & Maritime Culprit Attribution",
           "Fusing SAR remote sensing, metocean drift physics, and historical AIS telemetry into an actionable operational console.",
           2)

# Left Column Top: Detailed Explanation of Proposed Solution
add_card(slide2, 0.6, 1.55, 5.85, 2.70, "• Detailed Explanation of the Proposed Solution", bg_color=C_CARD_BG, border_color=C_BLUE)
tb_s2_exp = slide2.shapes.add_textbox(Inches(0.75), Inches(1.92), Inches(5.55), Inches(2.25))
tf_exp = tb_s2_exp.text_frame
tf_exp.word_wrap = True
tf_exp.margin_left = tf_exp.margin_right = tf_exp.margin_top = tf_exp.margin_bottom = 0
exp_points = [
    ("Automated SAR Ingestion: ", "Directly ingests Sentinel-1 C-band SAR dual-pol (VV/VH) GeoTIFFs, sensing capillary wave damping day/night and through monsoon clouds."),
    ("Deep Learning UNet++: ", "Segments oil slicks from look-alikes with 0.8891 IoU; calculates local AEQD geodetic area, perimeter, and PCA orientation."),
    ("Lagrangian Drift Inversion: ", "Couples ERA5 wind and Copernicus hydrodynamic currents via RK2 integration to rewind slick to exact release origin."),
    ("Forensic AIS Attribution: ", "Queries indexed maritime AIS, filters spatio-temporal funnel, and scores suspect vessels on proximity, behavior, and blackout gaps.")
]
for title, desc in exp_points:
    p = tf_exp.add_paragraph() if tf_exp.paragraphs[0].text else tf_exp.paragraphs[0]
    p.space_after = Pt(3)
    rb = p.add_run()
    rb.text = "• " + title
    rb.font.bold = True
    rb.font.size = Pt(9.0)
    rb.font.color.rgb = C_NAVY
    rt = p.add_run()
    rt.text = desc
    rt.font.size = Pt(8.6)
    rt.font.color.rgb = C_TEXT_MAIN

# Left Column Bottom: How It Addresses the Problem
add_card(slide2, 0.6, 4.35, 5.85, 2.70, "• How It Addresses the NTRO Problem Statement", bg_color=C_CARD_BG, border_color=C_TEAL)
tb_s2_prob = slide2.shapes.add_textbox(Inches(0.75), Inches(4.72), Inches(5.55), Inches(2.25))
tf_prob = tb_s2_prob.text_frame
tf_prob.word_wrap = True
tf_prob.margin_left = tf_prob.margin_right = tf_prob.margin_top = tf_prob.margin_bottom = 0
prob_points = [
    ("Eliminates 'Unattributable' Spills: ", "Over 80% of marine spills escape liability. TideTrace bridges the physical and temporal gap between observed slick and moving ships."),
    ("Catches Evasive Dark Vessels: ", "Vessels turning off AIS transponders to dump bilge waste are flagged via 30+ min dead-reckoning blackout gap forensics."),
    ("Sub-10s Rapid Deployment: ", "Replaces multi-day manual satellite interpretation with sub-minute automated detection and immediate coastal alerts."),
    ("Legally Admissible Proof: ", "Produces tamper-evident Maritime Pollution Attribution Notes with SHA-256 scene hashes and telemetry evidence.")
]
for title, desc in prob_points:
    p = tf_prob.add_paragraph() if tf_prob.paragraphs[0].text else tf_prob.paragraphs[0]
    p.space_after = Pt(3)
    rb = p.add_run()
    rb.text = "• " + title
    rb.font.bold = True
    rb.font.size = Pt(9.0)
    rb.font.color.rgb = C_NAVY
    rt = p.add_run()
    rt.text = desc
    rt.font.size = Pt(8.6)
    rt.font.color.rgb = C_TEXT_MAIN

# Right Column Top: Innovation & Uniqueness of the Solution
add_card(slide2, 6.65, 1.55, 6.08, 2.55, "• Innovation and Uniqueness of the Solution", bg_color=C_CARD_BG, border_color=C_AMBER)
tb_s2_inn = slide2.shapes.add_textbox(Inches(6.80), Inches(1.92), Inches(5.78), Inches(2.10))
tf_inn = tb_s2_inn.text_frame
tf_inn.word_wrap = True
tf_inn.margin_left = tf_inn.margin_right = tf_inn.margin_top = tf_inn.margin_bottom = 0
innovations = [
    ("Look-Alike & Clean Water Floor: ", "Multi-class segmentation rejects biogenic films. Strict <3.0 dB radiometric floor flags uniform sea with 0 false slicks."),
    ("Dark Vessel Blackout Tracking: ", "Flags AIS deactivations (>= 30 min gaps crossing within 5 km of origin); awards S_beh = 0.95 anomaly score."),
    ("Morphological Telemetry & Geodesy: ", "Local AEQD projection eliminates distortion; calculates metric area (km²), Feret length, and PCA orientation."),
    ("Court-Admissible Dossier: ", "Auto-exports Maritime Pollution Attribution Note (PDF/HTML) with SHA-256 hash and itemized telemetry.")
]
for title, desc in innovations:
    p = tf_inn.add_paragraph() if tf_inn.paragraphs[0].text else tf_inn.paragraphs[0]
    p.space_after = Pt(3)
    run_h = p.add_run()
    run_h.text = "✓ " + title
    run_h.font.bold = True
    run_h.font.size = Pt(9.0)
    run_h.font.color.rgb = C_BLUE
    run_d = p.add_run()
    run_d.text = desc
    run_d.font.size = Pt(8.6)
    run_d.font.color.rgb = C_TEXT_MAIN

# Right Column Bottom: Working Prototype - Ranked Culprit Leaderboard
add_card(slide2, 6.65, 4.20, 6.08, 2.85, "• Working Prototype: Ranked Culprit Leaderboard", bg_color=C_CARD_BG, border_color=C_AMBER)
vessels_crop_path = Path("docs/live_screenshots/vessels_leaderboard_focus.png")
if not vessels_crop_path.exists():
    vessels_crop_path = Path("docs/live_screenshots/live_vessels_light.png")
add_picture_fit(slide2, vessels_crop_path, 6.75, 4.55, 5.88, 2.42)

# ==============================================================================
# SLIDE 3: TECHNICAL APPROACH
# ==============================================================================
slide3 = prs.slides.add_slide(blank_layout)
set_slide_background(slide3)
add_footer(slide3, 3)

add_header(slide3, "TECHNICAL APPROACH (TECHNOLOGIES, PROCESS & WORKING PROTOTYPE)",
           "End-to-End System Pipeline, Tech Stack & Operational Console",
           "Modular 12-stage automated pipeline uniting deep learning segmentation, metocean physics, and spatio-temporal forensics.",
           3)

# Top: Process & Flowchart Diagram
add_card(slide3, 0.6, 1.55, 12.13, 2.70, "• Methodology & Process for Implementation: 12-Stage Automated Pipeline", bg_color=C_CARD_BG, border_color=C_BLUE)
diag_crop_path = Path("docs/architecture_diagram_light_cropped.png")
if not diag_crop_path.exists():
    diag_crop_path = Path("docs/architecture_diagram_light.png")
add_picture_fit(slide3, diag_crop_path, 0.75, 1.90, 6.75, 2.28)

# Right Side of Top Card: 3-Phase Core Engineering Highlights
tb_ph = slide3.shapes.add_textbox(Inches(7.65), Inches(1.88), Inches(4.90), Inches(2.30))
tf_ph = tb_ph.text_frame
tf_ph.word_wrap = True
tf_ph.margin_left = tf_ph.margin_right = tf_ph.margin_top = tf_ph.margin_bottom = 0

phase_highlights = [
    ("Phase A (SAR & ML Segmentation): ", "Dual-pol Sentinel-1 C-band GeoTIFFs, 512x512 cosine-smoothed tiling, UNet++ (0.8891 IoU), local AEQD metric geodetic extraction."),
    ("Phase B (Lagrangian Metocean Drift): ", "ERA5 10m wind + CMEMS current fusion, RK2 midpoint scheme with +15° Coriolis leeway, 50-particle Monte Carlo back-stepping to 8km origin, 36h coastal threat cone."),
    ("Phase C (AIS Spatio-Temporal Forensics): ", "Indexed SQLite database query, great-circle geodesic resampling, dark vessel blackout detection (S_beh=0.95), tamper-evident SHA-256 PDF legal dossier.")
]
for p_title, p_desc in phase_highlights:
    p = tf_ph.add_paragraph() if tf_ph.paragraphs[0].text else tf_ph.paragraphs[0]
    p.space_after = Pt(3.0)
    rb = p.add_run()
    rb.text = "• " + p_title
    rb.font.name = FONT_BODY
    rb.font.bold = True
    rb.font.size = Pt(8.8)
    rb.font.color.rgb = C_BLUE
    rt = p.add_run()
    rt.text = p_desc
    rt.font.name = FONT_BODY
    rt.font.size = Pt(8.2)
    rt.font.color.rgb = C_TEXT_MAIN


# Bottom Left: Technologies to be Used
add_card(slide3, 0.6, 4.35, 5.85, 2.70, "• Technologies to be Used", bg_color=C_CARD_BG, border_color=C_BLUE)
tb_tech = slide3.shapes.add_textbox(Inches(0.75), Inches(4.72), Inches(5.55), Inches(2.25))
tf_tech = tb_tech.text_frame
tf_tech.word_wrap = True
tf_tech.margin_left = tf_tech.margin_right = tf_tech.margin_top = tf_tech.margin_bottom = 0
tech_stack = [
    ("Deep Learning: ", "PyTorch 2.5, Segmentation Models PyTorch (SMP), UNet++ with timm-efficientnet-b0 backbone, ImageNet pre-trained stem."),
    ("Geospatial & Geodesy: ", "pyproj (Local AEQD projection), rasterio, Great-Circle geodesics, Scipy connected components & morphology."),
    ("Numerical Physics: ", "2nd-order Runge-Kutta (RK2) midpoint integrator, 50-particle Monte Carlo dispersion, +15° Coriolis leeway deflection."),
    ("Backend & Storage: ", "FastAPI async REST framework, Pydantic v2 schemas, SQLite spatial/temporal indexing, Uvicorn ASGI server."),
    ("Visual Console: ", "Vanilla HTML5/CSS3/ES6, Leaflet 1.9, Esri Satellite & Ocean Basemaps, interactive scrubbing timeline.")
]
for title, desc in tech_stack:
    p = tf_tech.add_paragraph() if tf_tech.paragraphs[0].text else tf_tech.paragraphs[0]
    p.space_after = Pt(3.5)
    rb = p.add_run()
    rb.text = "• " + title
    rb.font.name = FONT_BODY
    rb.font.bold = True
    rb.font.size = Pt(9.0)
    rb.font.color.rgb = C_NAVY
    rt = p.add_run()
    rt.text = desc
    rt.font.name = FONT_BODY
    rt.font.size = Pt(8.6)
    rt.font.color.rgb = C_TEXT_MAIN

# Bottom Right: Working Prototype - Investigation & Drift Console
add_card(slide3, 6.65, 4.35, 6.08, 2.70, "• Working Prototype: Investigation & Drift Console", bg_color=C_CARD_BG, border_color=C_TEAL)
proto_crop_path = Path("docs/live_screenshots/investigate_focus.png")
if not proto_crop_path.exists():
    proto_crop_path = Path("docs/live_screenshots/live_investigate_light.png")
add_picture_fit(slide3, proto_crop_path, 6.75, 4.70, 5.88, 2.28)

# ==============================================================================
# SLIDE 4: FEASIBILITY AND VIABILITY
# ==============================================================================
slide4 = prs.slides.add_slide(blank_layout)
set_slide_background(slide4)
add_footer(slide4, 4)

add_header(slide4, "FEASIBILITY AND VIABILITY (ANALYSIS, RISKS & MITIGATION STRATEGIES)",
           "Operational Feasibility & Technical Risk Mitigation Framework",
           "Ensuring computational efficiency, data accessibility, and high-fidelity resilience against real-world maritime challenges.",
           4)

# Top: Feasibility Analysis (3 Dimensions with Non-Overlapping Stat Badges & Crisp Bullets)
feasibility_dims = [
    ("Computational Feasibility", "< 10s GPU / 45s CPU",
     [("Sliding Window: ", "512x512 inference with cosine-tapered edge smoothing."),
      ("Zero Memory Spill: ", "Runs reliably on edge laptops (< 1 GB RAM footprint).")],
     C_BLUE_LIGHT, C_BLUE),
    
    ("Data Availability & Access", "100% Free Open Data",
     [("Public Satellites: ", "Copernicus Sentinel-1 SAR + ECMWF ERA5 wind/currents."),
      ("Public AIS Feeds: ", "MarineCadastre & national coastal feeds; zero lock-in.")],
     C_TEAL_LIGHT, C_TEAL),
    
    ("Operational Deployment", "Plug-and-Play API",
     [("Modular REST API: ", "Async FastAPI microservices (/api/run, /api/report)."),
      ("Direct Drop-In: ", "Seamless integration into Coast Guard & NTRO consoles.")],
     C_AMBER_LIGHT, C_AMBER)
]

for i, (f_title, f_badge, f_bullets, bg_col, b_col) in enumerate(feasibility_dims):
    fx = 0.6 + i * 4.13
    add_card(slide4, fx, 1.55, 3.85, 1.45, title=None, bg_color=bg_col, border_color=b_col, border_width=Pt(1.2))
    
    # Title on left (width 2.10")
    tb_t = slide4.shapes.add_textbox(Inches(fx + 0.15), Inches(1.62), Inches(2.10), Inches(0.30))
    tf_t = tb_t.text_frame
    tf_t.word_wrap = True
    tf_t.margin_left = tf_t.margin_right = tf_t.margin_top = tf_t.margin_bottom = 0
    p_t = tf_t.paragraphs[0]
    p_t.text = "• " + f_title
    p_t.font.name = FONT_HEAD
    p_t.font.size = Pt(9.8)
    p_t.font.bold = True
    p_t.font.color.rgb = C_NAVY

    # Badge on right (width 1.40")
    badge_f = slide4.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(fx + 2.30), Inches(1.63), Inches(1.40), Inches(0.24))
    badge_f.fill.solid()
    badge_f.fill.fore_color.rgb = C_WHITE
    badge_f.line.color.rgb = b_col
    badge_f.line.width = Pt(1.0)
    badge_f.adjustments[0] = 0.4
    tf_bf = badge_f.text_frame
    tf_bf.margin_left = tf_bf.margin_right = tf_bf.margin_top = tf_bf.margin_bottom = 0
    p_bf = tf_bf.paragraphs[0]
    p_bf.text = f_badge
    p_bf.font.name = FONT_HEAD
    p_bf.font.size = Pt(7.8)
    p_bf.font.bold = True
    p_bf.font.color.rgb = b_col
    p_bf.alignment = PP_ALIGN.CENTER

    tb_f = slide4.shapes.add_textbox(Inches(fx + 0.15), Inches(1.96), Inches(3.55), Inches(0.95))
    tf_f = tb_f.text_frame
    tf_f.word_wrap = True
    tf_f.margin_left = tf_f.margin_right = tf_f.margin_top = tf_f.margin_bottom = 0
    
    for b_idx, (b_head, b_desc) in enumerate(f_bullets):
        p = tf_f.add_paragraph() if b_idx > 0 else tf_f.paragraphs[0]
        p.space_after = Pt(2.5)
        rh = p.add_run()
        rh.text = "• " + b_head
        rh.font.name = FONT_BODY
        rh.font.bold = True
        rh.font.size = Pt(8.8)
        rh.font.color.rgb = C_NAVY
        rd = p.add_run()
        rd.text = b_desc
        rd.font.name = FONT_BODY
        rd.font.size = Pt(8.5)
        rd.font.color.rgb = C_TEXT_MAIN

# Bottom: 4 Key Challenges & Proven Mitigation Strategies (2x2 Grid with Short Punchy Text)
challenges = [
    ("SAR Look-Alikes & Biogenic Films",
     "Low winds (< 3 m/s) and algal slicks mimic petroleum radar damping.",
     "2-stage UNet++ filter + 3.0 dB clean-water floor (0 false alarms in Mumbai test).",
     "Tested on Arabian Sea & Mumbai Offshore with zero false slicks."),

    ("AIS Evasion & Dark Vessels",
     "Deliberate transponder shut-offs prior to illegal bilge discharges.",
     "Trajectory Gap Forensics: dead-reckons >= 30 min gaps within 5 km; S_beh = 0.95.",
     "Penalizes dark vessels in leaderboard with high behavioral anomaly score."),

    ("Turbulent Ocean Eddy Drift",
     "Single back-drift vectors diverge rapidly in dynamic coastal currents.",
     "50-particle Monte Carlo ensemble with RK2 midpoint physics; bounds 8 km origin.",
     "Stochastic wind/current jitter establishes robust spatial-temporal envelope."),

    ("Monsoon Clouds & Night Blindness",
     "Clouds, rain, and darkness blind optical sensors (Sentinel-2, Landsat).",
     "Active C-band SAR radar operates 24/7; optical used for daytime corroboration.",
     "All-weather radar microwave penetration independent of solar illumination.")
]

for i, (c_title, c_risk, c_strat, c_val) in enumerate(challenges):
    row = i // 2
    col = i % 2
    cx = 0.6 + col * 6.22
    cy = 3.15 + row * 1.95
    add_card(slide4, cx, cy, 5.91, 1.82, f"• Challenge {i+1}: " + c_title, bg_color=C_CARD_BG, border_color=C_CARD_BORDER)
    tb_c = slide4.shapes.add_textbox(Inches(cx + 0.18), Inches(cy + 0.40), Inches(5.55), Inches(1.35))
    tf_c = tb_c.text_frame
    tf_c.word_wrap = True
    tf_c.margin_left = tf_c.margin_right = tf_c.margin_top = tf_c.margin_bottom = 0

    p_r = tf_c.paragraphs[0]
    p_r.space_after = Pt(3.0)
    rr_tag = p_r.add_run()
    rr_tag.text = "⚠️ Risk: "
    rr_tag.font.name = FONT_BODY
    rr_tag.font.bold = True
    rr_tag.font.size = Pt(8.8)
    rr_tag.font.color.rgb = C_AMBER
    rr_txt = p_r.add_run()
    rr_txt.text = c_risk
    rr_txt.font.name = FONT_BODY
    rr_txt.font.size = Pt(8.5)
    rr_txt.font.color.rgb = C_TEXT_MAIN

    p_s = tf_c.add_paragraph()
    p_s.space_after = Pt(3.0)
    rs_tag = p_s.add_run()
    rs_tag.text = "🛡️ Proven Strategy: "
    rs_tag.font.name = FONT_BODY
    rs_tag.font.bold = True
    rs_tag.font.size = Pt(8.8)
    rs_tag.font.color.rgb = C_TEAL
    rs_txt = p_s.add_run()
    rs_txt.text = c_strat
    rs_txt.font.name = FONT_BODY
    rs_txt.font.size = Pt(8.5)
    rs_txt.font.color.rgb = C_TEXT_MAIN

    p_v = tf_c.add_paragraph()
    rv_tag = p_v.add_run()
    rv_tag.text = "📌 Validation: "
    rv_tag.font.name = FONT_BODY
    rv_tag.font.bold = True
    rv_tag.font.size = Pt(8.8)
    rv_tag.font.color.rgb = C_BLUE
    rv_txt = p_v.add_run()
    rv_txt.text = c_val
    rv_txt.font.name = FONT_BODY
    rv_txt.font.size = Pt(8.5)
    rv_txt.font.color.rgb = C_TEXT_MAIN

# ==============================================================================
# SLIDE 5: IMPACT AND BENEFITS
# ==============================================================================
slide5 = prs.slides.add_slide(blank_layout)
set_slide_background(slide5)
add_footer(slide5, 5)

add_header(slide5, "IMPACT AND BENEFITS (AUDIENCE IMPACT & MULTI-DIMENSIONAL BENEFITS)",
           "Empowering Maritime Domain Awareness & Disaster Management",
           "Delivering actionable intelligence, national ecological protection, legal accountability, and cost savings.",
           5)

# Top: Potential Impact on Target Audience (4 Stakeholders with High-Contrast Capability Pills)
stakeholders = [
    ("NTRO Intelligence", "24/7 Radar Watch", "Automated satellite-to-AIS surveillance across Indian EEZ.", C_BLUE_LIGHT, C_BLUE),
    ("Indian Coast Guard", "Sub-Minute Intercept", "Instant intercept coordinates replace 3-day blind patrols.", C_TEAL_LIGHT, C_TEAL),
    ("DG Shipping & Ports", "MARPOL Enforcement", "Tamper-evident legal dossier enables port vessel detention.", C_AMBER_LIGHT, C_AMBER),
    ("INCOIS & MoES", "36h Coastal Alert", "Early drift forecasting shields sensitive mangrove coastlines.", C_CARD_BG, C_NAVY)
]

for i, (s_title, s_badge, s_desc, bg_col, b_col) in enumerate(stakeholders):
    sx = 0.6 + i * 3.09
    add_card(slide5, sx, 1.55, 2.85, 1.35, "• " + s_title, bg_color=bg_col, border_color=b_col, border_width=Pt(1.2))
    
    # Capability pill
    pill = slide5.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(sx + 0.15), Inches(1.92), Inches(2.55), Inches(0.24))
    pill.fill.solid()
    pill.fill.fore_color.rgb = C_WHITE
    pill.line.color.rgb = b_col
    pill.line.width = Pt(1.0)
    pill.adjustments[0] = 0.4
    tf_p = pill.text_frame
    tf_p.margin_left = tf_p.margin_right = tf_p.margin_top = tf_p.margin_bottom = 0
    p_pill = tf_p.paragraphs[0]
    p_pill.text = s_badge.upper()
    p_pill.font.name = FONT_HEAD
    p_pill.font.size = Pt(7.8)
    p_pill.font.bold = True
    p_pill.font.color.rgb = b_col
    p_pill.alignment = PP_ALIGN.CENTER

    tb_s = slide5.shapes.add_textbox(Inches(sx + 0.15), Inches(2.22), Inches(2.55), Inches(0.60))
    tf_s = tb_s.text_frame
    tf_s.word_wrap = True
    tf_s.margin_left = tf_s.margin_right = tf_s.margin_top = tf_s.margin_bottom = 0
    p = tf_s.paragraphs[0]
    p.text = s_desc
    p.font.name = FONT_BODY
    p.font.size = Pt(8.4)
    p.font.color.rgb = C_TEXT_MAIN

# Bottom: Multi-Dimensional Benefits Matrix (4 Executive Split KPI Cards: Bold Stat Left + 2 Concise Bullets Right)
benefits = [
    ("Environmental Defense", "36h", "Early Warning",
     [("Mangrove & Reef Shield: ", "Alerts fragile shores before oil makes landfall."),
      ("Targeted Skimming: ", "Guides containment booms directly to thickest slick centers.")]),
    
    ("Economic & Resource ROI", "> 80%", "Cost Reduction",
     [("Flight Hour Savings: ", "Slashes expensive aviation transit via direct intercept paths."),
      ("Polluter Pays Doctrine: ", "Enables full recovery of multi-crore cleanup costs from owners.")]),
    
    ("Legal Accountability", "SHA-256", "Tamper-Proof",
     [("Attribution Certainty: ", "Bridges ocean physical dispersion with recorded AIS ship tracks."),
      ("Court-Admissible Dossier: ", "Auto-exports tamper-evident forensic dossier for prosecution.")]),
    
    ("Social & Coastal Security", "100%", "Livelihood Shield",
     [("Fisheries Security: ", "Safeguards artisanal coastal fishing grounds & aquaculture."),
      ("Desalination Safety: ", "Early warning enables coastal plants to shut intake valves in time.")])
]

for i, (b_title, b_num, b_sub, b_points) in enumerate(benefits):
    row = i // 2
    col = i % 2
    bx = 0.6 + col * 6.22
    by = 3.10 + row * 1.95
    
    col_map = [C_TEAL, C_BLUE, C_AMBER, C_NAVY]
    col_bg_map = [C_TEAL_LIGHT, C_BLUE_LIGHT, C_AMBER_LIGHT, C_BLUE_LIGHT]
    theme_col = col_map[i]
    theme_bg = col_bg_map[i]
    
    # Outer card
    add_card(slide5, bx, by, 5.91, 1.82, title=None, bg_color=C_CARD_BG, border_color=theme_col, border_width=Pt(1.2))
    
    # Left KPI Stat Callout Box
    kpi_box = slide5.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(bx + 0.12), Inches(by + 0.12), Inches(1.60), Inches(1.58))
    kpi_box.fill.solid()
    kpi_box.fill.fore_color.rgb = theme_bg
    kpi_box.line.color.rgb = theme_col
    kpi_box.line.width = Pt(1.0)
    kpi_box.adjustments[0] = 0.15
    tf_kpi = kpi_box.text_frame
    tf_kpi.word_wrap = True
    tf_kpi.margin_left = tf_kpi.margin_right = tf_kpi.margin_top = tf_kpi.margin_bottom = 0
    
    p_num = tf_kpi.paragraphs[0]
    p_num.text = b_num
    p_num.font.name = FONT_HEAD
    p_num.font.size = Pt(22) if len(b_num) <= 5 else Pt(17)
    p_num.font.bold = True
    p_num.font.color.rgb = theme_col
    p_num.alignment = PP_ALIGN.CENTER
    p_num.space_before = Pt(18)
    p_num.space_after = Pt(2)
    
    p_lbl = tf_kpi.add_paragraph()
    p_lbl.text = b_sub.upper()
    p_lbl.font.name = FONT_HEAD
    p_lbl.font.size = Pt(7.6)
    p_lbl.font.bold = True
    p_lbl.font.color.rgb = theme_col
    p_lbl.alignment = PP_ALIGN.CENTER
    
    # Right Column: Title + 2 Concise Bullets
    tb_r = slide5.shapes.add_textbox(Inches(bx + 1.85), Inches(by + 0.15), Inches(3.90), Inches(1.52))
    tf_r = tb_r.text_frame
    tf_r.word_wrap = True
    tf_r.margin_left = tf_r.margin_right = tf_r.margin_top = tf_r.margin_bottom = 0
    
    p_t = tf_r.paragraphs[0]
    p_t.text = "• " + b_title
    p_t.font.name = FONT_HEAD
    p_t.font.size = Pt(11.5)
    p_t.font.bold = True
    p_t.font.color.rgb = C_NAVY
    p_t.space_after = Pt(6)
    
    for b_idx, (b_head, b_desc) in enumerate(b_points):
        p = tf_r.add_paragraph()
        p.space_after = Pt(5.5)
        
        rh = p.add_run()
        rh.text = "✓ " + b_head
        rh.font.name = FONT_BODY
        rh.font.bold = True
        rh.font.size = Pt(9.0)
        rh.font.color.rgb = C_NAVY
        
        rd = p.add_run()
        rd.text = b_desc
        rd.font.name = FONT_BODY
        rd.font.size = Pt(8.6)
        rd.font.color.rgb = C_TEXT_MAIN

# ==============================================================================
# SLIDE 6: RESEARCH AND REFERENCES
# ==============================================================================
slide6 = prs.slides.add_slide(blank_layout)
set_slide_background(slide6)
add_footer(slide6, 6)

add_header(slide6, "RESEARCH AND REFERENCES (DATASETS, SCIENTIFIC LITERATURE & BENCHMARKS)",
           "Scientific Foundations, Open Datasets & Empirical Validation",
           "Grounded in published physical oceanography, peer-reviewed remote sensing, and open satellite archives.",
           6)

# Column 1: Authoritative Datasets Used
add_card(slide6, 0.6, 1.55, 3.85, 5.45, "• Datasets & Remote Sensing Sources", bg_color=C_CARD_BG, border_color=C_BLUE)
tb_d = slide6.shapes.add_textbox(Inches(0.75), Inches(1.95), Inches(3.55), Inches(4.90))
tf_d = tb_d.text_frame
tf_d.word_wrap = True
tf_d.margin_left = tf_d.margin_right = tf_d.margin_top = tf_d.margin_bottom = 0
datasets_info = [
    ("Zenodo Sentinel-1 SAR Oil Spill Dataset", "Parts I, II & III (DOIs: 10.5281/zenodo.8346860, .8253899, .13761290). 1,164 annotated dual-pol SAR image tiles used for UNet++ training & validation."),
    ("Copernicus Sentinel-1 SAR IW GRD", "C-band (5.405 GHz) dual-pol (VV/VH) radiometrically terrain corrected (RTC) cloud-optimized GeoTIFFs via Microsoft Planetary Computer."),
    ("MarineCadastre.gov & NOAA / BOEM", "Standardized national maritime AIS database. Ingests MMSI, timestamp, SOG, COG, heading, vessel type, and draught into indexed SQLite schema."),
    ("ECMWF ERA5 Atmospheric Reanalysis", "Hourly 10m surface wind vectors (u10, v10) at 0.25° resolution via Open-Meteo Archive API."),
    ("Copernicus Marine Service (CMEMS)", "GLOBAL_ANALYSISFORECAST_PHY_001_024 eddy-resolving 1/12° hydrodynamic surface currents."),
    ("Natural Earth Physical Vectors", "1:10m high-resolution land polygons for coastal impact checks; 1:110m and 1:50m simplified vectors for global basemap.")
]
for title, desc in datasets_info:
    p = tf_d.add_paragraph() if tf_d.paragraphs[0].text else tf_d.paragraphs[0]
    p.space_after = Pt(3.5)
    rb = p.add_run()
    rb.text = "• " + title + ": "
    rb.font.name = FONT_BODY
    rb.font.bold = True
    rb.font.size = Pt(8.8)
    rb.font.color.rgb = C_NAVY
    rt = p.add_run()
    rt.text = desc
    rt.font.name = FONT_BODY
    rt.font.size = Pt(8.2)
    rt.font.color.rgb = C_TEXT_MAIN

# Column 2: Academic Literature & Methodological References
add_card(slide6, 4.74, 1.55, 3.85, 5.45, "• Scientific & Peer-Reviewed References", bg_color=C_CARD_BG, border_color=C_TEAL)
tb_r = slide6.shapes.add_textbox(Inches(4.89), Inches(1.95), Inches(3.55), Inches(4.90))
tf_r = tb_r.text_frame
tf_r.word_wrap = True
tf_r.margin_left = tf_r.margin_right = tf_r.margin_top = tf_r.margin_bottom = 0
literature_info = [
    ("SAR Oil Film Damping Physics", "Alpers, W. et al. (2017). Oil spill detection by imaging radars: Challenges and pitfalls. Remote Sensing of Environment. Details capillary-gravity wave damping and biogenic vs. mineral oil discrimination."),
    ("Deep Learning Image Segmentation", "Zhou, Z. et al. (2018). UNet++: A Nested U-Net Architecture for Medical Image Segmentation. IEEE TMI. Provides multi-scale skip pathways for sharp slick boundary delineation."),
    ("Lagrangian Metocean Advection", "NOAA GNOME (General NOAA Operational Modeling Environment) Technical Manual. Establishes the 3% wind leeway rule and +15° Ekman Coriolis deflection in Northern Hemisphere."),
    ("Numerical Inversion & RK2 Midpoint", "Press, W. et al. Numerical Recipes in C: The Art of Scientific Computing. 2nd-order Runge-Kutta scheme for time-reversible particle advection."),
    ("Spatio-Temporal AIS Forensics", "Arguedas, V. et al. (2018). Maritime anomaly detection and vessel trajectory forecasting using AIS data. Ocean Engineering.")
]
for title, desc in literature_info:
    p = tf_r.add_paragraph() if tf_r.paragraphs[0].text else tf_r.paragraphs[0]
    p.space_after = Pt(4.5)
    rb = p.add_run()
    rb.text = "• " + title + ": "
    rb.font.name = FONT_BODY
    rb.font.bold = True
    rb.font.size = Pt(8.8)
    rb.font.color.rgb = C_NAVY
    rt = p.add_run()
    rt.text = desc
    rt.font.name = FONT_BODY
    rt.font.size = Pt(8.2)
    rt.font.color.rgb = C_TEXT_MAIN

# Column 3: Empirical Validation & Benchmark Comparison Table
add_card(slide6, 8.88, 1.55, 3.85, 5.45, "• Empirical Validation Benchmarks", bg_color=C_CARD_BG, border_color=C_AMBER)
tb_bmark = slide6.shapes.add_textbox(Inches(9.03), Inches(1.95), Inches(3.55), Inches(4.90))
tf_bm = tb_bmark.text_frame
tf_bm.word_wrap = True
tf_bm.margin_left = tf_bm.margin_right = tf_bm.margin_top = tf_bm.margin_bottom = 0

p_bt = tf_bm.paragraphs[0]
p_bt.text = "Model Evaluation on Zenodo Held-Out Test Set:"
p_bt.font.name = FONT_HEAD
p_bt.font.size = Pt(9.5)
p_bt.font.bold = True
p_bt.font.color.rgb = C_NAVY
p_bt.space_after = Pt(3)

# Table inside card
table_shape = slide6.shapes.add_table(6, 3, Inches(9.03), Inches(2.25), Inches(3.55), Inches(1.75))
tbl = table_shape.table
tbl.columns[0].width = Inches(1.55)
tbl.columns[1].width = Inches(1.0)
tbl.columns[2].width = Inches(1.0)

bmark_rows = [
    ("Metric", "TideTrace", "Baseline"),
    ("IoU Oil", "0.8891", "0.0000"),
    ("Mean IoU (mIoU)", "0.9356", "0.2882"),
    ("Pixel Accuracy", "98.44%", "86.45%"),
    ("Water False Oil", "0.0106", "0.0038"),
    ("Execution Time", "< 10s (GPU)", "Instant")
]
for r_idx, (c0, c1, c2) in enumerate(bmark_rows):
    for c_idx, val in enumerate([c0, c1, c2]):
        cell = tbl.cell(r_idx, c_idx)
        cell.text = val
        cell.margin_left = cell.margin_right = cell.margin_top = cell.margin_bottom = 0
        p = cell.text_frame.paragraphs[0]
        p.font.name = FONT_HEAD if r_idx == 0 else FONT_BODY
        p.font.size = Pt(8.2) if r_idx > 0 else Pt(8.8)
        p.font.bold = (r_idx == 0 or c_idx == 1)
        p.font.color.rgb = C_BLUE if (r_idx > 0 and c_idx == 1) else (C_NAVY if r_idx == 0 else C_TEXT_MAIN)
        p.alignment = PP_ALIGN.CENTER if c_idx > 0 else PP_ALIGN.LEFT

# Multi-Sea Benchmark notes below table
tb_ms = slide6.shapes.add_textbox(Inches(9.03), Inches(4.15), Inches(3.55), Inches(2.75))
tf_ms = tb_ms.text_frame
tf_ms.word_wrap = True
tf_ms.margin_left = tf_ms.margin_right = tf_ms.margin_top = tf_ms.margin_bottom = 0

p_msh = tf_ms.paragraphs[0]
p_msh.text = "Multi-Sea Scenario Validation:"
p_msh.font.name = FONT_HEAD
p_msh.font.size = Pt(9.5)
p_msh.font.bold = True
p_msh.font.color.rgb = C_NAVY
p_msh.space_after = Pt(2)

multi_seas = [
    ("Gulf of Mexico (MC20): ", "57 oil slicks detected (15.6 km²), 11h backward hindcast, ranked culprit vessel (MMSI 311211224) with 46min AIS blackout gap."),
    ("Arabian Sea (Mumbai Offshore): ", "Clean water floor test: 1.56 dB contrast span correctly reports 'Uniform Clean Water' with 0 false slicks."),
    ("Santa Barbara Channel: ", "Natural geological seep discrimination and heavy vessel lane isolation."),
    ("Caspian Sea (Baku Fields): ", "Dense offshore platform cluster stress-test.")
]
for title, desc in multi_seas:
    p = tf_ms.add_paragraph()
    p.space_after = Pt(2.5)
    rb = p.add_run()
    rb.text = "• " + title
    rb.font.name = FONT_BODY
    rb.font.bold = True
    rb.font.size = Pt(8.5)
    rb.font.color.rgb = C_BLUE
    rt = p.add_run()
    rt.text = desc
    rt.font.name = FONT_BODY
    rt.font.size = Pt(8.0)
    rt.font.color.rgb = C_TEXT_MAIN

# Save Presentation in both tidetrace and workspace root
out_pptx_local = Path("TideTrace_SIH26143_Submission_Final.pptx")
out_pptx_root = Path("../TideTrace_SIH26143_Submission_Final.pptx")
prs.save(str(out_pptx_local))
prs.save(str(out_pptx_root))
print(f"Presentation saved successfully to {out_pptx_local.resolve()} and {out_pptx_root.resolve()}")
