"""Bring a clean SNAP install to an exact module version using the SNAP Maven repository.

For SNAP 6 the ESA update centre only keeps the last build (6.0.10), but Google Earth Engine used 6.0.4
(image property SNAP_Graph_Processing_Framework_GPF_vers). The Maven repository still has every release as
NetBeans modules (.nbm). This script replaces each org.esa module of the given clusters with that version;
modules that were not released in that version are left as they are and listed.

Usage:
    python install_snap_maven.py <SNAP install dir> <version, e.g. 6.0.4> [cluster ...]   (default: snap s1tbx)
"""
import io
import sys
import zipfile
from pathlib import Path

import requests

REPO = "https://nexus.senbox.net/nexus/content/groups/public"
GROUPS = {"org-esa-snap-": "org/esa/snap", "org-esa-s1tbx-": "org/esa/s1tbx", "org-jlinda-": "org/jlinda"}
# modules a release added, which a clean install of the .0 version does not have: (cluster, group, artifact)
ADDED = {"6.0.4": [("s1tbx", "org/esa/s1tbx", "s1tbx-io-ephemeris")]}


def apply_nbm(content: bytes, artifact: str, cluster_dir: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for member in z.namelist():
            if not member.startswith("netbeans/") or member.endswith("/"):
                continue
            if member.endswith(".pack.gz"):
                raise RuntimeError(f"{artifact}: pack200-compressed jar {member} is not supported")
            target = cluster_dir / member[len("netbeans/"):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))


def main(install: Path, version: str, clusters: list) -> None:
    applied, kept = [], []
    for cluster in clusters:
        for jar in sorted((install / cluster / "modules").glob("*.jar")):
            group = next((g for prefix, g in GROUPS.items() if jar.name.startswith(prefix)), None)
            if not group:
                continue
            artifact = jar.stem[len(next(p for p in GROUPS if jar.name.startswith(p))):]
            r = requests.get(f"{REPO}/{group}/{artifact}/{version}/{artifact}-{version}.nbm", timeout=600)
            if not r.ok:
                kept.append(f"{cluster}/{artifact}")
                continue
            apply_nbm(r.content, artifact, install / cluster)
            applied.append(f"{cluster}/{artifact}")
    for cluster, group, artifact in ADDED.get(version, []):
        if cluster in clusters:
            r = requests.get(f"{REPO}/{group}/{artifact}/{version}/{artifact}-{version}.nbm", timeout=600)
            r.raise_for_status()
            apply_nbm(r.content, artifact, install / cluster)
            applied.append(f"{cluster}/{artifact} (new)")
    print(f"applied {len(applied)} modules at {version} to {install}")
    if kept:
        print(f"left unchanged ({len(kept)}, no {version} release): " + ", ".join(kept))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3:] or ["snap", "s1tbx"])
