# Super-resolution compliance matrix

The NTRO problem statement "Deep Learning Based Super Resolution Mapping (SRM) from
Medium Resolution Satellite Imageries" mapped to the code that answers it and the
evidence you can check without trusting anyone. Every number below is written by
`scripts/evaluate_sr.py` into `data/validation/sr_metrics.json` and copied here;
none is typed by hand.

The earlier oil-spill version of this project is kept in the git tag `oil-spill-final`.

---

## What the statement asks

Turn 10 m Sentinel-2 imagery into sharper, information-rich products finer than
4 m, with a deep learning framework (transformer, generative or CNN based) that
keeps geospatial and spectral consistency. The solution is to cover
pre-processing, training on paired data, accuracy assessment and validation
against high-resolution references, support crop monitoring, urban analysis and
disaster assessment, and account clearly for uncertainty and error.

## The verdict, in one table

| Requirement | Status | Where it stands |
| --- | --- | --- |
| Sentinel-2 10 m in, finer than 4 m out | Met | 4x, so 2.5 m, on an exact grid |
| Deep learning framework | Met, one family | A residual CNN. No transformer or generative model was built |
| Geospatial consistency | Met | Output is a GeoTIFF on the input's own corner and CRS at a quarter of the pixel size |
| Spectral consistency | Met | Averages back to the measured 10 m pixel (error 0.0001 reflectance); spectral angle 1.50 degrees against 1.58 for bicubic |
| Pre-processing | Met | Reflectance scaling, band order, cloud screening, registration, radiometric matching |
| Training on paired datasets | Met | 27 Sentinel-2 and 0.6 m aerial pairs |
| Accuracy assessment | Met | Eight metrics, bootstrap interval over scenes |
| Validation against high-resolution references | Met for the US only | 4 held-out scenes in CO, LA, MN against NAIP aerial imagery. No reference exists for India |
| Crop monitoring, urban analysis, disaster assessment | Partly | NDVI, water and edge outputs are measured on the held-out scenes. No downstream classification or change-detection experiment was run |
| Uncertainty and error accounting | Met | Per-pixel uncertainty map, checked against real error |

---

## Clause by clause

### Sentinel-2 to finer than 4 m

| Requirement | Implementation | How to verify |
| --- | --- | --- |
| 10 m medium resolution input | `app/sr/infer.py:read_scene` reads Sentinel-2 L2A blue, green, red and NIR (B2, B3, B4, B8) from a GeoTIFF, with band order and digital-number offset as settings. | `POST /api/sr/enhance`; a file with the wrong pixel size or band count is refused with a sentence saying why. |
| Output finer than 4 m | `app/sr/model.py` ends in sub-pixel convolution, 4x. | The result document reports `output.pixel_m` of 2.5. |
| Robust to scene size and seams | Tiled inference with overlap, cosine-weighted stitching, optional eight-way flip averaging. | |

### Geospatial and spectral consistency

| Requirement | Implementation | How to verify |
| --- | --- | --- |
| Same place, same projection | `app/sr/infer.py:write_geotiff` keeps the input's CRS and top-left corner and divides the pixel size by 4, so each 10 m pixel is exactly a 4 x 4 block of output pixels. | |
| Spectrally faithful | `app/sr/metrics.py:project_consistent` moves every 4 x 4 block back onto the measured Sentinel-2 value and keeps the detail inside it. | Held-out scenes: mean consistency error 0.0001 reflectance after the step, 0.0021 before it. |
| No spectral distortion | Spectral angle between the output and the aerial reference, per pixel, in degrees. | 1.50 degrees, bicubic 1.58. |
| A fair reference | The aerial image averaged to 10 m disagrees with Sentinel-2 by about 0.05 reflectance (different dates, sun angles, sensors). Training and scoring use the aerial image anchored to the Sentinel-2 pixels, so what is scored is the detail inside each pixel. | `app/sr/data.py` docstring. The unanchored PSNR is also written: 28.85 dB, bicubic 28.88 dB. |

### Pre-processing

| Step | Implementation |
| --- | --- |
| Reflectance and offset | Digital numbers to reflectance; the 1000 offset of processing baseline 04.00 is a setting (`dn_offset`). |
| Cloud and shadow screening | Scene Classification Layer classes 0, 1, 3, 8, 9, 10 and 11 are excluded from pair building; a scene needs under 10 percent cloud. Uploaded images get a warning when more than 5 percent is bright in every band. |
| Registration | Sub-pixel phase correlation (parabolic peak) between the Sentinel-2 image and the aerial image averaged to 10 m; pairs shifting more than 2 px or correlating under 0.70 are dropped. |
| Radiometric matching | Per-band linear fit of the aerial numbers to Sentinel-2 reflectance. |
| Splits | By region: training, validation and test use different states, so scores measure unseen land cover and climate. |

All of it is `scripts/fetch_sr_pairs.py`, which is the only part that needs the network.

### Training on paired data

