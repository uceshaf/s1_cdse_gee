"""How far from Google Earth Engine is the output when ONE SNAP build is used for every year?

GEE processed each period with the SNAP version of the day (scripts/snap_versions.py). This script runs the
GEE recipe with a single build (default: the SNAP 11 install) on scenes of every period, with the orbit file
GEE used, and compares with the pixels read from GEE. It needs scenes that orbit_rule_experiment.py has
already processed and whose raw product is still in data/s_<start>_<hash>/.

Usage: python single_version_check.py [results.json] [install folder under tools/, default snap11]
"""
import glob
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import compare_chip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEEP = ["pct_bit_identical_float32", "pct_within_1dn", "median_abs_dn", "p99_abs_dn", "max_abs_dn",
        "median_abs_db", "p99_abs_db"]


def main() -> None:
    results_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "single_version_snap11.json"
    install = sys.argv[2] if len(sys.argv) > 2 else "snap11"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    known = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "orbit_rule*.json"))):
        for scene, rec in json.loads(Path(path).read_text()).items():
            cands = sorted(rec.get("candidates", []), key=lambda c: c["generated"])
            for k, c in enumerate(cands):
                if (c.get("vv_bit_identical_pct") or 0) > 99.99:
                    known[scene] = (k, c["file"], rec.get("snap"))
    for scene, (k, orbit_file, gee_snap) in sorted(known.items(), key=lambda kv: kv[0].split("_")[4]):
        parts = scene.split("_")
        work = ROOT / "data" / f"s_{parts[4]}_{parts[-1]}"
        safes = list(work.glob("*.SAFE"))
        if scene in results or not safes or not (work / "ref" / "gee_chips.json").exists():
            continue
        source = ROOT / "tools" / "ud_exp" / f"{parts[4]}_{k}" / "auxdata"
        if not list(source.rglob(orbit_file)):
            continue
        userdir = ROOT / "tools" / "ud_exp" / f"{parts[4]}_{install}"
        for sub in ("Orbits", "dem"):                      # same orbit file and elevation tiles, fresh SNAP caches
            if (source / sub).exists() and not (userdir / "auxdata" / sub).exists():
                shutil.copytree(source / sub, userdir / "auxdata" / sub)
        chips = work / "ref" / "gee_chips.json"
        crs = json.loads(chips.read_text())["grid"]["crs"]
        tag = f"one_{install}"
        out = work / "snap" / f"{tag}_pt.tif"
        out.unlink(missing_ok=True)
        subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "run_snap.py"), str(safes[0]), str(work / "snap"),
                        "--crs", crs, "--chips", str(chips), "--tag", tag, "--jvm-prop", f"snap.userdir={userdir}",
                        "--timeout", "1500", "--gpt", str(ROOT / "tools" / install / "bin" / "gpt.exe")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        rec = {"gee_snap": gee_snap, "install": install, "orbit_file": orbit_file}
        if out.exists():
            cmp = compare_chip(work / "ref" / "gee_pt.tif", out)
            rec["grid_identical"] = cmp.get("grid_identical")
            for band in ("VV", "VH"):
                s = cmp.get("bands", {}).get(band, {}).get("all", {})
                rec[band] = {key: s.get(key) for key in KEEP}
        else:
            rec["error"] = "SNAP produced no output"
        results[scene] = rec
        results_path.write_text(json.dumps(results, indent=2))
        vv = rec.get("VV", {})
        print(f"{parts[0]} {parts[4]} GEE {gee_snap}: VV within 1 DN {vv.get('pct_within_1dn')}  "
              f"median {vv.get('median_abs_db')} dB  p99 {vv.get('p99_abs_db')} dB  {rec.get('error', '')}", flush=True)


if __name__ == "__main__":
    main()
