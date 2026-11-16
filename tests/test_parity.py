"""Regression test: the job driver must reproduce Google Earth Engine bit for bit
(scenes GEE processed with SNAP 6.0.4, until 12 Jan 2020: within 1 WorldCereal DN, see README).

Each case in tests/reference/cases.json is a 2.5 x 2.5 km window of one scene, with the pixels read from GEE
(COPERNICUS/S1_GRD_FLOAT) stored next to it. There is one case per SNAP build, because GEE used a different
SNAP version in each period (scripts/snap_versions.py). The test runs scripts/run_job.py for the window and
compares. No Earth Engine account is needed. The raw scenes (1 to 1.7 GB each) come from Planetary Computer
unless a staged copy is found in the folder named by S1GEE_CACHE.

    python tests/test_parity.py [name filter]        (or: pytest tests/test_parity.py)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests" / "reference"
CASES = json.loads((REFERENCE / "cases.json").read_text())


@pytest.mark.parametrize("name", sorted(CASES))
def test_bit_identical_to_gee(name):
    case = CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        args = Path(tmp) / "arguments.json"
        args.write_text(json.dumps(case["job"]))
        out = Path(tmp) / "out"
        cmd = [sys.executable, str(ROOT / "scripts" / "run_job.py"), str(args), "--out", str(out),
               "--source", os.environ.get("S1GEE_SOURCE", "planetary-computer")]
        if os.environ.get("S1GEE_CACHE"):
            cmd += ["--cache", os.environ["S1GEE_CACHE"]]
        subprocess.run(cmd, check=True)

        assert (out / "collection.json").exists(), "the job must write a STAC collection"
        acquisition = "_".join(case["scene"].split("_")[:8])      # scene name without the product hash
        tifs = list(out.glob(acquisition + "*_sigma0.tif"))
        assert len(tifs) == 1, f"expected one output for {acquisition}, got {[t.name for t in out.glob('*.tif')]}"
        with rasterio.open(REFERENCE / name) as ref, rasterio.open(tifs[0]) as got:
            assert got.crs == ref.crs
            offset = ((ref.transform.c - got.transform.c) / 10, (got.transform.f - ref.transform.f) / 10)
            assert all(abs(v - round(v)) < 1e-6 for v in offset), f"output is not on the GEE pixel grid: {offset}"
            window = from_bounds(*ref.bounds, got.transform).round_offsets().round_lengths()
            mine = got.read(window=window)
            theirs = ref.read()
        assert mine.shape == theirs.shape, f"output does not cover the reference window: {mine.shape}"
        identical = float((mine == theirs).mean())
        max_dn = float(np.abs(np.sqrt(mine.astype("float64")) - np.sqrt(theirs.astype("float64"))).max() * 10 ** 4.15)
        print(f"{name} (GEE SNAP {case.get('gee_snap_version')}): bit-identical pixels {identical:.4%}; "
              f"largest difference {max_dn:.3f} WorldCereal DN")
        if "min_share_within_1dn" in case:      # a period without a bit-identical SNAP build
            dn = np.abs(np.sqrt(mine.astype("float64")) - np.sqrt(theirs.astype("float64"))) * 10 ** 4.15
            within = float((dn <= 1).mean())
            print(f"    pixels within 1 DN: {within:.4%} (required {case['min_share_within_1dn']:.1%})")
            assert within >= case["min_share_within_1dn"], f"only {within:.4%} of pixels are within 1 DN of GEE"
        else:
            assert identical == 1.0, f"only {identical:.4%} of pixels are bit-identical to GEE (max {max_dn:.3f} DN)"


if __name__ == "__main__":
    for case_name in sorted(CASES):
        if len(sys.argv) < 2 or sys.argv[1] in case_name:
            test_bit_identical_to_gee(case_name)
    print("OK")
