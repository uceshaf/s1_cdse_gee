# Container for Google-Earth-Engine-identical Sentinel-1 sigma0 (scripts/run_job.py).
# Built and tested by .github/workflows/docker.yml; build from the repository root:
#     docker build -t s1-gee-parity -f cwl/Dockerfile .
#
# The recipe was verified against GEE on Windows; this Linux image must pass tests/test_parity.py
# (the CI workflow runs it) before it is used.
#
# GEE used a different ESA SNAP version in each period and the numbers only match with a matching build
# (scripts/snap_versions.py), so the image carries four SNAP installs under /opt/snap/<version>:
#     7.0.0   Jan - Nov 2020 (GEE: 7.0.3)      8.0.3   Nov 2020 - Aug 2023 (GEE: 8.0.1 and 8.0.3)
#     9.0.0   Aug 2023 - Nov 2024              11.0.1  Nov 2024 - today (GEE: 11.0.0 and 11.0.1),
#                                                      and scenes until Jan 2020 (GEE: 6.0.4, within 1 DN)
FROM ubuntu:22.04

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 LANG=C.UTF-8

RUN apt-get -qq update && \
    apt-get -qq install -y --no-install-recommends wget ca-certificates fontconfig unzip procps python3 python3-pip && \
    rm -rf /var/lib/apt/lists/* && \
    pip3 install --no-cache-dir "rasterio>=1.3" numpy requests boto3 pytest

COPY scripts/install_snap_snapshot.py /opt/s1gee/build/

# Only the SNAP engine and the Sentinel-1 (microwave) toolbox are used. The optical toolboxes (s2tbx, s3tbx;
# opttbx in SNAP 11) and the updater's backup copies of replaced modules are removed in the same step as each
# install, otherwise they stay in the image layer: no radar or core module depends on them (checked on the
# module manifests of all four versions), and tests/test_parity.py checks the result. The openEO cluster did
# not manage to start the first image (4.6 GB compressed, about 11 GB unpacked) within 20 minutes, while
# smaller images start in seconds.
# slim <install> <toolbox folders...>: remove the folders, their lines in etc/snap.clusters and update backups
RUN printf '%s\n' '#!/bin/sh' 'set -eu' 'home=$1; shift' \
      'for t in "$@"; do rm -rf "$home/$t"; sed -i "/^$t\$/d" "$home/etc/snap.clusters"; done' \
      'rm -rf "$home"/*/update/backup /root/.snap' > /usr/local/bin/slim_snap && chmod +x /usr/local/bin/slim_snap

# --- SNAP 7.0.0: the installer as it is (bit-identical to GEE's 7.0.3 on the test scene)
RUN wget -q -O /tmp/snap.sh https://step.esa.int/downloads/7.0/installers/esa-snap_sentinel_unix_7_0.sh && \
    sh /tmp/snap.sh -q -dir /opt/snap/7.0.0 && rm /tmp/snap.sh && \
    slim_snap /opt/snap/7.0.0 s2tbx s3tbx

# --- SNAP 8.0.3: 8.0 installer, then ESA's update snapshot of 19 May 2021
RUN wget -q -O /tmp/snap.sh https://download.esa.int/step/snap/8.0/installers/esa-snap_sentinel_unix_8_0.sh && \
    sh /tmp/snap.sh -q -dir /opt/snap/8.0.3 && rm /tmp/snap.sh && \
    python3 /opt/s1gee/build/install_snap_snapshot.py /opt/snap/8.0.3 8.0_20210519-131352 && \
    rm -rf /opt/snap/nbm_* && \
    slim_snap /opt/snap/8.0.3 s2tbx s3tbx

# --- SNAP 9.0.0: the installer as it is
RUN wget -q -O /tmp/snap.sh https://download.esa.int/step/snap/9.0/installers/esa-snap_sentinel_unix_9_0_0.sh && \
    sh /tmp/snap.sh -q -dir /opt/snap/9.0.0 && rm /tmp/snap.sh && \
    slim_snap /opt/snap/9.0.0 s2tbx s3tbx

# --- SNAP 11.0.1: 11.0.0 installer, then update the modules.
# `snap --modules --update-all` does not exit by itself: wait until it reports that nothing is left
# ("updates=0"), then stop it. `timeout` runs it in its own process group and passes the signal on to the
# whole group, so stopping `timeout` stops the launcher and its Java process and nothing else.
RUN wget -q -O /tmp/snap.sh https://download.esa.int/step/snap/11.0/installers/esa-snap_sentinel_linux-11.0.0.sh && \
    sh /tmp/snap.sh -q -dir /opt/snap/11.0.1 && rm /tmp/snap.sh && \
    for attempt in 1 2 3; do \
      timeout -k 20 1500 /opt/snap/11.0.1/bin/snap --nosplash --nogui --modules --update-all > /tmp/snap_update.log 2>&1 & \
      pid=$!; \
      while kill -0 "$pid" 2>/dev/null && ! grep -q "^updates=0" /tmp/snap_update.log; do sleep 2; done; \
      sleep 3; kill -TERM "$pid" 2>/dev/null || true; wait "$pid" || true; \
      echo "--- update attempt $attempt:"; { grep -v "^$" /tmp/snap_update.log | uniq | tail -30; } || true; \
    done; \
    rm -f /tmp/snap_update.log && \
    slim_snap /opt/snap/11.0.1 opttbx

# Fail the build if a calibration module is not the expected build (a different build changes the numbers).
RUN check() { unzip -p "$1" META-INF/MANIFEST.MF | tr -d '\r' | grep -x "OpenIDE-Module-Specification-Version: $2"; }; \
    check /opt/snap/7.0.0/s1tbx/modules/org-esa-s1tbx-s1tbx-op-calibration.jar 7.0.0 && \
    check /opt/snap/8.0.3/s1tbx/modules/org-esa-s1tbx-s1tbx-op-calibration.jar 8.0.3 && \
    check /opt/snap/9.0.0/s1tbx/modules/org-esa-s1tbx-s1tbx-op-calibration.jar 9.0.0 && \
    check /opt/snap/11.0.1/microwavetbx/modules/eu-esa-microwavetbx-sar-op-calibration.jar 11.0.1 && \
    du -sh /opt/snap/*

# Java heap for gpt: the installers size it for the build machine; fix it for the job container instead.
RUN for v in 7.0.0 8.0.3 9.0.0 11.0.1; do \
      sed -i '/^-Xmx/d' /opt/snap/$v/bin/gpt.vmoptions && echo "-Xmx8G" >> /opt/snap/$v/bin/gpt.vmoptions; \
    done

COPY scripts/ /opt/s1gee/scripts/
COPY tests/ /opt/s1gee/tests/
ENV S1GEE_SNAP_HOME=/opt/snap

# At run time the job needs outbound HTTPS/HTTP to:
#   step.esa.int                           orbit files and SRTM tiles
#   catalogue.dataspace.copernicus.eu      scene search and publication times
#   the eodata S3 endpoint                 raw scenes (AWS_* variables, injected by the openEO backend)
ENTRYPOINT ["python3", "/opt/s1gee/scripts/run_job.py"]
