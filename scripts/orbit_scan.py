"""Metadata-only scan: for recent GEE Sentinel-1 scenes at several points, list the covering restituted
orbit files and when each was generated relative to the ESA product completion time.
Usage: python orbit_scan.py <start> <end> lon:lat [lon:lat ...]"""
import sys
from datetime import datetime, timezone

import ee

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from orbit_rule import ORBIT_SERVER as BASE, candidates  # noqa: E402,F401

if __name__ == "__main__":
    ee.Initialize()
    start, end = sys.argv[1], sys.argv[2]
    for pt in sys.argv[3:]:
        lon, lat = map(float, pt.split(":"))
        col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(ee.Geometry.Point([lon, lat])).filterDate(start, end)
               .filter(ee.Filter.eq("instrumentMode", "IW")).filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
               .sort("system:time_start").limit(4))
        for f in col.getInfo()["features"]:
            pr = f["properties"]
            fin = datetime.fromtimestamp(pr["GRD_Post_Processing_stop"] / 1e3, tz=timezone.utc).replace(tzinfo=None)
            cands = candidates(pr["system:index"])
            desc = "  ".join(f"[gen {c['generated']:%H:%M} = finish{(c['generated'] - fin).total_seconds() / 60:+.0f}min, from {c['valid_from']:%H:%M}]" for c in cands)
            print(f"{pr['system:index']} {pt} snap {pr.get('SNAP_Graph_Processing_Framework_GPF_vers')} finish {fin:%m-%d %H:%M} | {desc}")
