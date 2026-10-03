# TideTrail: 10-Minute Pitch and Live Demo Script

**Problem statement:** Leveraging satellite imagery to determine oil spills at sea along with AIS data correlations to identify the vessel responsible for the spill. **Organisation:** NTRO. **Category:** Software.

**Format:** about 6.5 minutes of slides, 2.5 minutes of live console, 1 minute to close, then judge questions.

Every number below is one the prototype shows on screen or writes to disk. If a judge asks where a number comes from, the answer is the file named next to it.

---

## Timeline

| Time | Slide / section | What the judges should take away |
| --- | --- | --- |
| 0:00 to 1:00 | Slide 1, title | The problem: a slick is seen hours after the ship has gone, and often nobody is held responsible |
| 1:00 to 2:45 | Slide 2, solution | Three clauses answered, and the one number that proves attribution works |
| 2:45 to 4:15 | Slide 3, technical approach | The pipeline, the model accuracy, the physics |
| 4:15 to 6:45 | Live console | A real Sentinel-1 pass, real AIS, a 3-ship shortlist |
| 6:45 to 8:00 | Slide 4, feasibility and risks | Runs offline on a laptop; what we do about look-alikes, dark ships, drift error |
| 8:00 to 9:00 | Slide 5, impact | Who uses it and for what |
| 9:00 to 10:00 | Slide 6, references and close | Built on published methods and open data |

---

## Script

### 0:00 to 1:00 · Slide 1

> "Respected members of the jury.
>
> A slick on a radar image is seen hours after it was released. By then the wind and the current have moved it kilometres from where it went into the water, and the ship that released it has sailed on, sometimes with its AIS transponder switched off. Matching the slick to the ship by hand means days of work, and often it is never done.
>
> Problem statement 26143 asks for an automated pipeline that detects the slick, traces it back to where and when it was released, forecasts where it goes next, and ranks the vessels that could be responsible.
>
> TideTrail does all four, offline, on a laptop."

### 1:00 to 2:45 · Slide 2

> "On the left, how it answers the problem statement.
>
> It links the slick the radar sees to every vessel and installation that could have released it, and tests each one against the drift physics. It flags AIS silences of 30 minutes or more near the release zone, and it finds ships on the radar image itself that broadcast no AIS at all.
>
> And it is measured, not claimed. We took real ships from real recorded AIS, released oil along their real tracks through the real currents, and asked TideTrail to find them blind, among a median of 20 real vessels. In 48 such runs the ship that released the oil was on our three-ship shortlist 37 times. Picking three at random would manage about 7.
>
> On the right, what is new. The forward source test: instead of only asking which ship was near the estimated origin, we release oil from every candidate ship along its own track, and from every nearby platform, drift it to the radar time, and see which one reproduces the slick. That moved the true ship to first place in 23 of the 48 runs, against 14 for proximity scoring alone.
>
> A clean-water floor: a scene with under 3 dB of structure is reported as clean water rather than forced to contain a slick. And a radar against AIS cross-check of every ship echo in the image."

### 2:45 to 4:15 · Slide 3

> "Left to right.
>
> Inputs: Sentinel-1 or any RISAT-class SAR GeoTIFF, optical imagery, AIS in the MarineCadastre format the problem statement points to, ERA5 wind and 1/12 degree surface currents, and a coastline.
>
> Detect: a UNet++ with an EfficientNet-B0 encoder, trained on the Zenodo Sentinel-1 oil spill dataset. Intersection over union for oil is 0.89 on the held-out tiles. It masks land and the image edge, then measures area, length, width and orientation.
>
> Trace: 50 particles seeded inside the slick, integrated with a second-order Runge-Kutta step through the wind and currents, backwards to a release zone and time window, and forwards 36 hours. Oil that reaches the coast stops there, so the forecast reports how much beaches and when.
>
> Attribute: AIS is filtered to the release zone and to wherever the drifting oil was, berthed boats are dropped, and each vessel is scored on proximity, timing, trajectory, behaviour and type.
>
> Verify: the forward source test ranks them into the shortlist. Output: the console, a PDF and HTML note with SHA-256 digests, GeoJSON, and a REST API."

### 4:15 to 6:45 · Live console

Before the session: server running, Gulf of Mexico scene selected, browser at full screen.

> "This is the console running on this laptop, with no network.
>
> This is a real Sentinel-1 pass over the MC20 site in the Gulf of Mexico, 24 September 2023, with the real recorded AIS for that day."

*Click Run analysis. It takes about a minute; narrate the progress steps as they appear.*

> "Detection outlines 28 slicks, about 5 square kilometres. The drift runs back to a release zone around 15:00 UTC the previous afternoon, which you see as the ochre zone.
>
> Of 108 vessels in the area, 16 were near that zone or along the drifting oil. Each was tested by releasing oil along its own track. The finding names three, BOSSMAN, PATRICIA DE-ANNE and RAPID RUNNER II, as the ships whose oil best reproduces the slick, and says plainly that this is a shortlist to inspect, not a verdict."

