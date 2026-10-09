/* Super-resolution page. Upload or pick a Sentinel-2 scene, enhance it to 2.5 m,
   compare it with the input, and see what the model is unsure of. Every number
   shown comes from the server's answer or from the saved validation file. */
(function () {
  "use strict";

  var map, overlays = { input: null, sr: null, unc: null };
  var doc = null, mode = "compare", kind = "colour";
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function fmt(v, d) { return v == null || isNaN(v) ? "n/a" : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v, d) { return v == null ? "n/a" : (100 * v).toFixed(d == null ? 0 : d) + "%"; }

  function notice(kind, text) {
    $("notices").appendChild(el("div", "notice " + kind, text));
  }

  /* ----------------------------------------------------------------- theme */
  $("theme").addEventListener("click", function () {
    var cur = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = cur;
    try { localStorage.setItem("tidetrail-theme-v2", cur); } catch (e) { /* not saved */ }
  });

  /* ------------------------------------------------------------------- map */
  function initMap() {
    map = L.map("map", { zoomControl: false, attributionControl: false, zoomSnap: 0.25, minZoom: 2, maxZoom: 22 });
    L.control.zoom({ position: "topright" }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);
    map.setView([20, 78], 4);
    map.on("mousemove", function (e) {
      $("readout").textContent = "lat " + e.latlng.lat.toFixed(5) + "  lon " + e.latlng.lng.toFixed(5);
    });
    map.on("move zoom viewreset resize moveend zoomend", applyClip);
  }

  function setOverlays(d) {
    ["input", "sr", "unc"].forEach(function (k) { if (overlays[k]) { map.removeLayer(overlays[k]); overlays[k] = null; } });
    var b = d.bounds;
    var pv = d.previews[kind];
    overlays.input = L.imageOverlay(pv.input, b, { className: "px", zIndex: 1, interactive: false }).addTo(map);
    overlays.sr = L.imageOverlay(pv.sr, b, { zIndex: 2, interactive: false }).addTo(map);
    overlays.unc = L.imageOverlay(d.previews.uncertainty, b, { zIndex: 3, interactive: false, opacity: 0.65 }).addTo(map);
    map.fitBounds(b, { padding: [24, 24], animate: false });
    ["input", "sr", "compare"].forEach(function (m) { document.querySelector('.mode[data-mode="' + m + '"]').disabled = false; });
    Array.prototype.forEach.call(document.querySelectorAll(".kind"), function (k) { k.disabled = false; });
    $("unc").disabled = false;
    $("unc-op").disabled = false;
    setMode(mode);
  }

  function setMode(m) {
    mode = m;
    Array.prototype.forEach.call(document.querySelectorAll(".mode"), function (b) {
      b.setAttribute("aria-selected", b.dataset.mode === m ? "true" : "false");
    });
    if (!overlays.sr) return;
    var show = function (layer, on) { var e = layer.getElement(); if (e) e.style.display = on ? "" : "none"; };
    show(overlays.input, m === "input" || m === "compare");
    show(overlays.sr, m === "sr" || m === "compare");
    overlays.sr.getElement().style.clip = "";
    $("swipe").hidden = m !== "compare";
    show(overlays.unc, $("unc").checked);
    overlays.unc.setOpacity($("unc-op").value / 100);
    applyClip();
  }

  /* In compare mode the 2.5 m layer is cut at a vertical line, in the map's own
     coordinates, so the 10 m image shows to the left of it and 2.5 m to the right. */
  function applyClip() {
    if (mode !== "compare" || !overlays.sr || !overlays.sr.getElement()) return;
    var size = map.getSize();
    var x = size.x * $("swipe-range").value / 1000;
    var nw = map.containerPointToLayerPoint([0, 0]);
    var se = map.containerPointToLayerPoint(size);
    var tl = map.latLngToLayerPoint(overlays.sr.getBounds().getNorthWest());
    overlays.sr.getElement().style.clip = "rect(" + [nw.y - tl.y, se.x - tl.x, se.y - tl.y, nw.x + x - tl.x].join("px,") + "px)";
    var line = document.querySelector(".swipe-line"), grip = document.querySelector(".swipe-grip");
    line.style.left = x + "px";
    grip.style.left = x + "px";
  }

  /* Colour, vegetation or water: the same two images, read through a different band combination. */
  function setKind(k) {
    kind = k;
    Array.prototype.forEach.call(document.querySelectorAll(".kind"), function (b) {
      b.setAttribute("aria-pressed", b.dataset.kind === k ? "true" : "false");
    });
    if (!doc || !overlays.sr) return;
    overlays.input.setUrl(doc.previews[k].input);
    overlays.sr.setUrl(doc.previews[k].sr);
    setTimeout(function () { setMode(mode); }, 0);
  }
  document.querySelectorAll(".kind").forEach(function (b) { b.addEventListener("click", function () { setKind(b.dataset.kind); }); });

  document.querySelectorAll(".mode").forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); }); });
  $("swipe-range").addEventListener("input", applyClip);
  $("unc").addEventListener("change", function () { setMode(mode); });
  $("unc-op").addEventListener("input", function () { if (overlays.unc) overlays.unc.setOpacity(this.value / 100); });

  /* -------------------------------------------------------------- the run */
  var timer = null;

  function busy(on, text) {
    $("busy").classList.toggle("on", on);
    if (text) $("busytext").textContent = text;
  }

  function watch(jobId) {
    clearInterval(timer);
    timer = setInterval(function () {
      fetch("/api/sr/jobs/" + jobId + "/progress").then(function (r) { return r.json(); }).then(function (p) {
        if (p.state === "running" && p.total > 1) $("busytext").textContent = "Enhancing: tile " + p.done + " of " + p.total;
      }).catch(function () { /* the run's own answer reports failure */ });
    }, 500);
  }

  function post(url, form) {
    var jobId = "sr_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    form.append("job_id", jobId);
    $("notices").innerHTML = "";
    busy(true, "Enhancing");
    watch(jobId);
    return fetch(url, { method: "POST", body: form }).then(function (r) {
      return r.text().then(function (t) {
        var body;
        try { body = JSON.parse(t); } catch (e) { body = { detail: t }; }
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : r.status + " error");
        return body;
      });
    }).then(show).catch(function (err) {
      notice("bad", err.message);
    }).then(function () {
      clearInterval(timer);
      busy(false);
    });
  }

  $("form-sr").addEventListener("submit", function (e) {
    e.preventDefault();
    var f = $("file").files[0];
    if (!f) { notice("bad", "Choose a Sentinel-2 GeoTIFF first."); return; }
    var form = new FormData($("form-sr"));
    form.set("tta", $("form-sr").elements.tta.checked ? "true" : "false");
    form.set("project", $("form-sr").elements.project.checked ? "true" : "false");
    post("/api/sr/enhance", form);
  });

  function loadDemos() {
    fetch("/api/sr/demos").then(function (r) { return r.json(); }).then(function (list) {
      var box = $("demos");
      box.innerHTML = "";
      if (!list.length) { box.appendChild(el("p", "hint", "No bundled scenes. Upload your own image below.")); return; }
      list.forEach(function (d) {
        var row = el("button", "scene-row");
        row.type = "button";
        row.appendChild(el("span", "nm", d.title));
        if (d.has_reference) row.appendChild(el("span", "ais ref", "has aerial reference"));
        row.appendChild(el("span", "dt", [d.date, d.note].filter(Boolean).join(" · ")));
        row.addEventListener("click", function () {
          var form = new FormData();
          form.append("project", "true");
          post("/api/sr/demo/" + encodeURIComponent(d.name), form);
        });
        box.appendChild(row);
      });
    }).catch(function () { $("demos").textContent = "Could not list the bundled scenes."; });
  }

  /* ------------------------------------------------------------- the result */
  function factRow(parent, k, v) {
    var c = el("div", "fact");
    c.appendChild(el("span", "fact-k", k));
    c.appendChild(el("span", "fact-v", v));
    parent.appendChild(c);
  }

  function metric(parent, k, v, base, good) {
    var r = el("div", "metric");
    r.appendChild(el("span", "k", k));
    if (base != null) r.appendChild(el("span", "v base", base));
    r.appendChild(el("span", "v" + (good ? " good" : ""), v));
    parent.appendChild(r);
  }

  function show(d) {
    doc = d;
    setOverlays(d);
    $("title").textContent = d.name;
    $("meta").textContent = d.input.width + " x " + d.input.height + " px at " + d.input.pixel_m + " m  to  " +
      d.output.width + " x " + d.output.height + " px at " + d.output.pixel_m + " m  ·  " + d.seconds + " s";

    var q = d.quality;
    var f = $("finding");
    f.innerHTML = "";
    f.appendChild(el("span", "finding-k", "Enhanced " + d.input.pixel_m + " m to " + d.output.pixel_m + " m"));
    var sure = q.high_uncertainty_share < 0.005
      ? "The model is about equally sure everywhere in this scene."
      : "Uncertainty is more than twice the scene median over " + pct(q.high_uncertainty_share) +
        " of the pixels, drawn in magenta when Uncertainty is on.";
    f.appendChild(document.createTextNode(
      "Each 10 m pixel became sixteen. Averaged back to 10 m, the result is within " + fmt(q.consistency_pct, 1) +
      "% of what the satellite measured. " + sure));

    var facts = $("facts");
    facts.innerHTML = "";
    factRow(facts, "Input pixel", d.input.pixel_m + " m");
    factRow(facts, "Output pixel", d.output.pixel_m + " m");
    factRow(facts, "Output size", d.output.width + " x " + d.output.height);
    factRow(facts, "Enhancement", d.output.scale + "x, 4 bands");

    var t = $("trust");
    t.innerHTML = "";
    metric(t, "Matches the input pixels (RMSE)", fmt(q.consistency_rmse, 4), fmt(q.consistency_rmse_raw, 4) + " before", true);
    metric(t, "Average model uncertainty", fmt(q.mean_uncertainty, 4), null);
    metric(t, "Uncertainty as share of brightness", fmt(q.uncertainty_pct, 1) + "%", null);
    metric(t, "Mean NDVI, input and result", fmt(q.ndvi_mean_output, 3), fmt(q.ndvi_mean_input, 3));
    var bar = el("div", "bar-unc");
    var fill = el("i");
    fill.style.width = Math.min(100, q.uncertainty_pct * 4) + "%";
    bar.appendChild(fill);
    t.appendChild(bar);
    t.appendChild(el("p", "hint metric-note", "Uncertainty is the model's own estimate of its error. It is checked against " +
      "real errors on held-out regions below; it is not a guarantee, and it is only meaningful for 10 m Sentinel-2."));
    $("trust-count").textContent = d.settings.consistency_projection ? "pixels kept" : "raw model";

    var dl = $("downloads");
    dl.innerHTML = "";
    dl.className = "dl";
    [["Super-resolved GeoTIFF", d.downloads.sr, "2.5 m, reflectance x 10000"],
     ["Uncertainty GeoTIFF", d.downloads.uncertainty, "same units, per band"]].forEach(function (x) {
      var a = el("a");
      a.href = x[1];
      a.appendChild(document.createTextNode(x[0]));
      a.appendChild(el("span", null, x[2]));
      dl.appendChild(a);
    });

    (d.notes || []).forEach(function (n) { notice(/cloud/.test(n) ? "warn" : "ok", n); });
  }

  /* ----------------------------------------------------------- measured accuracy */
  function loadValidation() {
    fetch("/api/sr/validation").then(function (r) { return r.json(); }).then(function (v) {
      var box = $("validation");
      box.innerHTML = "";
      var m = v.metrics;
      if (!m) {
        box.appendChild(el("p", "hint", v.model_present
          ? "Not measured yet. Run python scripts/evaluate_sr.py to score the model on held-out regions."
          : "No trained model yet. Run python -m app.sr.train."));
        return;
      }
      $("val-count").textContent = m.scenes + " scenes";
      var head = el("div", "metric-head");
      head.appendChild(el("span", null, m.split + " split, " + m.regions.join(" ")));
      head.appendChild(el("span", null, "Bicubic"));
      head.appendChild(el("span", null, "Model"));
      box.appendChild(head);
      var b = m.summary.bicubic, s = m.summary.model_projected;
      [["PSNR, dB (higher)", "psnr", 2], ["SSIM (higher)", "ssim", 3], ["Spectral angle, deg (lower)", "sam", 2],
       ["NDVI error (lower)", "ndvi_mae", 3], ["Water edge overlap (higher)", "water_iou", 3], ["Edge match F1 (higher)", "edge_f1", 3]]
        .forEach(function (r) { if (b[r[1]] != null) metric(box, r[0], fmt(s[r[1]], r[2]), fmt(b[r[1]], r[2]), true); });
      var u = m.uncertainty;
      metric(box, "Uncertainty tracks real error", fmt(u.rank_corr, 2), null);
      metric(box, "90% interval covers", pct(u.coverage_90), null);
      box.appendChild(el("p", "hint metric-note", "Scored against " + m.output_resolution_m + " m aerial imagery in " +
        "US regions the model never saw in training, improved on " + m.scenes_improved.psnr + " of " + m.scenes +
        " scenes. Accuracy elsewhere, India included, has not been measured: no free aerial reference exists there."));
    }).catch(function () { $("validation").textContent = "Validation file could not be read."; });
  }

  initMap();
  loadDemos();
  loadValidation();
})();
