"""Get Google-Earth-Engine-identical Sentinel-1 sigma0 from an openEO batch job on the Copernicus Data Space.

NOT YET RUN: it needs the published container image (see README, "Running inside openEO").
"""
import openeo

CWL = "https://raw.githubusercontent.com/uceshaf/s1-gee-parity/main/cwl/s1_sigma0_gee.cwl"

connection = openeo.connect("openeo.dataspace.copernicus.eu").authenticate_oidc()

s1 = connection.datacube_from_process(
    "run_udf",
    data=None,
    udf=CWL,
    runtime="EOAP-CWL",
    context={
        "spatial_extent": {"west": 10.86, "south": 45.17, "east": 10.94, "north": 45.23},
        "temporal_extent": ["2025-06-01", "2025-07-01"],   # end date not included
        "orbit_direction": "DESCENDING",
        # "exact" (default): each scene runs on the SNAP version GEE used for it (bit-identical to GEE)
        # "approximate": every scene runs on SNAP 11 (small differences before November 2024, see README)
        "match": "exact",
    },
)
# `s1` is a normal openEO data cube from here on (bands VV and VH, linear sigma0): composite it,
# merge it with Sentinel-2, run a model on it, or just download it.
job = s1.execute_batch("s1_sigma0_gee.nc", title="S1 sigma0 (GEE-identical)", out_format="NetCDF")
print(job.job_id, job.status())
