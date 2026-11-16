"""End-to-end parity check for one Sentinel-1 scene: SNAP (GEE recipe) versus Google Earth Engine.

Steps: download the raw GRD product, fetch GEE reference chips, run SNAP on the same chips, compare.

Usage:
    python validate_scene.py <GEE scene id> name:lon:lat[:size] [name:lon:lat ...] [-- extra run_snap.py options]

Example:
    python validate_scene.py S1A_IW_GRDH_1SDV_20250609T052724_20250609T052749_059565_076523_C73E \
        flat:10.90:45.20 steep:10.85:46.10
"""
import json
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/"


def run(*cmd) -> None:
    print("\n$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([sys.executable, "-u", *map(str, cmd)], check=True)


def main() -> None:
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        extra = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    scene_id, chips = argv[0], argv[1:]
    # short folder name (sensing start + product hash): Windows paths are limited to 260 characters
    parts = scene_id.split("_")
    work = ROOT / "data" / f"s_{parts[4]}_{parts[-1]}"
    work.mkdir(parents=True, exist_ok=True)

    # 1. raw product (Planetary Computer item ids drop the trailing product hash)
    item_id = scene_id.rsplit("_", 1)[0]
    safes = list(work.glob("*.SAFE"))
    if not safes:
        item = requests.get(STAC + item_id, timeout=60)
        item.raise_for_status()
        product = item.json()["assets"]["safe-manifest"]["href"].rsplit("/", 2)[1]
        print(f"GEE scene {scene_id}\nraw product {product}"
              f"{'' if product == scene_id else '  (different product hash: repackaged copy or different ESA processing run)'}")
        run(SCRIPTS / "fetch_grd_from_pc.py", item_id, work)
        safes = list(work.glob("*.SAFE"))
    safe = safes[0]

    # 2. GEE reference chips on GEE's native grid
    ref = work / "ref"
    run(SCRIPTS / "gee_reference.py", scene_id, ref, *chips)
    crs = json.loads((ref / "gee_chips.json").read_text())["grid"]["crs"]

    # 3. SNAP with the GEE recipe, 4. compare
    out = work / "snap"
    tag = "snap"
    run(SCRIPTS / "run_snap.py", safe, out, "--crs", crs, "--chips", ref / "gee_chips.json", "--tag", tag, *extra)
    run(SCRIPTS / "compare.py", ref / "gee_chips.json", out, tag, "--json", out / "report.json")


if __name__ == "__main__":
    main()
