# s1_cdse_gee

Reproduce Google Earth Engine's Sentinel-1 backscatter (`COPERNICUS/S1_GRD` / `S1_GRD_FLOAT`) outside GEE,
so openEO workflows get the same pixel values.

## Result

With GEE's recipe, the SNAP version GEE used for the scene and the orbit file GEE used, the output is
**bit-identical to GEE**: every float32 value equal, in flat and steep terrain, on water and at scene edges.
GEE moved through seven SNAP versions; status per period (details under "Validation"). Scenes until
12 Jan 2020 run on SNAP 11.0.1 instead of GEE's 6.0.4: that is not bit-identical but within 1 DN, and it
avoids a build that needs 12 GB of memory.

| Scenes sensed | GEE's SNAP | SNAP used here | Scenes bit-identical to GEE |
|---|---|---|---|
| until 12 Jan 2020 | 6.0.4 | 11.0.1 | within 1 DN in 8 of 8: VV 100% of pixels, VH 99.6 - 99.99%, largest difference 5.5 DN (with SNAP 6.0.4 itself: 8 of 8 bit-identical) |
| 13 Jan 2020 - 24 Nov 2020 | 7.0.3 | 7.0.0 | 4 of 4 |
| 25 Nov 2020 - 29 Mar 2021 | 8.0.1 | 8.0.3 | 3 of 3 |
| 30 Mar 2021 - 7 Aug 2023 | 8.0.3 | 8.0.3 | 12 of 16; the four failures are three passes in May - July 2021 |
| 8 Aug 2023 - 7 Nov 2024 | 9.0.0 | 9.0.0 | 10 of 10 |
| 8 Nov 2024 - 3 Apr 2025 | 11.0.0 | 11.0.1 | 4 of 4 |
| 4 Apr 2025 - today | 11.0.1 | 11.0.1 | 13 of 13 |

With the build of GEE's version that is 54 of 58 scenes bit-identical (2018 - 2026, eight regions,
S1A/B/C/D), given the orbit file GEE used. The rule that picks that file without asking GEE is right for
51 of the 55 scenes that have a matching file ("Orbit file choice"). `tests/test_parity.py` runs the whole
job driver for one scene of each period (2018, 2020, 2022, 2023, 2025): the last four are 100%
bit-identical, the 2018 scene has 99.99% of its pixels within 1 DN (99.4% identical).

Known gaps:

- **4 Nov 2022 - early Aug 2023: no orbit files.** ESA's server (step.esa.int) has no restituted orbit files
  for this period, and the ASF, AWS and Copernicus catalogue copies start in 2024 or later. Without the file
  GEE used the result cannot be exact. `process_scene.py` falls back to the precise orbit file there and
  says so; on a June 2023 scene that gives 62% of pixels within 1 DN, 87% within 2, 98.8% within 5 and
  99.8% within 10. The Copernicus `eodata` bucket does not have them either (its `AUX_RESORB` folder holds only Mar - Apr 2025
  and Aug - Nov 2026). Another archive of `S1A_OPER_AUX_RESORB` files for these months would close the gap.
- **May - July 2021: orbit files re-issued.** For three passes (1 May, 2 June, 17 July 2021) no archived
  orbit file reproduces GEE; the best one gives 98%, 87% and 100% of pixels within 1 DN.
- **Which of the two orbit files.** Two restituted orbit files cover every scene. The right one gives 100%
  identical pixels, the other 0% (share of pixels within 1 DN with the wrong file, 49 scenes: 13% to 99%,
  median 65%; median difference 0.002 dB). The choice is predicted by a rule, not known with certainty.
- About 1% of the scenes were processed by GEE with a version other than the one of their date; they are
  listed in `scripts/gee_version_exceptions.json.gz` and handled automatically. 2,890 of them (mostly before
  mid-2018) used 5.0.4, 6.0.0, 6.0.1 or 6.0.3, which are not reproduced here.
- A few products (4 of 1,470 checked, and S1C over Spain in June 2025) exist under one product hash in the
  Copernicus catalogue and another in GEE; these were not compared.

Recipe (`scripts/run_snap.py`), the same in every period:

1. Apply orbit file: restituted
2. Remove GRD border noise (scenes from 12 Jan 2018 on)
3. Thermal noise removal
4. Calibration to sigma0 (linear)
5. Range-Doppler terrain correction: SRTM 1Sec HGT, bilinear DEM and image resampling, 10 m,
   automatic UTM zone (`AUTO:42001`), SNAP default grid origin (`alignToStandardGrid=false`),
   `nodataValueAtSea=false`

