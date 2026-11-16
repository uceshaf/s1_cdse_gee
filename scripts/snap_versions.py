"""Which ESA SNAP build reproduces Google Earth Engine for a Sentinel-1 product.

GEE records the SNAP version it used in the image property SNAP_Graph_Processing_Framework_GPF_vers.
Tabulated over the whole COPERNICUS/S1_GRD archive (Nov 2026, data/gee_snap_version_by_month.json and
..._by_day.json), GEE moved through seven versions, switching on the sensing dates in GEE_VERSIONS.
About 1% of the scenes do not follow their date (products GEE added later, e.g. 15% of Aug 2018 - Jan 2019
carry 9.0.0, and the days of a switch). Those are listed by name in gee_version_exceptions.json.gz,
written by export_gee_version_exceptions.py.

The result only matches GEE with a matching SNAP build (SNAP 11 on a 2021 scene: 25% of pixels within 1 DN).
The exception is GEE's 6.0.4 (scenes until 12 Jan 2020): SNAP 11.0.1 gives the same values within 1 DN
(README, "Exact or approximate"), so no SNAP 6 install is used.
"""
import gzip
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (first day of the period, version GEE reports). During a switch GEE ran both versions for a few days
# (8.0.1 -> 8.0.3: all of March 2021; 11.0.0 -> 11.0.1: 27 March - 4 April 2025).
GEE_VERSIONS = [
    (datetime(2014, 1, 1), "6.0.4"),
    (datetime(2020, 1, 13), "7.0.3"),
    (datetime(2020, 11, 25), "8.0.1"),
    (datetime(2021, 3, 30), "8.0.3"),
    (datetime(2023, 8, 8), "9.0.0"),
    (datetime(2024, 11, 8), "11.0.0"),
    (datetime(2025, 4, 4), "11.0.1"),
]

# SNAP install to use for each GEE version: the build checked bit-identical against GEE (README, Validation);
# for 6.0.4 the build checked to be within 1 DN of GEE.
INSTALL_FOR = {
    "6.0.4": "11.0.1",
    "7.0.3": "7.0.0",
    "8.0.1": "8.0.3",
    "8.0.3": "8.0.3",
    "9.0.0": "9.0.0",
    "11.0.0": "11.0.1",
    "11.0.1": "11.0.1",
}

# folder names of the installs on the development machine (tools/); a container uses $S1GEE_SNAP_HOME/<version>
LOCAL_FOLDERS = {"7.0.0": "snap7", "8.0.3": "snap803", "9.0.0": "snap9", "11.0.1": "snap11"}

# GEE versions whose install above gives exactly GEE's float32 values (with GEE's orbit file)
BIT_IDENTICAL = {"7.0.3", "8.0.1", "8.0.3", "9.0.0", "11.0.0", "11.0.1"}


# "approximate" matching: this one build for every year (smaller image, less memory). How far that is from
# GEE per period: README, "Exact or approximate"; measured by single_version_check.py.
SINGLE_INSTALL = "11.0.1"

_exceptions = None


def version_exceptions() -> dict:
    """{ESA product name: SNAP version} for the scenes GEE processed with another version than that of
    their sensing date. Most are products GEE added to its archive years after the acquisition."""
    global _exceptions
    if _exceptions is None:
        path = Path(__file__).resolve().parent / "gee_version_exceptions.json.gz"
        with gzip.open(path, "rt") as f:
            _exceptions = json.load(f)
    return _exceptions


def gee_version(scene: str) -> str:
    """SNAP version GEE used for a product, e.g. S1A_IW_GRDH_1SDV_20220613T052726_..._8BC2 -> 8.0.3.
    Give the ESA product name with its hash: the exception list is keyed by it. (A COG repackaging has
    another hash and gets the version of its sensing date.)"""
    name = scene.replace(".SAFE", "")
    if name in version_exceptions():
        return version_exceptions()[name]
    sensed = datetime.strptime(name.split("_")[4], "%Y%m%dT%H%M%S")
    return [v for since, v in GEE_VERSIONS if since <= sensed][-1]


def gpt_for(gee_snap_version: str, match: str = "exact") -> tuple:
    """(path of the gpt executable, version of that install) for a GEE SNAP version.
    match "exact": the build that reproduces that GEE version; "approximate": SINGLE_INSTALL for every scene.
    $SNAP_GPT forces one executable for everything (its version is then reported as 'forced')."""
    if os.environ.get("SNAP_GPT"):
        return Path(os.environ["SNAP_GPT"]), "forced"
    if match == "approximate":
        install = SINGLE_INSTALL
    elif gee_snap_version in INSTALL_FOR:
        install = INSTALL_FOR[gee_snap_version]
    else:
        raise LookupError(f"GEE processed this product with SNAP {gee_snap_version}, which is not reproduced "
                          f"here (supported: {', '.join(INSTALL_FOR)}); use match=approximate")
    home = os.environ.get("S1GEE_SNAP_HOME")
    base = Path(home) / install if home else ROOT / "tools" / LOCAL_FOLDERS[install]
    for name in ("gpt.exe", "gpt"):
        if (base / "bin" / name).exists():
            return base / "bin" / name, install
    raise FileNotFoundError(f"SNAP {install} (needed for GEE version {gee_snap_version}) not found in {base}")
