"""Job driver: Google-Earth-Engine-identical Sentinel-1 sigma0 for an area and a date range.

This is what the container runs inside an openEO job (through CWL, see cwl/s1_sigma0_gee.cwl):

    1. find the Sentinel-1 IW GRDH dual-pol (VV+VH) scenes over the area in the Copernicus catalogue
    2. stage the part of each raw scene that the area needs (from the Copernicus `eodata` S3 archive;
       credentials come from the environment, the openEO backend injects them into CWL containers)
    3. run the GEE recipe on the requested window (process_scene.py)
    4. write one Cloud-Optimized GeoTIFF + STAC item per scene and a `collection.json` in the output folder

Usage:
    python run_job.py arguments.json [--out DIR] [--source cdse-s3|planetary-computer] [--cache DIR]

arguments.json:
    {"spatial_extent": {"west": 10.86, "south": 45.17, "east": 10.94, "north": 45.23},
     "temporal_extent": ["2025-06-01", "2025-07-01"],     # end not included; dates or UTC timestamps
     "orbit_direction": "DESCENDING",          # optional: ASCENDING or DESCENDING
     "db": false,                              # optional: true writes dB like COPERNICUS/S1_GRD
     "match": "exact"}                         # optional: "exact" (default) runs each scene on the SNAP version
                                               # GEE used for it; "approximate" runs every scene on SNAP 11
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


def _iso(t: str) -> str:
    """2025-06-01 -> 2025-06-01T00:00:00.000Z; a full timestamp (2025-06-01T05:00:00Z) is kept."""
    return t if "T" in t else f"{t}T00:00:00.000Z"


def drop_products_gee_lacks(scenes: list) -> list:
    """The catalogue holds two products for many passes of 2019 - early 2021 over Europe: a near-real-time
    one (timeliness NRT-3h) and the regular one (Fast-24h), cut at different times. GEE has only the regular
    one, except for near-real-time products it added years later (those are in the version exception list).
    Checked against GEE's scene lists for 7 places x 10 months: 323 products dropped, none of them in GEE."""
    from snap_versions import version_exceptions

    def key(scene):
        parts = scene["acquisition"].split("_")
        return parts[0], parts[7], parts[4], parts[5]          # satellite, datatake, start, end

    regular = [key(s) for s in scenes if s["timeliness"] == "Fast-24h"]
    kept = []
    for scene in scenes:
        sat, datatake, start, end = key(scene)
        twin = any(sat == a and datatake == d and start < e2 and s2 < end for a, d, s2, e2 in regular)
        if scene["timeliness"] != "Fast-24h" and twin and scene["name"] not in version_exceptions():
            print(f"skipped {scene['name']}: near-real-time duplicate that GEE does not hold", flush=True)
            continue
        kept.append(scene)
    return kept


def search_scenes(extent: dict, start: str, end: str, orbit_direction: str = None) -> list:
    """Sentinel-1 IW GRDH VV+VH products intersecting the extent, oldest first, without the duplicates GEE
    does not hold. Returns the original ESA product where the catalogue has it, else the COG repackaging."""
    w, s, e, n = extent["west"], extent["south"], extent["east"], extent["north"]
    polygon = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
    flt = ("Collection/Name eq 'SENTINEL-1' and contains(Name,'_IW_GRDH_1SDV_')"
           f" and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}')"
           f" and ContentDate/Start ge {_iso(start)} and ContentDate/Start lt {_iso(end)}")
    if orbit_direction:
        flt += (" and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'orbitDirection'"
                f" and att/OData.CSC.StringAttribute/Value eq '{orbit_direction.upper()}')")
    products, url, params = [], CATALOGUE, {"$filter": flt, "$orderby": "ContentDate/Start asc", "$top": 200,
                                         "$expand": "Attributes"}
    while url:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        body = r.json()
        products += body["value"]
        url, params = body.get("@odata.nextLink"), None

    by_acquisition = {}
    for p in products:
        name = p["Name"].replace(".SAFE", "")
        is_cog = name.endswith("_COG")
        parts = name.replace("_COG", "").split("_")
        key = "_".join(parts[:8]) if is_cog else name      # a COG copy belongs to the same acquisition
        timeliness = next((a["Value"] for a in p.get("Attributes", []) if a["Name"] == "timeliness"), None)
        entry = {"name": name, "s3_path": p["S3Path"], "is_cog": is_cog, "acquisition": "_".join(parts[:8]),
                 "timeliness": timeliness}
        by_acquisition.setdefault(entry["acquisition"], []).append(entry)
    scenes = []
    for acquisition, entries in sorted(by_acquisition.items()):
        originals = [x for x in entries if not x["is_cog"]]
        scenes += originals if originals else entries[:1]
    return drop_products_gee_lacks(scenes)