`S1_GRD_FLOAT` is that output; `S1_GRD` is exactly `10*log10` of it (no clipping, no quantisation).

Acceptance metric: WorldCereal's 16-bit scaling `DN = 10^((dB + 83) / 20)`; a pixel passes when
`|DN - DN_GEE| <= 1`.

## Two things that break the match

- **Copernicus COG products.** The cloud-optimised repackaging on CDSE (also mirrored on Planetary Computer)
  adds a `COG Conversion` / `Sentinel-1 COGifier 001.00` entry at the top of `manifest.safe`. SNAP reads `001.00`
  as the ESA processor (IPF) version, treats the product as pre-2018 and silently skips the azimuth noise
  vectors in thermal noise removal (and crashes in border noise removal). Effect: median error 0.014 dB (VV)
  and 0.075 dB (VH) in the flat test area, only ~9% of VV pixels within 1 DN.
  `fetch_grd_from_pc.py: unwrap_cog_manifest` removes the wrapper. The measurement TIFFs are also
  ZSTD-compressed, which SNAP cannot read; `decompress_measurements` rewrites them losslessly.
- **SNAP version.** Each GEE scene carries the SNAP version it was processed with (image property
  `SNAP_Graph_Processing_Framework_GPF_vers`), and the numbers only match with a matching build: SNAP 11 on
  a 2021 scene (GEE: 8.0.3) is close in the median (0.007 dB) but only ~25% of VV pixels are within 1 DN
  and dark VH pixels are off by several dB ("Exact or approximate").
  `scripts/snap_versions.py` picks the build per product: by sensing date, plus the list of 30,577 scenes
  that do not follow their date (`scripts/export_gee_version_exceptions.py` reads it from GEE).

## Scripts

