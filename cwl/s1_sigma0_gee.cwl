#!/usr/bin/env cwl-runner
# Google-Earth-Engine-identical Sentinel-1 sigma0 as an openEO step.
#
# Use from openEO (Copernicus Data Space, runtime "EOAP-CWL"):
#
#   cube = connection.datacube_from_process(
#       "run_udf", data=None, runtime="EOAP-CWL",
#       udf="https://raw.githubusercontent.com/uceshaf/s1_cdse_gee/main/cwl/s1_sigma0_gee.cwl",
#       context={"spatial_extent": {"west": 10.86, "south": 45.17, "east": 10.94, "north": 45.23},
#                "temporal_extent": ["2025-06-01", "2025-07-01"],
#                "orbit_direction": "DESCENDING"})
#
# The output is a STAC collection (collection.json + one item and one GeoTIFF per scene, bands VV and VH,
# linear sigma0 on the pixel grid Google Earth Engine stores the scene on); openEO loads it as a data cube.
#
# Local run:  cwltool --preserve-environment AWS_ACCESS_KEY_ID --preserve-environment AWS_SECRET_ACCESS_KEY \
#                     --preserve-environment AWS_ENDPOINT_URL_S3 cwl/s1_sigma0_gee.cwl inputs.json
#
# Not yet run to completion on the CDSE cluster (see README, "Running inside openEO"). Keep a job to a few
# scenes: the backend's CWL runner fails steps that run longer than about 30 minutes.
# "main" is the latest build; for a fixed version use a release tag in both the
# URL above and the image below.
cwlVersion: v1.2
class: CommandLineTool
id: s1_sigma0_gee
label: Sentinel-1 sigma0, identical to Google Earth Engine COPERNICUS/S1_GRD_FLOAT

baseCommand: ["python3", "/opt/s1gee/scripts/run_job.py"]
arguments: ["arguments.json"]

requirements:
  - class: InlineJavascriptRequirement
  - class: InitialWorkDirRequirement
    listing:
      - entryname: arguments.json
        entry: |
          ${
            return JSON.stringify({
              "spatial_extent": inputs.spatial_extent,
              "temporal_extent": inputs.temporal_extent,
              "orbit_direction": inputs.orbit_direction,
              "db": inputs.db,
              "match": inputs.match
            });
          }
  - class: DockerRequirement
    dockerPull: ghcr.io/uceshaf/s1-gee-parity:main
  - class: NetworkAccess
    networkAccess: true
  # SNAP gets an 8 GB Java heap; the rest is for Python and the raw scene being unpacked
  - class: ResourceRequirement
    ramMin: 12000
    ramMax: 12000
    coresMin: 2
    coresMax: 4

inputs:
  spatial_extent:
    type: Any
    doc: 'Window to compute, in longitude/latitude: {"west": .., "south": .., "east": .., "north": ..}'
  temporal_extent:
    type: string[]
    doc: '["start", "end"] as YYYY-MM-DD; the end date is not included'
  orbit_direction:
    type: string?
    doc: "ASCENDING or DESCENDING; default: both"
  db:
    type: boolean?
    doc: "true: write 10*log10(sigma0) like COPERNICUS/S1_GRD; default: linear like COPERNICUS/S1_GRD_FLOAT"
  match:
    type: string?
    doc: "exact (default): each scene on the SNAP version Google Earth Engine used for it; approximate: every scene on SNAP 11"

outputs:
  output:
    type: Directory
    outputBinding:
      glob: .
