"""Runs INSIDE the job container on the openEO cluster and reports what it finds.

The Copernicus Data Space backend shows no output of a CWL container, neither while it runs nor when it
fails. This probe therefore returns its report as the job result: the text is stored as pixel values of a
GeoTIFF (one byte per pixel) with the same STAC metadata as a normal result, and submit_probe.py turns the
downloaded result back into text.

The backend's CWL runner (calrissian) loses track of a container step that runs longer than roughly
30-50 minutes and fails the job (calrissian issue #217), so the probe has a hard time budget well below
that: whatever happens, it writes its report and ends after BUDGET seconds.

It records when the container started (the gap to the backend's "Created CWL job" log line is the time the
image took to arrive), checks the environment and network, and runs the real job for one scene with
time-stamped output; on overrun it records the processes and a Java thread dump of SNAP.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BUDGET = 840                                   # seconds of container time, all included
T0 = time.time()
LOG = []
SECRET = ("KEY", "TOKEN", "SECRET", "PASSWORD")
JOB = {"spatial_extent": {"west": 10.868, "south": 45.192, "east": 10.911, "north": 45.223},
       "temporal_extent": ["2025-06-09", "2025-06-10"], "orbit_direction": "DESCENDING"}
SCENE_PREFIX = ("Sentinel-1/SAR/IW_GRDH_1S/2025/06/09/"
                "S1A_IW_GRDH_1SDV_20250609T052724_20250609T052749_059565_076523_C73E.SAFE/")


def left() -> float:
    return BUDGET - (time.time() - T0)


def log(*parts) -> None:
    LOG.append(f"[{time.time() - T0:6.1f}s] " + " ".join(str(p) for p in parts))


def sh(cmd: str, timeout: float = 30, keep: int = 2500) -> None:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        log(f"$ {cmd}  (exit {r.returncode})")
        LOG.append((r.stdout + r.stderr).strip()[-keep:])
    except Exception as exc:
        log(f"$ {cmd}  -> {type(exc).__name__}: {exc}")


def environment() -> None:
    log("container started", datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "| python", sys.version.split()[0], "| cwd", os.getcwd(), "| HOME", os.environ.get("HOME"),
        "| TMPDIR", os.environ.get("TMPDIR"), "| cpus", os.cpu_count())
    names = sorted(os.environ)
    log("environment variables:", " ".join(names))
    for name in names:
        if name.startswith(("AWS", "S1GEE", "OPENEO")):
            log(f"   {name} =", "(set, hidden)" if any(w in name for w in SECRET) else os.environ[name])
    sh("id; nproc; free -m; cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max 2>&1")
    sh("df -h . /tmp /opt 2>&1; grep -E ' /tmp | / |calrissian' /proc/mounts")
    for folder in ("/tmp", "."):
        sh(f"( time dd if=/dev/zero of={folder}/probe_dd bs=1M count=50 conv=fsync ) 2>&1 | tail -4; "
           f"rm -f {folder}/probe_dd", timeout=40)


def network() -> None:
    import requests

    for url in ("https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$top=1",
                "https://step.esa.int/auxdata/orbits/Sentinel-1/RESORB/S1A/2025/06/",
                "http://step.esa.int/auxdata/dem/SRTMGL1/N45E010.SRTMGL1.hgt.zip",
                "https://step.esa.int/auxdata/dem/SRTMGL1/N45E010.SRTMGL1.hgt.zip"):
        started = time.time()
        try:
            r = requests.get(url, timeout=(5, 10), stream=True)
            size = len(r.raw.read(300_000))
            log(f"GET {url} -> {r.status_code}, {size} bytes in {time.time() - started:.1f} s")
        except Exception as exc:
            log(f"GET {url} FAILED after {time.time() - started:.1f} s: {type(exc).__name__}: {str(exc)[:250]}")

    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("AWS_ENDPOINT_URL_S3", "https://eodata.dataspace.copernicus.eu")
    region = os.environ.get("AWS_DEFAULT_REGION", "default")
    started = time.time()
    try:
        s3 = boto3.client("s3", endpoint_url=endpoint, region_name=region,
                          config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1}))
        objects = s3.list_objects_v2(Bucket="eodata", Prefix=SCENE_PREFIX).get("Contents", [])
        log(f"S3 list at {endpoint} (region {region}): {len(objects)} objects in {time.time() - started:.1f} s")
        tiff = next(o for o in objects if o["Key"].endswith(".tiff"))
        started = time.time()
        body = s3.get_object(Bucket="eodata", Key=tiff["Key"], Range="bytes=100000000-119999999")["Body"].read()
        log(f"S3 ranged read of {len(body) / 1e6:.0f} MB: {time.time() - started:.1f} s")
    except Exception as exc:
        log(f"S3 at {endpoint} (region {region}) FAILED after {time.time() - started:.1f} s: "
            f"{type(exc).__name__}: {str(exc)[:300]}")


def real_run(out: Path) -> None:
    """The real job for one scene, with each output line time-stamped. On overrun: processes, SNAP thread
    dump and log, then stop it."""
    limit = left() - 75
    out.mkdir(parents=True, exist_ok=True)
    args = Path("/tmp/probe_args.json")
    args.write_text(json.dumps(JOB))
    log(f"--- real job, one scene, output in {out} (limit {limit:.0f} s)")
    started = time.time()
    proc = subprocess.Popen([sys.executable, "-u", "/opt/s1gee/scripts/run_job.py", str(args), "--out", str(out)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    lines = []

    def pump():
        for line in proc.stdout:
            lines.append(f"   [{time.time() - T0:6.1f}s] {line.rstrip()}")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    while proc.poll() is None and time.time() - started < limit:
        time.sleep(2)
    overran = proc.poll() is None
    if overran:
        log(f"NOT FINISHED after {limit:.0f} s; processes:")
        sh("ps -eo pid,etime,time,pcpu,rss,stat,args --width 230 | cut -c1-230")
        sh("ls -la /tmp/s1gee_*/staged/*/measurement /tmp/s1gee_*/snap_userdir/*/auxdata/* 2>&1 | head -30; "
           "du -sh /tmp/s1gee_* 2>&1")
        sh("pkill -3 java; sleep 3")             # Java thread dump goes to the SNAP log
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        log(f"exit code {proc.returncode} after {time.time() - started:.0f} s")
    reader.join(timeout=5)
    LOG.extend(lines[-60:])
    for gpt_log in sorted(out.glob("*_gpt.log")):
        text = gpt_log.read_text(errors="replace")
        if overran:                             # the threads that are doing something
            keep = [block for block in text.split("\n\n") if "esa" in block and "RUNNABLE" in block or
                    "Http" in block or "Socket" in block or "Download" in block]
            log(f"SNAP log {gpt_log.name}: {len(text)} chars; first part, then the busy threads:")
            LOG.append(text[:2500])
            LOG.append("\n\n".join(keep)[:9000])
        else:
            log(f"SNAP log {gpt_log.name}, last part:")
            LOG.append(text[-1500:])
    sh(f"ls -la {out}")


def write_report(text: str) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    sys.path.insert(0, "/opt/s1gee/scripts")
    from process_scene import write_stac

    raw = np.frombuffer(text.encode("ascii", errors="replace"), dtype="uint8")
    width = 256
    height = max(2, -(-len(raw) // width))
    data = np.zeros(width * height, dtype="float32")
    data[:len(raw)] = raw
    data = np.stack([data.reshape(height, width)] * 2)
    crs = rasterio.crs.CRS.from_epsg(32632)
    transform = from_origin(646000.0, 5007000.0, 10.0, 10.0)
    name = "S1X_PROBE_REPORT"
    tif = Path(f"{name}_sigma0.tif")
    with rasterio.open(tif, "w", driver="COG", width=width, height=height, count=2, dtype="float32", crs=crs,
                       transform=transform, nodata=0.0, compress="deflate", blocksize=512) as dst:
        dst.write(data)
        dst.descriptions = ("VV", "VH")
    with rasterio.open(tif) as src:
        bounds = src.bounds
    start = datetime(2025, 6, 9, 5, 27, 24, tzinfo=timezone.utc)
    write_stac(Path("."), name, tif, crs, transform, data.shape, bounds, start, start, False, "none", "none",
               "none", False, "probe")


def main() -> None:
    def out_of_time(signum, frame):
        raise TimeoutError("probe time budget used up")

    mode = sys.argv[2] if len(sys.argv) > 2 else "full"      # "quick": no SNAP run
    signal.signal(signal.SIGALRM, out_of_time)
    signal.alarm(BUDGET)
    try:
        log("mode", mode)
        environment()
        network()
        if mode != "quick":
            real_run(Path("/tmp/real_local"))
    except BaseException:
        log("probe stopped:\n" + traceback.format_exc()[-2000:])
    finally:
        signal.alarm(0)
        subprocess.run("pkill -9 java; rm -f probe_dd", shell=True)
        log("probe finished")
        write_report("\n".join(LOG) + "\n")


main()