| Script | Purpose |
|---|---|
| `scripts/process_scene.py` | Production unit: `.SAFE` in, GEE-identical COG + STAC out (full scene or lon/lat window) |
| `scripts/validate_scene.py` | End-to-end check of one scene against GEE (download, GEE reference, SNAP, compare) |
| `scripts/fetch_grd_from_pc.py` | Download a raw GRD product from Planetary Computer (no account needed) and make it SNAP-readable |
| `scripts/gee_reference.py` | Read GEE pixels on GEE's own grid, no resampling |
| `scripts/run_snap.py` | Build the SNAP graph and run `gpt` (parameter sweeps, diagnostics) |
| `scripts/compare.py` | Per-pixel comparison in WorldCereal DN, split by terrain slope |
| `scripts/orbit_scan.py`, `scripts/orbit_rule_experiment.py` | List the orbit files covering a scene; find which one GEE used |
| `scripts/install_snap_snapshot.py`, `scripts/install_snap_maven.py` | Build an exact older SNAP version (ESA update snapshots; SNAP Maven repository) |
| `scripts/snap_versions.py` | Which SNAP build to use for a product (GEE's version by date, plus the exception list) |
| `scripts/export_gee_version_exceptions.py` | Reads from GEE the scenes whose SNAP version does not follow their date |
| `scripts/sample_scenes.py` | Picks test scenes spread over the years and the globe |
| `scripts/run_job.py` | Job driver the container runs: area + dates in, STAC collection of GEE-identical scenes out |
| `scripts/orbit_rule.py` | Picks the restituted orbit file GEE most likely used (rule per period) |
| `scripts/orbit_rule_fit.py`, `scripts/period_table.py` | Check the orbit rule against all experiment results; write the per-scene table of this README |
| `scripts/single_version_check.py` | Measures how far one SNAP build for all years is from GEE ("approximate" matching) |
| `scripts/make_test_reference.py` | Turns a checked scene into a regression test case |
| `cwl/` | Dockerfile and CWL workflow for running inside an openEO job (not built or run on a cluster yet) |
| `scripts/partial_stage.py` | Fetches only the image rows of a scene that a window needs; checks that the output equals the full scene |
| `tests/test_parity.py` | Regression test: job output must be bit-identical to stored GEE pixels (no GEE account needed) |

Requirements: one SNAP install per period in `tools/` (`snap11` = 11.0.0 installer +
`snap64 --nosplash --nogui --modules --update-all`, `snap9`, `snap803`, `snap7`; see "Older
periods" for how to build them; `$S1GEE_SNAP_HOME/<version>` in a container),
Python with `rasterio numpy requests pyproj affine earthengine-api`, an authenticated Earth Engine account
for the reference pixels only.

```bash
# check a scene against GEE (GEE scene id, then test areas as name:lon:lat)
python scripts/validate_scene.py S1A_IW_GRDH_1SDV_20250609T052724_20250609T052749_059565_076523_C73E \
    flat:10.90:45.20 steep:10.85:46.10

# produce GEE-identical sigma0 for a window
python scripts/process_scene.py data/<product>.SAFE data/out --bbox 10.86 45.17 10.94 45.23
```

## Running inside openEO (Copernicus Data Space)

No change to the openEO backend is needed. The public endpoint `openeo.dataspace.copernicus.eu` offers the
`EOAP-CWL` runtime for `run_udf`: it runs a container described by a CWL file inside the batch job and loads
the STAC collection the container writes as a data cube. The backend injects S3 credentials for the Copernicus
`eodata` archive into the container, so it can read the raw scenes.

1. Put this folder in a public GitHub repository (`data/` and `tools/` are git-ignored).
2. Every push to `main` that changes code runs `.github/workflows/docker.yml`: it builds the container, runs
   `tests/test_parity.py` in it and publishes it as `ghcr.io/uceshaf/s1-gee-parity:main`. A version tag
   (`v0.1.1`) publishes `ghcr.io/uceshaf/s1-gee-parity:<tag>`. Make the package public once.
3. `cwl/s1_sigma0_gee.cwl` names the image (`dockerPull`). It points at `main`; for a fixed version use a
   release tag there and in the URL below.
4. Call it from an openEO job, see `examples/openeo_job.py`:

   ```python
   s1 = connection.datacube_from_process(
       "run_udf", data=None, runtime="EOAP-CWL",
       udf="https://raw.githubusercontent.com/uceshaf/s1_cdse_gee/main/cwl/s1_sigma0_gee.cwl",
       context={"spatial_extent": {"west": 10.86, "south": 45.17, "east": 10.94, "north": 45.23},
                "temporal_extent": ["2025-06-01", "2025-07-01"], "orbit_direction": "DESCENDING",
                "match": "exact"})
   ```

   `"match": "exact"` (default) runs every scene on the SNAP version GEE used for it; `"approximate"` runs
   every scene on SNAP 11 (see "Exact or approximate").

Scene selection: the Copernicus catalogue holds two products for many passes of 2019 - early 2021 over Europe,
a near-real-time one and the regular one, cut at different times. GEE has only the regular one. `run_job.py`
skips the near-real-time duplicates (`drop_products_gee_lacks`); compared with GEE's scene lists for 7 places
and 10 months, 323 products were skipped, none of which GEE holds, and 4 of 1,470 remain different.

Verified here: `run_job.py` end to end on Windows (scene search, reading from `s3://eodata` with personal S3
keys, orbit rule, processing, STAC); the five regression cases pass with `S1GEE_SOURCE=cdse-s3`, and in the
Linux image on GitHub Actions. Not verified yet: a complete job on the CDSE cluster (the credentials the backend
injects, and how the backend's `load_stac` grids the per-scene outputs).

What the CDSE cluster needs (found on 17 Nov 2026 with test jobs; `scripts/submit_probe.py`):
- **A plain image without a build attestation.** `docker build` on GitHub Actions adds a provenance
  attestation by default (an image index with an `unknown/unknown` platform entry). The cluster never starts
  such an image: an 18 MB image packaged that way did not start in 20 minutes, while `alpine` started in
  12 s and cloudinsar's 2.7 GB SNAP image in under 2 minutes. The workflow builds with
  `--provenance=false --sbom=false`.
- **Steps shorter than about 30 minutes.** The backend's CWL runner (calrissian) loses track of a container
  that runs longer and fails the job with `410 Expired: too old resource version`
  ([calrissian #217](https://github.com/Duke-GCB/calrissian/issues/217), fixed upstream but not deployed on
  CDSE in November 2026). Keep jobs to a few scenes until the job is split into one step per scene.
- The backend shows no output of the container. `scripts/submit_probe.py` runs `scripts/cluster_probe.py` in
  the container (environment, network, S3, one scene with time stamps, all within 14 minutes) and returns its
  report as the job result; it cancels the job after 20 minutes.
- Credits seen: about 3 credits per job plus about 0.33 credits per minute that a 12 GB / 2-core container
  step exists. `examples/cost_comparison.py` runs the same window through this container and through
  `sigma0-ellipsoid` (2 minutes, 4 credits for 10 scenes of a 3.4 x 3.4 km window) and records both.

Cost: only the output window is computed, and only the image rows it needs are fetched
(`scripts/partial_stage.py`). A raw scene is two uncompressed images of 880 MB stored row by row; for a
5 x 5 km window 155 MB of the 1,775 MB are read (range requests), and the output is byte for byte the same
as from the complete scene (checked in flat and steep terrain, at four scene edges and with every SNAP
build). Measured from a laptop outside the Copernicus cloud: 46 s per scene for a 2.5 x 2.5 km window,
download included; most of that is SNAP starting up, once per scene. A year over one location is 50 to 240
scenes (both orbit directions; most in Europe), so about 0.6 to 3 hours of one worker per location and year
if the scenes run one after another. That is slower than `sigma0-ellipsoid`. Not done yet: several windows of the same
scene in one SNAP run, and scenes in parallel.

Each scene comes out on the pixel grid GEE stores it on (its UTM zone, 10 m). What a GEE workflow does after
that (reprojection to another grid or resolution, compositing) has to be repeated the same way on the openEO
side to end with the same numbers.

## Validation

Scene `S1A_IW_GRDH_1SDV_20250609T052724_..._C73E` (Lake Garda, descending, GEE on SNAP 11.0.1), SNAP 11.0.1,
1024 x 1024 px areas, VV and VH:

| Area | Terrain | Pixels compared | Bit-identical to GEE |
|---|---|---|---|
| flat | Po plain, median slope 1.6 deg | 1,048,576 | 100% |
| steep | Alps, median slope 32 deg | 1,048,576 | 100% |
| lake | Lake Garda | 1,048,576 | 100% |
| cornerSE, cornerNW | scene corners | 654,317 and 937,656 | 100% |
| edgeE, edgeW | swath edges | 938,694 and 777,823 | 100% |

At one corner 21 pixels are 0 in both products but flagged valid by GEE; treat 0 as no-data on both sides.

`scripts/process_scene.py` with a lon/lat window and SNAP's automatic UTM zone was checked the same way:
whole-pixel offset to the GEE grid, 100% bit-identical.

A full-scene run (no window) gave the same image size and origin as GEE (29245 x 21740 px, origin
465870.0743187864, 5197889.81732168, EPSG:32632), and all seven areas read from that file are again 100%
bit-identical. It took 59 minutes on a 14-thread laptop with 15.5 GB RAM (10 GB Java heap) and wrote a 4.1 GB
LZW-compressed BigTIFF. Processing only a window is much faster (about 25 s for a 10 x 10 km area).

### Other regions and satellites (SNAP 11.0.1 period)

One 1024 x 1024 px area per scene, SNAP 11.0.1:

| Scene | Where | Bit-identical to GEE |
|---|---|---|
| S1C 2026-08-10, descending | Kenya, southern hemisphere (UTM 37S) | 100% |
| S1A 2026-05-08, ascending | Himalayan foothills, median slope 29.5 deg | 100% |
| S1D 2026-07-05, ascending | Iowa cropland | 100% with the second restituted orbit file; with SNAP's default pick only 55% (VV) / 83% (VH) within 1 DN |

### Orbit file choice

Two (sometimes three) restituted orbit files cover every acquisition, and they differ at the centimetre
level. That is enough to move the result by about 0.002 dB (median), which fails the 1 DN test for a large
share of pixels. SNAP uses the first covering file it finds (local folder first, then a server), so GEE's
output depends on which file its SNAP version was handed when GEE processed the scene. To reproduce GEE,
exactly that file has to be in `<SNAP userdir>/auxdata/Orbits/Sentinel-1/RESORB/<sat>/<yyyy>/<mm>/` before
the run. `scripts/process_scene.py` does this with the rule below (`--orbit-file` overrides it).

`scripts/orbit_rule_experiment.py` finds GEE's file per scene by trying each candidate against GEE: where a
file matches, it is 100% bit-identical and the others are 0%. Which file GEE got changes over the years,
most likely with the orbit download code of its SNAP version (`GEE_ORBIT_RULE` in `scripts/orbit_rule.py`):

| Scenes sensed | GEE used |
|---|---|
| until Feb 2018 | the file generated last |
| Mar 2018 - Jul 2018 | the file generated first (two scenes only) |
| Aug 2018 - Dec 2021 | the file generated last |
| Jan 2022 - Jul 2024 | the file generated first |
| from Aug 2024 | the last file generated at least 37 minutes before the product was published in the Copernicus catalogue; if there is none, the first file |

`python scripts/orbit_rule_fit.py` prints every scene with the generation time of its candidates relative
to the publication time and whether the rule picks GEE's file: 51 of 55. The rule was fitted on 42
scenes and then checked on 11 scenes it had not seen (2018 - 2024): right for all 11. Two more scenes
(23 Jan and 22 Feb 2018) then showed that GEE used the last file before March 2018; the first row of the
table was added for them. Limits:

- The switch dates are only known to within a few weeks or months (between 22 Feb and 18 Mar 2018, between
  4 Jun and 5 Aug 2018, between 10 Dec 2021 and 10 Jan 2022, between 25 Jul and 11 Sep 2024).
- The four scenes that break the rule (11 Nov 2021, 16 Sep 2022, 13 Mar 2025, 2 Jun 2026) have no visible
  cause; GEE's processing time is not published. With the wrong file the values are off by about 0.002 dB.
- The certain method remains the per-scene check against GEE.

### Older periods

One 512 x 512 px area per scene (5 x 5 km), every restituted orbit file that covers the scene tried in turn
(`scripts/orbit_rule_experiment.py`; results in `data/orbit_rule_*.json`). "Identical" is the share of VV
pixels whose float32 value equals GEE's, with the best orbit file; the other file gives 0% in every case.

| Scene (sensing start, product hash) | GEE's SNAP | SNAP used | Identical to GEE | Orbit file GEE used (by generation time) |
|---|---|---|---|---|
<!-- period-rows:start -->
| S1B 2018-01-23 05:26 `4B05` | 6.0.4 | 11.0.1 | 100% | second of 2 |
| S1A 2018-02-22 00:05 `72A6` | 6.0.4 | 11.0.1 | 100% | second of 2 |
| S1A 2018-03-18 05:26 `BA9B` | 6.0.4 | 6.0.4 | 100% | first of 2 |
| S1B 2018-06-04 05:26 `9700` | 6.0.4 | 11.0.1 | 100% | first of 2 |
| S1A 2018-08-05 17:06 `BA8D` | 6.0.4 | 6.0.4 | 100% | second of 2 |
| S1A 2018-10-20 00:05 `EBEC` | 6.0.4 | 6.0.4 | 100% | second of 2 |
| S1A 2018-12-12 00:13 `40D4` | 6.0.4 | 6.0.4 | 100% | second of 2 |
| S1A 2019-03-15 12:55 `E79D` | 6.0.4 | 6.0.4 | 100% | second of 2 |
| S1B 2019-06-11 05:26 `D8D0` | 6.0.4 | 11.0.1 | 100% | second of 2 |
| S1A 2019-09-23 08:45 `A0C5` | 6.0.4 | 6.0.4 | 100% | second of 2 |
| S1B 2020-03-16 04:11 `39DD` | 7.0.3 | 7.0.0 | 100% | second of 2 |
| S1A 2020-06-19 19:32 `670B` | 7.0.3 | 7.0.0 | 100% | second of 2 |
| S1B 2020-08-04 05:26 `0BF0` | 7.0.3 | 7.0.0 | 100% | second of 2 |
| S1B 2020-10-02 06:25 `458D` | 7.0.3 | 7.0.0 | 100% | second of 2 |
| S1B 2020-12-20 03:11 `FD09` | 8.0.1 | 8.0.3 | 100% | second of 2 |
| S1A 2021-02-01 05:19 `F70D` | 8.0.1 | 8.0.3 | 100% | second of 2 |
| S1A 2021-02-18 05:27 `7CCC` | 8.0.1 | 8.0.3 | 100% | second of 2 |
| S1A 2021-04-09 12:55 `D67E` | 8.0.3 | 8.0.3 | 100% | second of 2 |
| S1A 2021-05-01 00:05 `4E5E` | 8.0.3 | 8.0.3 | 0% (98% within 1 DN) | none matches |
| S1A 2021-06-02 17:06 `7E0C` | 8.0.3 | 8.0.3 | 0% (87% within 1 DN) | none matches |
| S1A 2021-07-17 00:44 `BBD3` | 8.0.3 | 8.0.3 | 0% (100% within 1 DN) | none matches |
| S1B 2021-08-10 06:25 `AC00` | 8.0.3 | 8.0.3 | 100% | second of 2 |
| S1A 2021-09-10 05:27 `0D32` | 8.0.3 | 8.0.3 | 100% | second of 2 |
| S1A 2021-10-14 00:52 `3AB8` | 8.0.3 | 8.0.3 | 100% | second of 2 |
| S1A 2021-11-11 08:45 `2E0F` | 8.0.3 | 8.0.3 | 100% | first of 2 |
| S1B 2021-12-10 17:14 `D2D5` | 8.0.3 | 8.0.3 | 100% | second of 2 |
| S1A 2022-01-10 08:45 `C3A1` | 8.0.3 | 8.0.3 | 100% | first of 2 |
| S1A 2022-02-04 04:12 `9163` | 8.0.3 | 8.0.3 | 100% | first of 2 |
| S1A 2022-04-17 19:24 `EF1A` | 8.0.3 | 8.0.3 | 100% | first of 2 |
| S1A 2022-06-13 05:27 `8BC2` | 8.0.3 | 8.0.3 | 100% | first of 2 |
| S1A 2022-09-16 06:26 `12BA` | 8.0.3 | 8.0.3 | 100% | second of 2 |
| S1A 2022-10-25 08:45 `AF97` | 8.0.3 | 8.0.3 | 100% | first of 2 |
| S1A 2023-08-22 04:12 `972C` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2023-09-12 05:27 `A187` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2023-09-28 15:48 `BD3F` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2023-12-12 05:19 `52A1` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2024-01-18 19:32 `B54B` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2024-03-10 00:05 `D858` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2024-06-14 05:27 `CCDA` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2024-07-25 00:13 `722E` | 9.0.0 | 9.0.0 | 100% | first of 2 |
| S1A 2024-09-11 00:44 `6B24` | 9.0.0 | 9.0.0 | 100% | second of 2 |
| S1A 2024-10-28 15:48 `E23A` | 9.0.0 | 9.0.0 | 100% | second of 2 |
| S1A 2024-12-13 08:45 `D9F7` | 11.0.0 | 11.0.1 | 100% | first of 2 |
| S1A 2025-01-16 05:27 `92EE` | 11.0.0 | 11.0.1 | 100% | first of 2 |
| S1A 2025-02-03 15:45 `6CB4` | 11.0.0 | 11.0.1 | 100% | first of 2 |
| S1A 2025-03-13 19:32 `05AE` | 11.0.0 | 11.0.1 | 100% | second of 2 |
| S1A 2025-06-09 05:27 `C73E` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1A 2026-05-08 12:47 `E2ED` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1A 2026-06-01 17:48 `D572` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1C 2026-06-02 03:34 `3937` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1A 2026-06-02 09:22 `65C4` | 11.0.1 | 11.0.1 | 100% | second of 2 |
| S1A 2026-06-02 10:12 `02B7` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1C 2026-06-02 15:55 `A172` | 11.0.1 | 11.0.1 | 100% | second of 2 |
| S1D 2026-06-04 09:14 `61DD` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1A 2026-06-06 11:12 `A716` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1A 2026-06-12 16:46 `2F52` | 11.0.1 | 11.0.1 | 100% | first of 2 |
| S1D 2026-06-13 11:12 `C0B2` | 11.0.1 | 11.0.1 | 100% | second of 2 |
| S1D 2026-07-05 00:04 `3A5C` | 11.0.1 | 11.0.1 | 100% | second of 2 |
<!-- period-rows:end -->

Three passes fail, all between May and July 2021: 1 May (98% of VV pixels within 1 DN with the best file),
17 July (100% within 1 DN, but only 0.2% identical) and 2 June (two consecutive scenes). In each case the
covering orbit files were generated out of sequence, which points to files ESA issued again later. For the
2 June pass no orbit file on ESA's server comes close:
67% and 87% of VV pixels within 1 DN with the two restituted files, 75% with the precise
file, 1% without an orbit file. The restituted file covering that pass was generated at 20:45, two hours
after the product was finished and 13 minutes before the next one, which is out of sequence. The likely
explanation is that ESA re-issued the file and GEE had used the first issue, which is no longer archived.
A sweep of the terrain-correction settings on this scene (six DEM resampling methods, four image resampling
methods, SRTM 3Sec) confirms the settings are right: bilinear / bilinear / SRTM 1Sec HGT is by far the
closest, every alternative is 2 to 100 times worse.

Getting the old SNAP builds:

- 7.0, 8.0, 9.0, 11.0: ESA installers (`download.esa.int/step/snap/<v>/installers/`, 6.0 and 7.0 under
  `step.esa.int/downloads/<v>/installers/`).
- 8.0.3: `scripts/install_snap_snapshot.py` applies ESA's dated update snapshot `8.0_20210519-131352`
  (https://step.esa.int/updatecenter/) to a clean 8.0 install. SNAP's own updater only offers 8.0.6.
- 6.0.4 (not used by the job driver; only needed to find GEE's orbit file bit-exactly for scenes before
  2020): `scripts/install_snap_maven.py` replaces the modules of a clean 6.0 install with the 6.0.4 release
  from the SNAP Maven repository (nexus.senbox.net) and needs the extra module `s1tbx-io-ephemeris`.
  The 6.0 installer alone (6.0.0) crashes in border noise removal on 2018 products.

Quirks of the old builds:

- SNAP 6, 7 and 8 cannot download orbit files any more (retired ESA server); the file has to be placed in
  `<userdir>/auxdata/Orbits/...`, which `process_scene.py` does for every version anyway.
- `gpt` 8.0.3 and 9.0.0 do not exit after writing their output on Windows; `run_gpt` stops them.
- SNAP 6.0.4 builds the thermal noise table for the whole scene in memory before the first pixel: about
  7 GB (16668 x 26257 px, two polarisations, 8-byte values) and about 10 minutes, even for a small window.
  With the default Java memory layout a 10 GB heap is not enough and the run dies with a
  `NullPointerException` in `SunCachedTile`. Use `-Xmx11G -XX:NewSize=400m -XX:MaxNewSize=400m` or more.

### Exact or approximate

`"match": "approximate"` (job option; `--match approximate` in `process_scene.py`) runs every scene on SNAP
11.0.1 instead of the SNAP version GEE used: one install instead of four and a smaller image. Measured with
`scripts/single_version_check.py` and `scripts/orbit_rule_experiment.py` with `$SNAP_GPT` set to SNAP 11
(orbit file as GEE, one 5 x 5 km area per scene, difference from GEE):

| Scenes sensed | GEE's SNAP | Scenes | VV within 1 DN | VV median / 99th percentile | VH within 1 DN | VH pixels off by more than 1 dB |
|---|---|---|---|---|---|---|
| until 12 Jan 2020 | 6.0.4 | 8 | 100% | 0 / 0 dB | 99.6 - 99.99% | 0% |
| 13 Jan 2020 - 7 Aug 2023 | 7.0.3, 8.0.1, 8.0.3 | 6 | 22 - 30% | 0.007 / 0.05 - 0.08 dB | 33 - 40% | 4 - 20% on the three scenes examined |
| 8 Aug 2023 - 7 Nov 2024 | 9.0.0 | 1 | 24% | 0.007 / 0.07 dB | 37% | 0.03% |
| from 8 Nov 2024 | 11.0.0, 11.0.1 | 1 | 100% | 0 / 0 dB | 100% | 0% |

For 2020 - 2023 the approximation is not safe: the median difference is small, but dark VH pixels (below
about -22 dB in GEE) come out 4 to 6 dB lower with SNAP 11, because the older versions treat values near
the thermal noise level differently. Use `exact` (the default) for those years.

Before 2020 SNAP 11 is within 1 DN of GEE's SNAP 6.0.4 for all VV pixels and more than 99.6% of VH pixels
(eight scenes of Jan 2018 - Sep 2019 in Italy, Iowa, India and Brazil; largest difference 5.5 DN). The
`exact` mode therefore also runs those years on SNAP 11.0.1. STAC property `s1gee:expected_match` of every
output says which case applies: `bit-identical`, `within 1 DN` or `approximate`.

## Not covered yet

- Latitudes above 60 degrees, where GEE uses the ASTER DEM instead of SRTM.
- EW mode and HH/HV scenes.
- Whatever happens after the per-scene product in a GEE workflow (reprojection to another grid, compositing).
  GEE stores each scene on its own UTM grid; an openEO workflow has to resample from that grid the same way
  the GEE workflow does to end up with the same numbers.
