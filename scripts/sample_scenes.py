"""Pick test scenes spread over the years and the globe, for orbit_rule_experiment.py.

For each date below, takes the first Sentinel-1 IW VV+VH scene GEE has at one of the test points on or after
that date (points rotate, so every period sees several regions) and prints `scene id:lon:lat` specs for the
scenes whose raw product is on Planetary Computer.

Usage: python sample_scenes.py [scenes per date, default 1] [YYYY-MM-DD ...  (instead of the built-in dates)]
"""
import sys

import ee
import requests

POINTS = [("garda", 10.9, 45.2), ("iowa", -93.5, 42.0), ("india", 77.5, 28.9), ("brazil", -47.9, -15.8),
          ("ukraine", 31.0, 49.5), ("australia", 147.0, -35.0), ("spain", -4.0, 40.0), ("kenya", 37.0, -0.5)]
DATES = ["2018-03-15", "2018-10-15", "2019-03-15", "2019-09-15",                      # GEE on SNAP 6.0.4
         "2020-03-15", "2020-06-15", "2020-10-01",                                    # 7.0.3
         "2020-12-15", "2021-02-01",                                                  # 8.0.1
         "2021-05-01", "2021-07-15", "2021-11-01", "2022-02-01", "2022-04-15", "2022-09-15",   # 8.0.3
         "2023-09-20", "2023-12-10", "2024-03-10", "2024-09-10",                      # 9.0.0
         "2024-12-10", "2025-02-01", "2025-03-10"]                                    # 11.0.0
PC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/"


def main(per_date: int, dates: list) -> None:
    ee.Initialize()
    for i, date in enumerate(dates):
        found = 0
        for j in range(len(POINTS)):
            name, lon, lat = POINTS[(i + j) % len(POINTS)]
            col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(ee.Geometry.Point([lon, lat]))
                   .filterDate(date, ee.Date(date).advance(20, "day"))
                   .filter(ee.Filter.eq("instrumentMode", "IW"))
                   .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
                   .sort("system:time_start").limit(1))
            feats = col.getInfo()["features"]
            if not feats:
                continue
            sid = feats[0]["properties"]["system:index"]
            if not requests.get(PC + sid.rsplit("_", 1)[0], timeout=60).ok:
                continue
            print(f"{sid}:{lon}:{lat}", flush=True)
            print(f"   # {date} {name} SNAP {feats[0]['properties'].get('SNAP_Graph_Processing_Framework_GPF_vers')}",
                  file=sys.stderr, flush=True)
            found += 1
            if found >= per_date:
                break


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1, sys.argv[2:] or DATES)
