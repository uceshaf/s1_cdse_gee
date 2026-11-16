"""Fetch Google Earth Engine Sentinel-1 reference chips on GEE's own pixel grid.

For one S1 GRD scene and a list of chip centres (lon, lat), this reads the stored GEE
pixels without any resampling: the request grid is the scene's native grid (same CRS,
same 10 m transform, integer pixel offsets).

Output per chip: a GeoTIFF with bands
    1 VV linear   (COPERNICUS/S1_GRD_FLOAT)
    2 VH linear
    3 VV dB       (COPERNICUS/S1_GRD)
    4 VH dB
    5 valid mask  (1 = GEE has data)
    6 slope in degrees (SRTM, nearest neighbour; only used to split statistics by terrain)
plus a JSON sidecar with the scene grid and chip pixel offsets.

Usage:
    python gee_reference.py <scene id> <out dir> name:lon:lat[:size] [name:lon:lat[:size] ...]
"""
import io
import json
import sys
from pathlib import Path

import ee
import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer

BANDS = ["VV_lin", "VH_lin", "VV_dB", "VH_dB", "valid", "slope"]


def scene_grid(scene_id: str) -> dict:
    info = ee.Image(f"COPERNICUS/S1_GRD_FLOAT/{scene_id}").select("VV").getInfo()
    band = info["bands"][0]
    return {
        "crs": band["crs"],
        "transform": band["crs_transform"],  # [xres, 0, x0, 0, -yres, y0]
        "width": band["dimensions"][0],
        "height": band["dimensions"][1],
        "properties": {k: v for k, v in info["properties"].items() if "vers" in k.lower()},
    }


def fetch_chip(scene_id: str, grid: dict, lon: float, lat: float, size: int) -> tuple:
    xres, _, x0, _, yres, y0 = grid["transform"]
    x, y = Transformer.from_crs("EPSG:4326", grid["crs"], always_xy=True).transform(lon, lat)
    col = int(round((x - x0) / xres)) - size // 2
    row = int(round((y - y0) / yres)) - size // 2
    if not (0 <= col and col + size <= grid["width"] and 0 <= row and row + size <= grid["height"]):
        raise ValueError(f"chip at {lon},{lat} falls outside the scene grid (col={col}, row={row})")
    cx0, cy0 = x0 + col * xres, y0 + row * yres

    flt = ee.Image(f"COPERNICUS/S1_GRD_FLOAT/{scene_id}").select(["VV", "VH"], ["VV_lin", "VH_lin"])
    grd = ee.Image(f"COPERNICUS/S1_GRD/{scene_id}").select(["VV", "VH"], ["VV_dB", "VH_dB"])
    valid = flt.select("VV_lin").mask().rename("valid")
    slope = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003")).rename("slope")
    request_grid = {
        "dimensions": {"width": size, "height": size},
        "affineTransform": {"scaleX": xres, "shearX": 0, "translateX": cx0,
                            "shearY": 0, "scaleY": yres, "translateY": cy0},
        "crsCode": grid["crs"],
    }

    def pixels(image: ee.Image) -> np.ndarray:
        raw = ee.data.computePixels({"expression": image, "fileFormat": "NPY", "grid": request_grid})
        return np.load(io.BytesIO(raw))

    # two requests keep each one under the 48 MB limit; linear stays float32, dB stays double
    part_a = pixels(ee.Image.cat([flt.unmask(-9999), valid, slope.unmask(0)]).toFloat())
    part_b = pixels(grd.unmask(-9999).toDouble())
    data = np.stack([(part_a[b] if b in part_a.dtype.names else part_b[b]).astype("float64") for b in BANDS])
    return data, Affine(xres, 0, cx0, 0, yres, cy0), col, row


def main() -> None:
    scene_id, out_dir = sys.argv[1], Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    ee.Initialize()
    grid = scene_grid(scene_id)
    print("scene grid:", grid["crs"], grid["transform"], grid["width"], "x", grid["height"], grid["properties"])
    meta = {"scene_id": scene_id, "grid": grid, "chips": {}}
    for spec in sys.argv[3:]:
        parts = spec.split(":")
        name, lon, lat = parts[0], float(parts[1]), float(parts[2])
        size = int(parts[3]) if len(parts) > 3 else 1024
        data, transform, col, row = fetch_chip(scene_id, grid, lon, lat, size)
        path = out_dir / f"gee_{name}.tif"
        with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=len(BANDS),
                           dtype="float64", crs=grid["crs"], transform=transform, compress="deflate") as dst:
            dst.write(data)
            dst.descriptions = BANDS
        valid = data[4] > 0
        vv_db = data[2][valid]
        print(f"chip {name}: col={col} row={row} size={size} valid={valid.mean():.1%} "
              f"VV dB median={np.median(vv_db):.2f} slope median={np.median(data[5]):.1f} deg "
              f"p90={np.percentile(data[5], 90):.1f} deg")
        meta["chips"][name] = {"col": col, "row": row, "size": size, "lon": lon, "lat": lat, "file": path.name}
    (out_dir / "gee_chips.json").write_text(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
