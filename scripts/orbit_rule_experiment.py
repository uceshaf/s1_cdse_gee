"""Which restituted orbit file did Google Earth Engine use for a scene?

Two (sometimes three) restituted orbit files cover any acquisition. SNAP takes the first one it finds,
so GEE's result depends on which files existed when GEE processed the scene. For each scene this script
runs the GEE recipe once per candidate file and records which candidate is bit-identical to GEE.

Usage:
    python orbit_rule_experiment.py <results.json> <scene id>:<lon>:<lat> [...]
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import ee
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import compare_chip  # noqa: E402
from orbit_rule import publication_time  # noqa: E402
from orbit_scan import BASE, candidates  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
# local SNAP install that reproduces each GEE version (GEE property SNAP_Graph_Processing_Framework_GPF_vers);
# used unless $SNAP_GPT forces one install for every scene. The job driver runs 6.0.4-period scenes on SNAP 11
# (within 1 DN); finding GEE's orbit file needs the bit-identical 6.0.4 build (install_snap_maven.py).
INSTALLS = {"6.0.4": "snap604", "7.0.3": "snap7", "8.0.1": "snap803", "8.0.3": "snap803", "9.0.0": "snap9",
            "11.0.0": "snap11", "11.0.1": "snap11"}


def py(*cmd) -> None:
    subprocess.run([sys.executable, "-u", *map(str, cmd)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def main() -> None:
    results_path = Path(sys.argv[1])
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    ee.Initialize()
    for spec in sys.argv[2:]:
        scene_id, lon, lat = spec.split(":")
        if scene_id in results:
            continue
        parts = scene_id.split("_")
        sat = parts[0]
        work = ROOT / "data" / f"s_{parts[4]}_{parts[-1]}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            props = ee.Image(f"COPERNICUS/S1_GRD/{scene_id}").toDictionary(
                ["GRD_Post_Processing_stop", "SNAP_Graph_Processing_Framework_GPF_vers"]).getInfo()
            finished = datetime.fromtimestamp(props["GRD_Post_Processing_stop"] / 1e3, tz=timezone.utc).replace(tzinfo=None)

            fetched = not list(work.glob("*.SAFE"))
            if fetched:
                py(SCRIPTS / "fetch_grd_from_pc.py", scene_id.rsplit("_", 1)[0], work)
            safe = next(work.glob("*.SAFE"))
            ref = work / "ref"
            py(SCRIPTS / "gee_reference.py", scene_id, ref, f"pt:{lon}:{lat}:512")
            chips = json.loads((ref / "gee_chips.json").read_text())
            crs = chips["grid"]["crs"]

            gee_snap = props.get("SNAP_Graph_Processing_Framework_GPF_vers")
            record = {"finished": finished.isoformat(), "snap": gee_snap, "candidates": []}
            try:
                record["published"] = publication_time(scene_id).isoformat()
            except Exception:
                record["published"] = None
            gpt_args = []
            if not os.environ.get("SNAP_GPT"):
                gpt = ROOT / "tools" / INSTALLS[gee_snap] / "bin" / "gpt.exe"
                gpt_args = ["--gpt", str(gpt)]
                record["snap_used"] = INSTALLS[gee_snap]
            for k, cand in enumerate(candidates(scene_id)):
                start = cand["valid_from"]
                userdir = ROOT / "tools" / "ud_exp" / f"{parts[4]}_{k}"
                orbit_dir = userdir / "auxdata" / "Orbits" / "Sentinel-1" / "RESORB" / sat / parts[4][:4] / parts[4][4:6]
                orbit_dir.mkdir(parents=True, exist_ok=True)
                target = orbit_dir / cand["file"]
                if not target.exists():
                    # the server files are stored under the month of the validity start or the following day
                    for month in {f"{start:%Y/%m}", f"{cand['valid_to']:%Y/%m}"}:
                        r = requests.get(f"{BASE}/{sat}/{month}/{cand['file']}", timeout=120)
                        if r.ok:
                            target.write_bytes(r.content)
                            break
                tag = f"cand{k}"
                subprocess.run([sys.executable, "-u", str(SCRIPTS / "run_snap.py"), str(safe), str(work / "snap"),
                                "--crs", crs, "--chips", str(ref / "gee_chips.json"), "--tag", tag,
                                "--jvm-prop", f"snap.userdir={userdir}", "--timeout", "1500", *gpt_args],
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                out = work / "snap" / f"{tag}_pt.tif"
                entry = {"file": cand["file"], "generated": cand["generated"].isoformat(),
                         "valid_from": cand["valid_from"].isoformat(),
                         "generated_minus_finished_min": round((cand["generated"] - finished).total_seconds() / 60)}
                if out.exists():
                    cmp = compare_chip(ref / "gee_pt.tif", out)
                    vv = cmp["bands"]["VV"]["all"] if cmp.get("grid_identical") else {}
                    entry["vv_bit_identical_pct"] = vv.get("pct_bit_identical_float32")
                    entry["vv_within_1dn_pct"] = vv.get("pct_within_1dn")
                    vh = cmp["bands"]["VH"]["all"] if cmp.get("grid_identical") else {}
                    entry["vh_within_1dn_pct"] = vh.get("pct_within_1dn")
                    entry["vh_p99_abs_dn"] = vh.get("p99_abs_dn")
                    entry["vh_max_abs_dn"] = vh.get("max_abs_dn")
                else:
                    entry["error"] = "SNAP produced no output"
                record["candidates"].append(entry)
            results[scene_id] = record
            if os.environ.get("S1GEE_DISCARD_SAFE") and fetched:      # keep the disk from filling up
                shutil.rmtree(safe, ignore_errors=True)
        except Exception as exc:  # keep going; one failed scene must not lose the others
            results[scene_id] = {"error": f"{type(exc).__name__}: {exc}"}
        results_path.write_text(json.dumps(results, indent=2))
        rec = results[scene_id]
        if "error" in rec:
            print(scene_id, "ERROR", rec["error"][:150], flush=True)
        else:
            print(scene_id, "finished", rec["finished"][11:16], "|", "  ".join(
                f"[gen {c['generated'][11:16]} ({c['generated_minus_finished_min']:+d} min): "
                f"{c.get('vv_bit_identical_pct')}% identical]" for c in rec["candidates"]), flush=True)


if __name__ == "__main__":
    main()