*Drag the time slider back, then forward past the radar pass.*

> "The slider replays the vessels and the oil hour by hour, back to the release window and forward 36 hours."

*Open the Vessels view.*

> "Every vessel carries its reasons: how close, when, its course against the slick, any AIS silence. Above them, every bright ship echo on the radar is checked against AIS at the moment of the pass."

*Select Santa Barbara, and open its saved run from the history if time is short.*

> "Off Santa Barbara the slick is in the channel where the seeps are. The system lists one vessel and, beside it, the two documented fixed sources, Platform Holly and the Coal Oil Point seep field, which fit almost as well. It does not hand the blame to the nearest ship."

*Select Kochi.*

> "And this is the pass three days after the MSC ELSA 3 sank off Kochi. Monsoon sea, 2.4 dB of structure: the system reports clean water instead of inventing a slick."

*Method view, then Export the PDF note.*

> "The Method view shows these validation numbers, the scoring rules and the timings. The note exports as PDF or HTML with digests to check later copies against."

### 6:45 to 8:00 · Slide 4

> "Feasibility. About a minute per scene on a laptop CPU, with no GPU and no network. The data is open: Sentinel-1, Sentinel-2, ERA5, Copernicus currents, and AIS either from MarineCadastre or from NTRO's own feed loaded as CSV. Analysts can upload their own radar pass, AIS file and optical image from the console.
>
> Three risks. Look-alikes, calm water and natural films that also look dark: the model was trained with look-alike chips as negatives, and the clean-water floor and land and edge masks catch the rest. Ships that switch AIS off: the AIS gap check, plus radar echoes with no AIS. Drift uncertainty: an ensemble rather than a single line, and a shortlist rather than a single name."

### 8:00 to 9:00 · Slide 5

> "NTRO gets satellite-to-AIS screening for the EEZ. The Coast Guard gets a release zone and a forecast that narrow where to look and where oil will land. DG Shipping and the ports get a documented note to support an inspection. NDMA gets the landfall forecast to stage booms before oil reaches the coast."

### 9:00 to 10:00 · Slide 6 and close

> "The methods are published ones: Alpers on radar oil detection, UNet++ for segmentation, NOAA GNOME and OpenDrift for drift, the Zenodo benchmark for training.
>
> To close. TideTrail detects the slick, traces it, forecasts it, and narrows a sea of ships to three to inspect, and it says how often that shortlist has been right. Thank you."

---

## Likely questions

**How accurate is the detector?**
Oil IoU 0.889 on the Zenodo validation tiles; 1.1 percent of clean-sea pixels are misread as oil. File: `models/oil_unet_best.report.json`.

**How do you know the attribution works?**
Known-answer runs on real data: a real ship's real track, real currents, the unchanged pipeline searching blind among real traffic. 48 held-out runs: the true ship first in 23, on the three-ship shortlist in 37, in the top ten in 47. The seeds used to design the ranking were not used to score it. Reproduce with `python scripts/validate_attribution.py --cases 48`.

**Why a shortlist and not the culprit?**
With twenty ships in the water, an innocent ship's track often runs along the same drift line. In our runs, naming the single best fit was wrong about as often as right. Naming three and saying how often the guilty one is among them is the honest answer, and it is what an inspection needs.

**How do you separate oil from look-alikes such as algae or low-wind patches?**
The model is a binary oil segmenter trained with look-alike chips as negatives, which is why only 1.1 percent of clean sea is misread. A scene with under 3 dB of structure is reported as clean water, land and the image edge are masked, and the optical chip, when there is one, is used as a cross-check. A biogenic film can still be mistaken for oil, which is why the output is a lead for an analyst, not a finding.

**What about ships that switch off AIS?**
An AIS silence of 30 minutes or more whose dead-reckoned path passes the release zone scores 0.95 on behaviour. Independently, every bright ship echo on the radar is compared with AIS at the moment of the pass; an echo with no AIS is listed as a vessel with its transponder off or an unmapped structure.

**Why are some scenes' AIS simulated?**
Public AIS exists for US waters (MarineCadastre); Indian AIS is not public. The two US scenes use real recorded AIS. Two other scenes use a simulator over the real geography and time, labelled as simulated everywhere it appears, as the problem statement allows. NTRO's own AIS loads through the AIS file upload.

**Does it work with RISAT?**
It takes any georeferenced Sigma0 GeoTIFF at 5 to 50 m. RISAT-1A and EOS-04 are C-band like Sentinel-1, so the model should transfer, but it has not been validated on RISAT imagery yet.

**Is it court-ready?**
No, and we do not claim that. The note is tamper-evident (SHA-256 digests of the radar scene and of the run record) and states its method and limits. It supports an investigation; it is not proof.

**What would deployment need?**
Access control in front of the server, a live AIS feed in place of CSV files, scheduled wind and current downloads, and validation on RISAT. The pipeline itself needs no change.
