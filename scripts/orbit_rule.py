"""Choose the restituted orbit file Google Earth Engine most likely used for a Sentinel-1 scene.

Two (sometimes three) restituted orbit files cover every acquisition. With the file GEE used the output is
bit-identical to GEE; with the other one it differs by about 0.002 dB (median). Which file GEE got depended
on where its SNAP version downloaded orbit files from, so the rule changes over the years (GEE_ORBIT_RULE).
Checked against GEE on 55 scenes from 2018 to 2026 (scripts/orbit_rule_fit.py), it gives GEE's file in 51.
"""
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests

ORBIT_SERVER = "http://step.esa.int/auxdata/orbits/Sentinel-1/RESORB"
PRECISE_SERVER = "http://step.esa.int/auxdata/orbits/Sentinel-1/POEORB"
CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
FMT = "%Y%m%dT%H%M%S"
# (scenes sensed from, rule, minutes). Fitted to the scenes listed by scripts/orbit_rule_fit.py.
#   "oldest": the covering file that was generated first
#   "newest": the covering file that was generated last; with minutes, only files generated no later than
#             that many minutes relative to the product's publication in the Copernicus catalogue count,
#             and if there is none, the oldest file is taken
GEE_ORBIT_RULE = [
    (datetime(2014, 1, 1), "newest", None),
    (datetime(2018, 3, 1), "oldest", None),    # two scenes only (18 Mar, 4 Jun 2018); newest on 22 Feb and 5 Aug
    (datetime(2018, 8, 1), "newest", None),
    (datetime(2022, 1, 1), "oldest", None),
    (datetime(2024, 8, 1), "newest", -37),     # any value from -43 to -31 fits the scenes of this period
]
_listing_cache = {}


def scene_times(scene: str) -> tuple:
    """(satellite, sensing start, sensing end) from a product name such as S1A_IW_GRDH_1SDV_<start>_<end>_..."""
    parts = scene.split("_")
    return parts[0], datetime.strptime(parts[4], FMT), datetime.strptime(parts[5], FMT)


def _listing(sat: str, day: datetime, kind: str = "RESORB") -> list:
    key = (sat, day.year, day.month, kind)
    if key not in _listing_cache:
        server = ORBIT_SERVER if kind == "RESORB" else PRECISE_SERVER
        r = requests.get(f"{server}/{sat}/{day:%Y}/{day:%m}/", timeout=60)
        _listing_cache[key] = sorted(set(re.findall(
            rf"{sat}_OPER_AUX_{kind}_OPOD_\d+T\d+_V\d+T\d+_\d+T\d+\.EOF\.zip", r.text))) if r.ok else []
    return _listing_cache[key]


def candidates(scene: str, kind: str = "RESORB") -> list:
    """Orbit files whose validity covers the scene, oldest generation first.
    kind: RESORB (restituted, what GEE uses) or POEORB (precise)."""
    sat, start, end = scene_times(scene)
    server = ORBIT_SERVER if kind == "RESORB" else PRECISE_SERVER
    found = {}
    for day in (start - timedelta(days=1), start, start + timedelta(days=1)):
        for name in _listing(sat, day, kind):
            gen, v0, v1 = (datetime.strptime(g, FMT)
                           for g in re.search(r"OPOD_(\d+T\d+)_V(\d+T\d+)_(\d+T\d+)", name).groups())
            if v0 <= start and end <= v1:
                found[name] = {"file": name, "generated": gen, "valid_from": v0, "valid_to": v1, "kind": kind,
                               "url": f"{server}/{sat}/{day:%Y}/{day:%m}/{name}"}
    return sorted(found.values(), key=lambda c: c["generated"])


def publication_time(scene: str) -> datetime:
    """First publication of the product in the Copernicus catalogue (the original, not the COG repackaging)."""
    name = scene.replace(".SAFE", "")
    prefix = "_".join(name.split("_")[:8])                                # name without the product hash
    r = requests.get(CATALOGUE, params={"$filter": f"startswith(Name,'{prefix}')", "$top": 20}, timeout=90)
    r.raise_for_status()
    products = r.json()["value"]
    # ESA has re-published some acquisitions years later under a new hash; GEE holds both products and
    # processed each when it appeared. Use this product's own date when the catalogue knows this exact
    # name; a COG repackaging (other hash) falls back to the earliest product of the acquisition.
    exact = [p for p in products if p["Name"].replace(".SAFE", "") == name]
    dates = [p["PublicationDate"] for p in (exact or products)]
    if not dates:
        raise LookupError(f"{prefix} not found in the Copernicus catalogue")
    return datetime.fromisoformat(min(dates)[:19])


def rule_for(scene: str = None) -> tuple:
    """(rule, minutes) of GEE_ORBIT_RULE for the scene; the current rule when no scene is given."""
    sensed = scene_times(scene)[1] if scene else datetime.max
    return next((rule, minutes) for since, rule, minutes in reversed(GEE_ORBIT_RULE) if sensed >= since)


def choose(cands: list, published: datetime, scene: str = None) -> dict:
    """The candidate (of candidates(), oldest first) that GEE most likely used."""
    rule, minutes = rule_for(scene)
    if rule == "oldest":
        return cands[0]
    if minutes is None:
        return cands[-1]
    available = [c for c in cands if c["generated"] <= published + timedelta(minutes=minutes)]
    return available[-1] if available else cands[0]


def select_orbit_file(scene: str) -> dict:
    """The orbit file to use, with "kind" RESORB or POEORB. ESA's server has no restituted files for
    4 Nov 2022 - early Aug 2023; there the precise file is used instead and the result is close to GEE
    but not identical (README, "Known gaps")."""
    cands = candidates(scene)
    published = publication_time(scene)
    if not cands:
        precise = candidates(scene, "POEORB")
        if not precise:
            raise LookupError(f"no restituted or precise orbit file covers {scene}")
        chosen = dict(precise[0])
        chosen["published"] = published
        chosen["alternatives"] = [c["file"] for c in precise[1:]]
        return chosen
    chosen = dict(choose(cands, published, scene))
    chosen["published"] = published
    chosen["alternatives"] = [c["file"] for c in cands if c["file"] != chosen["file"]]
    return chosen


def place_orbit_file(orbit: dict, snap_userdir: Path, scene: str) -> Path:
    """Put the file where SNAP looks first. The user directory must be private to this scene: SNAP takes
    the first covering file in the folder, so no other covering file may be there."""
    sat, start, _ = scene_times(scene)
    content = None
    target = None
    # SNAP looks in the folder of the month of the product's first orbit state vector, which starts
    # shortly before the scene; cover a month boundary by also filling the validity-start month
    for month in {start, orbit["valid_from"]}:
        folder = (snap_userdir / "auxdata" / "Orbits" / "Sentinel-1" / orbit.get("kind", "RESORB") / sat
                  / f"{month:%Y}" / f"{month:%m}")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / orbit["file"]
        if not target.exists():
            if content is None:
                r = requests.get(orbit["url"], timeout=300)
                r.raise_for_status()
                content = r.content
            target.write_bytes(content)
    return target


if __name__ == "__main__":
    import sys

    for scene_name in sys.argv[1:]:
        o = select_orbit_file(scene_name)
        print(f"{scene_name}\n  published {o['published']}  ->  {o['file']}\n  not used: {o['alternatives']}")
