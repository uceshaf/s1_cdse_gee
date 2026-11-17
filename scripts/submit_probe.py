"""Run scripts/cluster_probe.py (or a bare command) inside a container on the Copernicus Data Space openEO
backend and print what comes back.

    python scripts/submit_probe.py <output folder> [--image IMAGE] [--ram MB] [--mode quick|full]
                                   [--command "sh -c ..."] [--cancel-after MINUTES]

Default image: the one in cwl/s1_sigma0_gee.cwl. The probe script travels inside the CWL text, so a changed
probe needs no new container build. --command runs that command instead of the probe (for images without
Python); the job then ends in an error after the step, because the step writes no STAC catalogue, but the
backend log shows whether and when the step finished. The job is cancelled after --cancel-after minutes so
a container that never starts cannot run up credits.
"""
import argparse
import base64
import json
import re
import shlex
import time
from pathlib import Path

import openeo

ROOT = Path(__file__).resolve().parents[1]

CWL = """cwlVersion: v1.2
class: CommandLineTool
id: s1gee_probe
baseCommand: {command}
requirements:
  - class: DockerRequirement
    dockerPull: {image}
  - class: NetworkAccess
    networkAccess: true
  - class: ResourceRequirement
    ramMin: {ram}
    ramMax: {ram}
    coresMin: 2
    coresMax: 4
inputs:
  probe:
    type: string?
outputs:
  output:
    type: Directory
    outputBinding:
      glob: .
"""


def decode(folder: Path) -> str:
    import numpy as np
    import rasterio

    parts = []
    for tif in sorted(folder.glob("*.tif")):
        with rasterio.open(tif) as src:
            values = np.nan_to_num(src.read(1)).round().astype("int64").ravel()
        parts.append(bytes(int(v) for v in values if 0 < v < 256).decode("ascii", errors="replace"))
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--image")
    ap.add_argument("--ram", type=int, default=12000)
    ap.add_argument("--mode", default="full")
    ap.add_argument("--command")
    ap.add_argument("--cancel-after", type=float, default=20)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    image = args.image or re.search(r"dockerPull:\s*(\S+)",
                                    (ROOT / "cwl" / "s1_sigma0_gee.cwl").read_text()).group(1)
    if args.command:
        command = shlex.split(args.command)
    else:
        script = base64.b64encode((ROOT / "scripts" / "cluster_probe.py").read_bytes()).decode()
        command = ["python3", "-c", "import base64,sys;exec(base64.b64decode(sys.argv[1]))", script, args.mode]
    cwl = CWL.format(command=json.dumps(command), image=image, ram=args.ram)

    connection = openeo.connect("openeo.dataspace.copernicus.eu").authenticate_oidc()
    cube = connection.datacube_from_process("run_udf", data=None, udf=cwl, runtime="EOAP-CWL",
                                            context={"probe": "1"})
    title = f"S1 GEE parity: probe {image.rsplit('/', 1)[-1]} {args.ram} MB"
    job = cube.create_job(out_format="GTiff", title=title)
    job.start()
    started = time.time()
    print("job", job.job_id, title, flush=True)
    status, info, cancelled = None, {}, False
    while status not in ("finished", "error", "canceled"):
        time.sleep(20)
        try:
            info = job.describe()
        except Exception as exc:
            print("status request failed:", exc, flush=True)
            continue
        if info["status"] != status:
            status = info["status"]
            print(time.strftime("%H:%M:%S"), status, flush=True)
        if status in ("queued", "running") and not cancelled and time.time() - started > args.cancel_after * 60:
            print(time.strftime("%H:%M:%S"), f"cancelling after {args.cancel_after:.0f} min", flush=True)
            job.stop()
            cancelled = True
    info = job.describe()
    record = {k: info.get(k) for k in ("id", "title", "status", "created", "updated", "costs", "usage")}
    record["cancelled_by_us"] = cancelled
    logs = [dict(e) for e in job.logs(level="debug")]
    steps = {}                                   # CWL step: (first poll, last poll, last status)
    for e in logs:
        m = re.search(r"CWL job poll loop: job_name='([^']+)' timer.elapsed\(\)=([\d.]+)s job.status: (.*)",
                      e.get("message") or "")
        if m:
            first = steps.get(m.group(1), (e.get("time"),))[0]
            steps[m.group(1)] = (first, e.get("time"), m.group(3)[:300])
    record["cwl_steps"] = steps
    (out / "job.json").write_text(json.dumps(record, indent=2))
    (out / "logs.json").write_text(json.dumps(
        [e for e in logs if "CWL job poll loop: " not in (e.get("message") or "")], indent=1))
    print(json.dumps(record, indent=2))
    if status == "finished":
        job.get_results().download_files(out / "result")
        report = decode(out / "result")
        (out / "report.txt").write_text(report)
        print(report)
    else:
        for e in [e for e in logs if "CWL job poll loop: " not in (e.get("message") or "")][-25:]:
            print(e.get("level"), (e.get("message") or "")[:1200])


if __name__ == "__main__":
    main()
