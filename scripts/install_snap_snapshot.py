"""Bring a clean SNAP install to the exact module versions of a dated ESA update-centre snapshot.

GEE records the toolbox version per scene (e.g. 8.0.3). SNAP's own updater only offers the newest
build of a major version (8.0.6), but https://step.esa.int/updatecenter/ keeps dated snapshots
(e.g. 8.0_20210519-131352 = 8.0.3). This script applies a snapshot the way the updater would:
each .nbm is a zip whose `netbeans/` folder is copied into the cluster named in Info/info.xml.

Usage:
    python install_snap_snapshot.py <SNAP install dir> <snapshot name, e.g. 8.0_20210519-131352>
"""
import re
import sys
import zipfile
from pathlib import Path

import requests

BASE = "https://step.esa.int/updatecenter"


def main(install: Path, snapshot: str) -> None:
    cache = install.parent / f"nbm_{snapshot}"
    cache.mkdir(exist_ok=True)
    applied, skipped = 0, []
    for group in ("snap", "snap-toolboxes"):
        listing = requests.get(f"{BASE}/{snapshot}/{group}/", timeout=120)
        listing.raise_for_status()
        for name in sorted(set(re.findall(r'href="([^"/]+\.nbm)"', listing.text))):
            nbm = cache / name
            if not nbm.exists():
                r = requests.get(f"{BASE}/{snapshot}/{group}/{name}", timeout=600)
                r.raise_for_status()
                nbm.write_bytes(r.content)
            with zipfile.ZipFile(nbm) as z:
                info = z.read("Info/info.xml").decode("utf-8", "replace")
                cluster = re.search(r'targetcluster="([^"]+)"', info)
                cluster = cluster.group(1) if cluster else None
                if not cluster or not (install / cluster).is_dir():
                    skipped.append(f"{name} (cluster {cluster})")
                    continue
                for member in z.namelist():
                    if not member.startswith("netbeans/") or member.endswith("/"):
                        continue
                    if member.endswith(".pack.gz"):
                        raise RuntimeError(f"{name}: pack200-compressed jar {member} is not supported")
                    target = install / cluster / member[len("netbeans/"):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(member))
            applied += 1
    print(f"applied {applied} modules from {snapshot} to {install}")
    if skipped:
        print(f"skipped {len(skipped)} (cluster not installed): " + ", ".join(skipped[:8]) + (" ..." if len(skipped) > 8 else ""))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2])
