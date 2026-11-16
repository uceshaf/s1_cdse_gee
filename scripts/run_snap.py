"""Run the Google Earth Engine Sentinel-1 GRD recipe with ESA SNAP.

GEE documents its S1_GRD preprocessing as (Sentinel-1 Toolbox):
    Apply orbit file -> GRD border noise removal -> Thermal noise removal
    -> Radiometric calibration (sigma0) -> Range-Doppler terrain correction (SRTM 30 m)
GEE stores each scene in its UTM zone at 10 m; S1_GRD is 10*log10 of that, S1_GRD_FLOAT is linear.

This script writes the SNAP graph XML and runs `gpt`. With --chips it only computes the
chips listed in a gee_chips.json (see gee_reference.py); the terrain correction still runs
on the full-scene grid, so pixels land exactly where a full-scene run would put them.

Usage:
    python run_snap.py <SAFE dir> <out dir> --crs EPSG:32632 [--chips gee_chips.json]
                       [--tag NAME] [--orbit restituted|precise|none]
                       [--dem-resampling M] [--img-resampling M] [--dem "SRTM 1Sec HGT"]
                       [--no-border-noise] [--gpt PATH] [--jvm-prop name=value ...]
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

def _default_gpt() -> Path:
    """SNAP's gpt: $SNAP_GPT, else the local install in tools/snap11, else `gpt` on the PATH (container)."""
    import os
    import shutil

    if os.environ.get("SNAP_GPT"):
        return Path(os.environ["SNAP_GPT"])
    local = Path(__file__).resolve().parents[1] / "tools" / "snap11" / "bin"
    for name in ("gpt.exe", "gpt"):
        if (local / name).exists():
            return local / name
    return Path(shutil.which("gpt") or "gpt")


DEFAULT_GPT = _default_gpt()
ORBIT_TYPES = {
    "restituted": "Sentinel Restituted (Auto Download)",
    "precise": "Sentinel Precise (Auto Download)",
}


def node(node_id: str, operator: str, source: str, params: dict) -> str:
    body = "\n".join(f"      <{k}>{escape(str(v))}</{k}>" for k, v in params.items())
    src = f"<sourceProduct refid=\"{source}\"/>" if source else ""
    return (f"  <node id=\"{node_id}\">\n    <operator>{operator}</operator>\n"
            f"    <sources>{src}</sources>\n    <parameters>\n{body}\n    </parameters>\n  </node>")


