"""Turn a GEE reference chip (gee_reference.py) into a regression test case for tests/test_parity.py.

Stores the central 256 x 256 px of the chip (linear VV and VH, float32, exactly as GEE serves them) in
tests/reference/ and adds the matching job (window, time range) to tests/reference/cases.json.

Usage: python make_test_reference.py <scene work dir, e.g. data/s_20180604T052627_9700> [...]
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "tests" / "reference"
SIZE = 256


def main(work_dirs: list) -> None:
    cases_path = REF / "cases.json"
    cases = json.loads(cases_path.read_text()) if cases_path.exists() else {}
    for work in map(Path, work_dirs):
        chips = json.loads((work / "ref" / "gee_chips.json").read_text())
        scene = chips["scene_id"]
        parts = scene.split("_")
        out = REF / f"gee_{parts[0]}_{parts[4]}_{parts[-1]}.tif"
        with rasterio.open(work / "ref" / "gee_pt.tif") as src:
            off = (src.width - SIZE) // 2
            window = Window(off, off, SIZE, SIZE)
            data = src.read([1, 2], window=window).astype("float32")
            transform = src.window_transform(window)
            profile = dict(driver="GTiff", width=SIZE, height=SIZE, count=2, dtype="float32", crs=src.crs,
                           transform=transform, compress="deflate", predictor=3)
            bounds = rasterio.windows.bounds(window, src.transform)
            west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *bounds)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data)
            dst.descriptions = ("VV", "VH")
        start = datetime.strptime(parts[4], "%Y%m%dT%H%M%S")
        pad = 0.004        # degrees; the job window must cover the reference completely
        cases[out.name] = {
            "scene": scene,
            "gee_snap_version": chips["grid"]["properties"].get("SNAP_Graph_Processing_Framework_GPF_vers"),
            "job": {"spatial_extent": {"west": round(west - pad, 4), "south": round(south - pad, 4),
                                       "east": round(east + pad, 4), "north": round(north + pad, 4)},
                    # this scene only: the catalogue can hold a second product of the same pass, cut differently
                    "temporal_extent": [f"{start - timedelta(seconds=1):%Y-%m-%dT%H:%M:%SZ}",
                                        f"{start + timedelta(seconds=1):%Y-%m-%dT%H:%M:%SZ}"]},
        }
        print(f"{out.name}: {scene}")
    cases_path.write_text(json.dumps(cases, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
