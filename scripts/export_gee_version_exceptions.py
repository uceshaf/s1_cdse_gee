"""List the GEE Sentinel-1 scenes whose SNAP version is not the one their sensing date implies.

GEE processed most scenes with the SNAP version current on the sensing date (snap_versions.GEE_VERSIONS),
but not all: products ESA re-published later, backfills and the days of a version switch differ. This
script reads the GEE image property for the whole COPERNICUS/S1_GRD archive (IW mode) and writes the
exceptions to scripts/gee_version_exceptions.json.gz as {scene id: version}. Versions that share a SNAP
install (snap_versions.INSTALL_FOR) are not exceptions of each other.

Usage: python export_gee_version_exceptions.py [first month YYYY-MM] [last month YYYY-MM]
"""
import gzip
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import ee

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snap_versions import GEE_VERSIONS, INSTALL_FOR  # noqa: E402

PROP = "SNAP_Graph_Processing_Framework_GPF_vers"
OUT = Path(__file__).resolve().parent / "gee_version_exceptions.json.gz"


def periods(first: datetime, last: datetime) -> list:
    """(start, end, expected version) pieces: whole months, split where the expected version changes."""
    cuts = sorted({d for d, _ in GEE_VERSIONS if first < d < last})
    out, y, m = [], first.year, first.month
    while datetime(y, m, 1) <= last:
        start = datetime(y, m, 1)
        end = datetime(y + (m == 12), m % 12 + 1, 1)
        edges = [start] + [c for c in cuts if start < c < end] + [end]
        for a, b in zip(edges, edges[1:]):
            expected = [v for d, v in GEE_VERSIONS if d <= a][-1]
            out.append((a, b, expected))
        y, m = end.year, end.month
    return out


def fetch(period: tuple) -> dict:
    a, b, expected = period
    same_install = [v for v, inst in INSTALL_FOR.items() if inst == INSTALL_FOR[expected]]
    col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterDate(a.isoformat(), b.isoformat())
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.inList(PROP, same_install).Not()))
    for attempt in range(4):
        try:
            rows = col.reduceColumns(ee.Reducer.toList(2), ["system:index", PROP]).get("list").getInfo()
            return dict(rows)
        except Exception as exc:  # GEE is occasionally busy; retry
            error = exc
    raise error


def main(first: str, last: str) -> None:
    ee.Initialize()
    todo = periods(datetime.strptime(first, "%Y-%m"), datetime.strptime(last, "%Y-%m"))
    exceptions = {}
    with ThreadPoolExecutor(6) as pool:
        for (a, b, expected), found in zip(todo, pool.map(fetch, todo)):
            exceptions.update(found)
            if found:
                counts = {}
                for v in found.values():
                    counts[v] = counts.get(v, 0) + 1
                print(f"{a:%Y-%m-%d}..{b:%Y-%m-%d} expected {expected}: {counts}", flush=True)
    with gzip.open(OUT, "wt") as f:
        json.dump(exceptions, f, separators=(",", ":"), sort_keys=True)
    print(f"{len(exceptions)} exceptions -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2014-10", sys.argv[2] if len(sys.argv) > 2 else f"{datetime.utcnow():%Y-%m}")
