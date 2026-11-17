"""Run the same small extraction twice on the Copernicus Data Space openEO backend and record what each
job costs: once through the GEE-identical container (cwl/s1_sigma0_gee.cwl), once with openEO's own
sar_backscatter(coefficient="sigma0-ellipsoid").

    python examples/cost_comparison.py <output folder> [gee] [ellipsoid]

Writes <output folder>/jobs.json (status, credits and resource usage of each job) and the job results.
"""
import json
import sys
import time
from pathlib import Path

import openeo

CWL = "https://raw.githubusercontent.com/uceshaf/s1_cdse_gee/main/cwl/s1_sigma0_gee.cwl"
EXTENT = {"west": 10.868, "south": 45.192, "east": 10.911, "north": 45.223}      # 3.4 x 3.4 km, Lake Garda
PERIOD = ["2025-06-01", "2025-07-01"]                                            # 10 descending scenes
ORBIT = "DESCENDING"


def gee_cube(connection):
    return connection.datacube_from_process(
        "run_udf", data=None, udf=CWL, runtime="EOAP-CWL",
        context={"spatial_extent": EXTENT, "temporal_extent": PERIOD, "orbit_direction": ORBIT, "match": "exact"})


def ellipsoid_cube(connection):
    cube = connection.load_collection(
        "SENTINEL1_GRD", spatial_extent=EXTENT, temporal_extent=PERIOD, bands=["VV", "VH"],
        properties={"sat:orbit_state": lambda v: v == ORBIT})
    return cube.sar_backscatter(coefficient="sigma0-ellipsoid")


def retry(call, attempts: int = 8):
    """The backend limits the request rate (HTTP 429): wait and try again."""
    for attempt in range(attempts):
        try:
            return call()
        except openeo.rest.OpenEoApiPlainError as exc:
            if exc.http_status_code != 429 or attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    wanted = sys.argv[2:] or ["gee", "ellipsoid"]
    state_path = out / "jobs.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    connection = openeo.connect("openeo.dataspace.copernicus.eu")
    connection.authenticate_oidc(max_poll_time=900)
    print("authenticated", flush=True)

    cubes = {"gee": gee_cube, "ellipsoid": ellipsoid_cube}
    jobs = {}
    for name in wanted:
        if name in state:                                  # continue an earlier run
            job = connection.job(state[name]["job_id"])
        else:
            job = retry(lambda: cubes[name](connection).create_job(
                out_format="NetCDF", title=f"S1 cost comparison: {name}"))
            state[name] = {"job_id": job.job_id, "status": "created"}
            state_path.write_text(json.dumps(state, indent=2))
        if retry(job.status) == "created":
            retry(job.start)
        jobs[name] = job
        print(name, job.job_id, flush=True)

    pending = dict(jobs)
    started = time.time()
    while pending:
        time.sleep(30)
        if time.time() - started > 40 * 60:              # never let a stuck job run up credits
            for name, job in pending.items():
                print(time.strftime("%H:%M:%S"), name, "cancelled after 40 min", flush=True)
                job.stop()
            started = float("inf")
        for name, job in list(pending.items()):
            try:
                info = job.describe()
            except Exception as exc:                       # a network hiccup must not lose the run
                print(name, "status request failed:", exc, flush=True)
                continue
            if info["status"] != state[name]["status"]:
                print(time.strftime("%H:%M:%S"), name, info["status"], flush=True)
            state[name].update({k: info.get(k) for k in ("status", "created", "updated", "costs", "usage")})
            if info["status"] in ("finished", "error", "canceled"):
                del pending[name]
                (out / f"{name}_logs.json").write_text(json.dumps(
                    [dict(entry) for entry in job.logs(level="warning" if info["status"] == "finished" else "info")],
                    indent=1))
                if info["status"] == "finished":
                    job.get_results().download_files(out / name)
        state_path.write_text(json.dumps(state, indent=2))
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
