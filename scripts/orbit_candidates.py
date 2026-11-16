"""For a GEE Sentinel-1 scene: list the restituted orbit files that cover it and GEE's ingestion time.
Usage: python orbit_candidates.py <GEE scene id> [...]"""
import re
import sys
from datetime import datetime, timedelta

import ee
import requests

BASE = "http://step.esa.int/auxdata/orbits/Sentinel-1/RESORB"
FMT = "%Y%m%dT%H%M%S"
ee.Initialize()
for sid in sys.argv[1:]:
    p = sid.split("_")
    sat, start, end = p[0], datetime.strptime(p[4], FMT), datetime.strptime(p[5], FMT)
    asset = ee.data.getAsset(f"projects/earthengine-public/assets/COPERNICUS/S1_GRD/{sid}")
    print(f"\n{sid}\n  sensed {start:%Y-%m-%d %H:%M:%S}   GEE asset updateTime {asset.get('updateTime')}")
    names = set()
    for day in {start - timedelta(days=1), start, start + timedelta(days=1)}:
        r = requests.get(f"{BASE}/{sat}/{day:%Y}/{day:%m}/", timeout=60)
        if r.ok:
            names |= set(re.findall(rf"{sat}_OPER_AUX_RESORB_OPOD_\d+T\d+_V\d+T\d+_\d+T\d+\.EOF\.zip", r.text))
    for n in sorted(names):
        gen, v0, v1 = re.search(r"OPOD_(\d+T\d+)_V(\d+T\d+)_(\d+T\d+)", n).groups()
        v0, v1 = datetime.strptime(v0, FMT), datetime.strptime(v1, FMT)
        if v0 <= start and end <= v1:
            print(f"  candidate generated {gen}  valid {v0:%d %H:%M:%S} - {v1:%d %H:%M:%S}  "
                  f"(scene starts {(start - v0).total_seconds() / 60:.0f} min after validity start)")