def build_graph(args, outputs: list) -> str:
    """outputs: list of (name, region or None, output path); the subset is taken after terrain
    correction, so it never changes where pixels fall."""
    nodes = [node("read", "Read", "", {"file": args.safe / "manifest.safe"})]
    last = "read"
    if args.orbit != "none":
        nodes.append(node("orbit", "Apply-Orbit-File", last, {
            "orbitType": ORBIT_TYPES[args.orbit], "polyDegree": 3, "continueOnFail": "false"}))
        last = "orbit"
    if not args.no_border_noise:
        nodes.append(node("border", "Remove-GRD-Border-Noise", last, {
            "borderLimit": 500, "trimThreshold": 0.5}))
        last = "border"
    if not args.no_thermal_noise:
        nodes.append(node("thermal", "ThermalNoiseRemoval", last, {
            "removeThermalNoise": "true", "reIntroduceThermalNoise": "false"}))
        last = "thermal"
    nodes.append(node("calibration", "Calibration", last, {
        "outputSigmaBand": "true", "outputGammaBand": "false", "outputBetaBand": "false",
        "outputImageScaleInDb": "false"}))
    nodes.append(node("terrain", "Terrain-Correction", "calibration", {
        "sourceBands": "Sigma0_VV,Sigma0_VH",   # fixes the output band order: 1 = VV, 2 = VH
        "demName": args.dem,
        "demResamplingMethod": args.dem_resampling,
        "imgResamplingMethod": args.img_resampling,
        "pixelSpacingInMeter": 10.0,
        "mapProjection": args.crs,
        "alignToStandardGrid": "false",
        "nodataValueAtSea": "false",
        "saveSelectedSourceBand": "true",
        "applyRadiometricNormalization": "false",
    }))
    for name, region, path in outputs:
        source = "terrain"
        if region is not None:
            # a string is a WKT polygon in lon/lat; a tuple is a pixel window (x, y, width, height)
            subset = ({"geoRegion": region} if isinstance(region, str)
                      else {"region": ",".join(str(v) for v in region)})
            nodes.append(node(f"subset_{name}", "Subset", "terrain", {**subset, "copyMetadata": "false"}))
            source = f"subset_{name}"
        nodes.append(node(f"write_{name}", "Write", source, {
            "file": path, "formatName": "GeoTIFF-BigTIFF"}))
    return "<graph id=\"gee_s1_grd\">\n  <version>1.0</version>\n" + "\n".join(nodes) + "\n</graph>\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("safe", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--crs", required=True, help="target CRS, e.g. EPSG:32632 (the scene's UTM zone in GEE)")
    ap.add_argument("--chips", type=Path, help="gee_chips.json; compute only these chips")
    ap.add_argument("--tag", default="snap")
    ap.add_argument("--orbit", default="restituted", choices=["restituted", "precise", "none"])
    ap.add_argument("--dem", default="SRTM 1Sec HGT")
    ap.add_argument("--dem-resampling", default="BILINEAR_INTERPOLATION")
    ap.add_argument("--img-resampling", default="BILINEAR_INTERPOLATION")
    ap.add_argument("--no-border-noise", action="store_true")
    ap.add_argument("--no-thermal-noise", action="store_true", help="diagnostic only: skip thermal noise removal")
    ap.add_argument("--gpt", type=Path, default=DEFAULT_GPT)
    ap.add_argument("--timeout", type=float, help="stop gpt after this many seconds")
    ap.add_argument("--jvm-prop", action="append", default=[],
                    help="Java system property for SNAP, e.g. snap.jai.defaultTileSize=1024 (repeatable)")
    args = ap.parse_args()
    args.safe = args.safe.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.chips:
        chips = json.loads(args.chips.read_text())["chips"]
        outputs = [(name, (c["col"], c["row"], c["size"], c["size"]), args.out / f"{args.tag}_{name}.tif")
                   for name, c in chips.items()]
    else:
        outputs = [("full", None, args.out / f"{args.tag}_full.tif")]

    graph_path = args.out / f"{args.tag}_graph.xml"
    graph_path.write_text(build_graph(args, outputs))
    print("graph:", graph_path)
    t0 = time.time()
    cmd = [str(args.gpt), str(graph_path)] + [f"-J-D{prop}" for prop in args.jvm_prop]
    returncode, log = run_gpt(cmd, args.out / f"{args.tag}_gpt.log", [path for _, _, path in outputs],
                              timeout=args.timeout)
    print(f"gpt exit code {returncode} after {time.time() - t0:.0f} s; log: {args.tag}_gpt.log")
    print("\n".join(log.strip().splitlines()[-12:]))
    sys.exit(returncode)


def run_gpt(cmd: list, log_path: Path, outputs: list, grace: float = 20.0, timeout: float = None) -> tuple:
    """Run gpt. Some old SNAP versions (seen with 8.0.3 on Windows) write their output, print "done."
    and then never exit; in that case stop the process after `grace` seconds and report success.
    Old versions can also hang after an internal error; `timeout` (seconds) stops them with exit code 124."""
    started = time.time()
    with open(log_path, "w") as log_file:
        posix = os.name != "nt"
        # on Linux gpt is a launcher script: run it in its own process group so stopping it stops Java too
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True, start_new_session=posix)

        def stop() -> None:
            if posix:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()

        finished_since = None
        while proc.poll() is None:
            time.sleep(2)
            text = log_path.read_text(errors="replace")
            if timeout and time.time() - started > timeout:
                stop()
                return 124, text + f"\n(gpt stopped after {timeout:.0f} s without finishing)"
            if " done." in text and all(Path(p).exists() for p in outputs):
                finished_since = finished_since or time.time()
                if time.time() - finished_since > grace:
                    stop()
                    return 0, text + "\n(gpt did not exit after finishing; stopped)"
    return proc.returncode, log_path.read_text(errors="replace")


if __name__ == "__main__":
    main()
