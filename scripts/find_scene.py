"""List GEE Sentinel-1 scenes at a point and whether Planetary Computer has the raw product.
Usage: python find_scene.py <lon> <lat> <start date> <end date>"""
import sys
import ee
import requests

lon, lat, start, end = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4]
ee.Initialize()
col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(ee.Geometry.Point([lon, lat]))
       .filterDate(start, end).filter(ee.Filter.eq("instrumentMode", "IW")).sort("system:time_start").limit(6))
for f in col.getInfo()["features"]:
    p, sid = f["properties"], f["properties"]["system:index"]
    r = requests.get("https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/"
                     + sid.rsplit("_", 1)[0], timeout=60)
    pc = r.json()["assets"]["safe-manifest"]["href"].rsplit("/", 2)[1] if r.ok else "not on PC"
    print(sid, "| SNAP", p.get("SNAP_Graph_Processing_Framework_GPF_vers"), "|", p.get("orbitProperties_pass"),
          p.get("transmitterReceiverPolarisation"), "| crs", f["bands"][0]["crs"], "| PC:", pc[-9:])
