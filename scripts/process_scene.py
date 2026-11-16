"""Produce Google-Earth-Engine-identical Sentinel-1 backscatter for one GRD scene.

Input : a Sentinel-1 GRD product folder (.SAFE). Copernicus COG-repackaged products are fixed up first.
Output: <out>/<scene>_sigma0.tif  Cloud-Optimized GeoTIFF, band 1 = VV, band 2 = VH, float32,
                                  linear sigma0 (same numbers as COPERNICUS/S1_GRD_FLOAT),
                                  or dB with --db (same numbers as COPERNICUS/S1_GRD); nodata = 0 / NaN
        <out>/<scene>.json        STAC item      } loadable with openEO load_stac
        <out>/collection.json     STAC collection}

The pixel grid is the one GEE stores the scene on: the scene's UTM zone, 10 m, SNAP's default origin.
Do not resample when comparing against GEE.

Usage:
    python process_scene.py <SAFE dir> <out dir> [--bbox WEST SOUTH EAST NORTH] [--db] [--match exact|approximate]
                            [--orbit-file NAME] [--snap-userdir DIR] [--no-border-noise] [--gpt PATH]
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_grd_from_pc import prepare_safe  # noqa: E402
from orbit_rule import candidates, place_orbit_file, publication_time, select_orbit_file  # noqa: E402
from run_snap import build_graph, run_gpt  # noqa: E402
from snap_versions import BIT_IDENTICAL, INSTALL_FOR, gee_version, gpt_for  # noqa: E402

# GEE applies GRD border noise removal to scenes ingested from 12 January 2018 onward.
BORDER_NOISE_SINCE = datetime(2018, 1, 12, tzinfo=timezone.utc)


def scene_times(safe: Path) -> tuple:
    m = re.search(r"_(\d{8}T\d{6})_(\d{8}T\d{6})_", safe.name)
    fmt = "%Y%m%dT%H%M%S"
    return tuple(datetime.strptime(g, fmt).replace(tzinfo=timezone.utc) for g in m.groups())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("safe", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                    help="lon/lat window to compute; default is the full scene")
    ap.add_argument("--db", action="store_true", help="write 10*log10(sigma0) like COPERNICUS/S1_GRD")
    ap.add_argument("--orbit-file", help="name of the restituted orbit file to use; default: the file Google "
                                         "Earth Engine most likely used (timing rule, see orbit_rule.py)")
    ap.add_argument("--snap-userdir", type=Path,
                    help="SNAP user directory private to this scene (default: <out>/snap_userdir/<sensing start>)")
    ap.add_argument("--no-border-noise", action="store_true")
    ap.add_argument("--match", default="exact", choices=["exact", "approximate"],
                    help="exact: the SNAP build that reproduces the version Google Earth Engine used for this "
                         "product; approximate: one SNAP build (11.0.1) for every year")
    ap.add_argument("--gpt", type=Path, help="SNAP gpt executable; default: the SNAP version Google Earth "
                                             "Engine used for this product (snap_versions.py)")
    args = ap.parse_args()
    args.safe, args.out = args.safe.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)

    prepare_safe(args.safe)
    start, end = scene_times(args.safe)
    if start < BORDER_NOISE_SINCE:
        args.no_border_noise = True
    scene = args.safe.name.replace(".SAFE", "")

    # Orbit file: exactly one covering file goes into a SNAP user directory private to this scene,
    # because SNAP takes the first covering file it finds.
    if args.orbit_file:
        kind = "POEORB" if "POEORB" in args.orbit_file else "RESORB"
        orbit = next(c for c in candidates(scene, kind) if c["file"] == args.orbit_file)
        orbit["published"] = publication_time(scene)
    else:
        orbit = select_orbit_file(scene)
        print(f"orbit file by timing rule: {orbit['file']} (product published {orbit['published']}; "
              f"not used: {orbit['alternatives'] or 'none'})")
    userdir = (args.snap_userdir or args.out / "snap_userdir" / scene.split("_")[4]).resolve()
    place_orbit_file(orbit, userdir, scene)
    args.orbit = "restituted" if orbit["kind"] == "RESORB" else "precise"
    if orbit["kind"] != "RESORB":
        print("WARNING: no restituted orbit file is archived for this scene; using the precise orbit file. "
              "The result is close to GEE but not identical.")

    # SNAP build: the one that reproduces the version GEE used for this product
    gee_snap = gee_version(scene)
    snap_used = "given with --gpt"
    if not args.gpt:
        args.gpt, snap_used = gpt_for(gee_snap, args.match)
    print(f"GEE processed this product with SNAP {gee_snap}; using SNAP {snap_used} ({args.gpt})")
    same_build = INSTALL_FOR.get(gee_snap) == snap_used
    identical_expected = orbit["kind"] == "RESORB" and same_build and gee_snap in BIT_IDENTICAL
    if orbit["kind"] != "RESORB" or not same_build:
        expected = "approximate"
    else:
        expected = "bit-identical" if identical_expected else "within 1 DN"

    # fixed parts of the GEE recipe (the same in every SNAP period)
    args.crs = "AUTO:42001"                      # automatic UTM zone, as GEE stores the scene
    args.dem = "SRTM 1Sec HGT"
    args.dem_resampling = args.img_resampling = "BILINEAR_INTERPOLATION"
    args.no_thermal_noise = False

    region = None
    if args.bbox:
        w, s, e, n = args.bbox
        region = f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"

    with tempfile.TemporaryDirectory(dir=args.out) as tmp:
        raw = Path(tmp) / "snap.tif"
        graph = Path(tmp) / "graph.xml"
        graph.write_text(build_graph(args, [("out", region, raw)]))
        code, _ = run_gpt([str(args.gpt), str(graph), f"-J-Dsnap.userdir={userdir}"],
                          args.out / f"{scene}_gpt.log", [raw])
        if code != 0 or not raw.exists():
            sys.exit(f"SNAP failed (exit code {code}); see {scene}_gpt.log")

        with rasterio.open(raw) as src:
            data = src.read().astype("float32")      # band 1 = Sigma0_VV, band 2 = Sigma0_VH
            crs, transform = src.crs, src.transform
            bounds = src.bounds
    nodata = 0.0
    if args.db:
        with np.errstate(divide="ignore", invalid="ignore"):
            data = np.where(data > 0, 10.0 * np.log10(data.astype("float64")), np.nan).astype("float32")
        nodata = float("nan")

    tif = args.out / f"{scene}_sigma0.tif"
    with rasterio.open(tif, "w", driver="COG", width=data.shape[2], height=data.shape[1], count=2,
                       dtype="float32", crs=crs, transform=transform, nodata=nodata,
                       compress="deflate", predictor=3, blocksize=512, overview_resampling="average") as dst:
        dst.write(data)
        dst.descriptions = ("VV", "VH")

    write_stac(args.out, scene, tif, crs, transform, data.shape, bounds, start, end, args.db, orbit["file"],
               gee_snap, snap_used, identical_expected, expected)
    print(f"wrote {tif}  ({data.shape[2]} x {data.shape[1]} px, {crs.to_string()}, origin {transform.c:.4f}, {transform.f:.4f})")


def write_stac(out: Path, scene: str, tif: Path, crs, transform, shape, bounds, start, end, db: bool,
               orbit_file: str, gee_snap: str, snap_used: str, identical_expected: bool, expected: str) -> None:
    west, south, east, north = transform_bounds(crs, "EPSG:4326", *bounds)
    unit = "dB" if db else "linear"
    item = {
        "type": "Feature", "stac_version": "1.0.0", "id": scene, "collection": "s1-grd-sigma0-gee",
        "stac_extensions": ["https://stac-extensions.github.io/projection/v1.1.0/schema.json",
                            "https://stac-extensions.github.io/eo/v1.1.0/schema.json"],
        "bbox": [west, south, east, north],
        "geometry": {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north],
                                                         [west, north], [west, south]]]},
        "properties": {
            "datetime": start.isoformat().replace("+00:00", "Z"),
            "start_datetime": start.isoformat().replace("+00:00", "Z"),
            "end_datetime": end.isoformat().replace("+00:00", "Z"),
            "proj:epsg": crs.to_epsg(),
            "proj:shape": [shape[1], shape[2]],
            "proj:transform": list(transform)[:6],
            "proj:bbox": list(bounds),
            "processing:software": {"ESA SNAP": snap_used},
            "s1gee:gee_snap_version": gee_snap,
            "s1gee:orbit_file": orbit_file,
            "s1gee:identical_to_gee_expected": identical_expected,
            "s1gee:expected_match": expected,          # bit-identical | within 1 DN | approximate
            "description": f"sigma0 ({unit}), Google Earth Engine COPERNICUS/S1_GRD recipe",
        },
        "assets": {"sigma0": {
            "href": tif.name, "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            "roles": ["data"],
            "eo:bands": [{"name": "VV"}, {"name": "VH"}],
        }},
        "links": [{"rel": "collection", "href": "collection.json"}],
    }
    (out / f"{scene}.json").write_text(json.dumps(item, indent=2))

    coll_path = out / "collection.json"
    if coll_path.exists():
        coll = json.loads(coll_path.read_text())
    else:
        coll = {
            "type": "Collection", "stac_version": "1.0.0", "id": "s1-grd-sigma0-gee",
            "description": "Sentinel-1 GRD sigma0 processed with the Google Earth Engine recipe (ESA SNAP)",
            "license": "proprietary",
            "extent": {"spatial": {"bbox": [[west, south, east, north]]},
                       "temporal": {"interval": [[item["properties"]["start_datetime"],
                                                  item["properties"]["end_datetime"]]]}},
            "summaries": {"eo:bands": [{"name": "VV"}, {"name": "VH"}]},
            "links": [],
        }
    href = f"{scene}.json"
    if not any(link.get("href") == href for link in coll["links"]):
        coll["links"].append({"rel": "item", "href": href, "type": "application/geo+json"})
    b = coll["extent"]["spatial"]["bbox"][0]
    coll["extent"]["spatial"]["bbox"][0] = [min(b[0], west), min(b[1], south), max(b[2], east), max(b[3], north)]
    t = coll["extent"]["temporal"]["interval"][0]
    coll["extent"]["temporal"]["interval"][0] = [min(t[0], item["properties"]["start_datetime"]),
                                                 max(t[1], item["properties"]["end_datetime"])]
    coll_path.write_text(json.dumps(coll, indent=2))


if __name__ == "__main__":
    main()