def stage_cdse_s3(scene: dict, dest: Path, extent: dict = None) -> Path:
    """Fetch the product from the Copernicus eodata bucket. With an extent only the image rows that window
    needs are fetched (partial_stage.py: about 6% of the 1.8 GB for a 5 km window); without one, or for a
    COG repackaging, the whole product. Endpoint and credentials come from AWS_ENDPOINT_URL_S3 /
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (/ AWS_SESSION_TOKEN)."""
    import boto3

    s3 = boto3.client("s3", endpoint_url=os.environ.get("AWS_ENDPOINT_URL_S3", "https://eodata.dataspace.copernicus.eu"),
                      region_name=os.environ.get("AWS_DEFAULT_REGION", "default"))
    prefix = scene["s3_path"].lstrip("/").removeprefix("eodata/").rstrip("/") + "/"
    safe = dest / (scene["name"] + ".SAFE")
    files = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket="eodata", Prefix=prefix):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix):]
            if rel and not rel.endswith("/"):
                files[rel] = obj["Size"]
    if "manifest.safe" not in files:
        raise FileNotFoundError(f"nothing found at s3://eodata/{prefix}")

    def read(rel: str, start: int, end: int) -> bytes:
        return s3.get_object(Bucket="eodata", Key=prefix + rel, Range=f"bytes={start}-{end - 1}")["Body"].read()

    if extent and not scene["is_cog"]:
        from partial_stage import stage_window

        info = stage_window(files, read, safe, tuple(extent[k] for k in ("west", "south", "east", "north")))
        print(f"staged {info['bytes'] / 1e6:.0f} MB of {info['of'] / 1e6:.0f} MB"
              + (f" (image rows {info['lines'][0]} to {info['lines'][1]})" if info["lines"] else " (whole product)"),
              flush=True)
    else:
        for rel, size in files.items():
            target = safe / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not (target.exists() and target.stat().st_size == size):
                s3.download_file("eodata", prefix + rel, str(target))
    return safe


def stage_planetary_computer(scene: dict, dest: Path, extent: dict = None) -> Path:
    """Public mirror, no account needed; for running outside the Copernicus cloud. Always the whole product
    (its images are compressed in blocks, not stored row by row)."""
    from fetch_grd_from_pc import main as fetch

    fetch(scene["acquisition"], str(dest))
    return next(p for p in dest.glob(scene["acquisition"] + "*.SAFE"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arguments", type=Path)
    ap.add_argument("--out", type=Path, default=Path("."))
    ap.add_argument("--source", default="cdse-s3", choices=["cdse-s3", "planetary-computer"])
    ap.add_argument("--cache", type=Path, help="keep staged scenes here and reuse them (default: delete after use)")
    args = ap.parse_args()
    job = json.loads(args.arguments.read_text())
    extent = job["spatial_extent"]
    start, end = job["temporal_extent"]
    args.out.mkdir(parents=True, exist_ok=True)

    scenes = search_scenes(extent, start, end, job.get("orbit_direction"))
    print(f"{len(scenes)} Sentinel-1 scenes for {extent} {start}..{end}", flush=True)
    work = Path(tempfile.mkdtemp(prefix="s1gee_"))
    failed = []
    for i, scene in enumerate(scenes, 1):
        stage_dir = args.cache or work / "staged"
        stage_dir.mkdir(parents=True, exist_ok=True)
        try:
            cached = list(stage_dir.glob(scene["acquisition"] + "*.SAFE"))
            stage = stage_cdse_s3 if args.source == "cdse-s3" else stage_planetary_computer
            # a cached scene is reused for other windows, so it has to be complete
            safe = cached[0] if cached else stage(scene, stage_dir, None if args.cache else extent)
            cmd = [sys.executable, "-u", str(SCRIPTS / "process_scene.py"), str(safe), str(args.out),
                   "--bbox", *(str(extent[k]) for k in ("west", "south", "east", "north")),
                   "--snap-userdir", str(work / "snap_userdir" / scene["acquisition"].split("_")[4])]
            if job.get("db"):
                cmd.append("--db")
            if job.get("match"):
                cmd += ["--match", job["match"]]
            subprocess.run(cmd, check=True)
            print(f"[{i}/{len(scenes)}] done {scene['name']}", flush=True)
        except Exception as exc:  # one bad scene must not lose the others; the job fails at the end
            failed.append(scene["name"])
            print(f"[{i}/{len(scenes)}] FAILED {scene['name']}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if not args.cache:
                shutil.rmtree(work / "staged", ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    if failed or not scenes:
        sys.exit(f"{len(failed)} of {len(scenes)} scenes failed: {failed}" if scenes else "no scenes found")


if __name__ == "__main__":
    main()