| Requirement | Implementation | How to verify |
| --- | --- | --- |
| Paired datasets | Sentinel-2 L2A and NAIP 0.6 m aerial scenes, 27 training and 7 validation pairs, each 3.2 km square. | `data/sr/pairs/` (not committed; rebuilt by the fetch script). |
| Model | Residual CNN, 0.69 M parameters: bicubic base plus learned detail, 12 residual blocks, sub-pixel convolution, mean and scale heads. | `app/sr/model.py`. |
| Loss | L1 on reflectance, L1 on gradients, and a Laplace negative log-likelihood for the uncertainty head (the mean is detached so the likelihood cannot bend it). | `app/sr/train.py:loss_fn`. |
| Training run | 250 iterations on a laptop CPU, resumable, best validation checkpoint kept. | `data/sr/train_history.jsonl`. |

### Accuracy assessment and validation against high-resolution references

Scored on **4 test scenes** in **CO, LA, MN**, regions with no scene in
training, against NAIP 0.6 m aerial imagery resampled to 2.5 m.

| Metric | Bicubic | TideTrail | Better when |
| --- | --- | --- | --- |
| PSNR, dB | 37.00 | 37.25 | higher |
| SSIM | 0.905 | 0.910 | higher |
| RMSE, reflectance | 0.0157 | 0.0152 | lower |
| Spectral angle, degrees | 1.58 | 1.50 | lower |
| NDVI error | 0.027 | 0.026 | lower |
| Open-water overlap (IoU) | 0.811 | 0.842 | higher |
| Edge match F1 | 0.415 | 0.450 | higher |

PSNR gain +0.25 dB, 95 percent bootstrap interval +0.09 to +0.40
over scenes; better than bicubic on 4 of 4 scenes by PSNR
and 4 by SSIM.

By land cover:

| Land cover | Scenes | PSNR bicubic | PSNR TideTrail | SSIM bicubic | SSIM TideTrail |
| --- | --- | --- | --- | --- | --- |
| Agriculture | 1 | 42.06 | 42.38 | 0.979 | 0.980 |
| Forest | 1 | 39.26 | 39.38 | 0.939 | 0.941 |
| Urban | 1 | 31.49 | 31.98 | 0.783 | 0.799 |
| Wetland | 1 | 35.18 | 35.25 | 0.917 | 0.919 |

Re-run it: `python scripts/evaluate_sr.py` (add `--tta` for flip averaging).

### Crop monitoring, urban analysis, disaster assessment

| Use | What is provided | What was measured | What was not |
| --- | --- | --- | --- |
| Crop monitoring | NDVI layer at 2.5 m beside the 10 m one in the console (`Vegetation`). | NDVI error against the reference 0.026, bicubic 0.027. | No crop classification or yield experiment. |
| Urban analysis | Sharper building and road edges. | Edge match F1 0.450, bicubic 0.415. | No building extraction experiment. |
| Disaster assessment | Open-water layer (`Water`, NDWI above zero) for flood and shoreline mapping. | Water overlap 0.842, bicubic 0.811. | No flood event was mapped before and after. |

Three Indian scenes (Jaipur, Kochi, Punjab farmland) are bundled for the console.
They show the enhancement and the model's uncertainty. They carry **no accuracy
score**, because no free aerial reference exists for them.

### Uncertainty and error accounting

| Requirement | Implementation | How to verify |
| --- | --- | --- |
| Per-pixel uncertainty | A second network output gives the scale of a Laplace distribution per pixel and band. It is saved as its own GeoTIFF and drawn as a magenta overlay. | `uncertainty.tif` in every run; the console's Uncertainty switch. |
| Is it honest? | Rank correlation between predicted uncertainty and the real absolute error (mean over scenes), and the share of pixels whose real error falls inside the stated 90 percent interval. | Rank correlation 0.24 (pooled 0.51); 90 percent intervals cover 91% of pixels; error is 7.0 times larger where the model is least sure than where it is surest. `figures`: `docs/screenshots/sr_reliability.png`. |
| Error that is not the model's | The registration residual and the date gap between the images are reported per scene in `per_scene` (`naip_date`, `s2_date`) and bound how far any score can be trusted. | `data/validation/sr_metrics.json`. |

---

## What this does not do

1. **No transformer or generative model.** The statement lists them as options.
   This is a CNN. A generative model would add convincing texture, which is the
   failure the consistency step and the uncertainty map exist to catch.
2. **Validated on US land only.** Free 0.6 m aerial references do not cover India.
   Whether the scores carry over to Indian fields, towns and coasts is unmeasured.
   The uncertainty map is the model's own doubt on those scenes, not a measurement.
3. **Sentinel-2 is read from the Microsoft Planetary Computer mirror** of the ESA
   L2A archive to build pairs. It is the same product the Copernicus Data Space
   Browser serves, but the Browser itself is not wired in. An operator can export
   from it and upload the GeoTIFF.
4. **One scene per place and date, 3.2 km square.** The pairs are small to fit a
   laptop. Larger and more varied training data would help.
5. **No downstream task was trained on the output.** The NDVI, water and edge scores
   say the output is closer to the reference than bicubic; they do not prove a
   better crop map or flood map.
