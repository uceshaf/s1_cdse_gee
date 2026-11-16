"""Download a raw Sentinel-1 GRD product from the Planetary Computer public archive
and restore the original ESA SAFE file names so SNAP can read it.

Usage: python fetch_grd_from_pc.py <PC item id> <output dir> [--list-only]
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-grd"


def main(item_id: str, out_dir: str, list_only: bool = False) -> None:
    item = requests.get(STAC + item_id, timeout=60)
    item.raise_for_status()
    manifest_href = item.json()["assets"]["safe-manifest"]["href"]
    base = manifest_href.rsplit("/", 1)[0]                       # .../<PRODUCT>
    account_container, prefix = re.match(r"(https://[^/]+/[^/]+)/(.+)", base).groups()
    product = prefix.rsplit("/", 1)[1]
    token = requests.get(SAS, timeout=60).json()["token"]

    # list every blob in the product folder
    listing = requests.get(f"{account_container}?restype=container&comp=list&prefix={prefix}/&{token}", timeout=120)
    listing.raise_for_status()
    blobs = [(b.find("Name").text, int(b.find("Properties/Content-Length").text))
             for b in ET.fromstring(listing.content).iter("Blob")]
    total = sum(s for _, s in blobs)
    print(f"product {product}: {len(blobs)} files, {total / 1e6:.0f} MB")
    for name, size in blobs:
        print(f"  {size / 1e6:9.1f} MB  {name[len(prefix) + 1:]}")
    if list_only:
        return

    safe = Path(out_dir) / f"{product}.SAFE"
    for name, size in blobs:
        rel = name[len(prefix) + 1:]
        if rel.startswith("preview/") and not rel.endswith(".kml"):
            continue
        dst = safe / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size == size:
            continue
        with requests.get(f"{account_container}/{name}?{token}", stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(dst, "wb") as fh:
                for chunk in r.iter_content(1 << 22):
                    fh.write(chunk)
        print("downloaded", rel, flush=True)
    prepare_safe(safe)
    print("done:", safe)


def prepare_safe(safe: Path) -> None:
    """Make a downloaded product readable by SNAP exactly like the original ESA product."""
    restore_esa_names(safe)
    unwrap_cog_manifest(safe)
    decompress_measurements(safe)


def unwrap_cog_manifest(safe: Path) -> None:
    """The Copernicus COG repackaging wraps the processing history in manifest.safe in an extra
    "COG Conversion" entry (software "Sentinel-1 COGifier 001.00"). SNAP takes the first software
    version it finds as the ESA IPF version, so it sees 1.00 instead of e.g. 3.91 and silently
    switches to its pre-2018 code path (no azimuth noise vectors in thermal noise removal).
    Remove the wrapper so the manifest reads like the original ESA product."""
    manifest = safe / "manifest.safe"
    text = manifest.read_text(encoding="utf-8")
    outer = text.find('<safe:processing name="COG Conversion"')
    if outer < 0:
        return
    inner = text.find("<safe:processing", outer + 10)
    xml_end = text.find("</xmlData>", outer)
    inner_end = text.rfind("</safe:resource>", outer, xml_end)
    if inner < 0 or inner_end < 0 or not text[inner:inner_end].rstrip().endswith("</safe:processing>"):
        raise ValueError("unexpected manifest layout; cannot unwrap the COG Conversion entry")
    backup = safe / "manifest.safe.cog"
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    manifest.write_text(text[:outer] + text[inner:inner_end].rstrip() + text[xml_end:], encoding="utf-8")
    print("removed COG Conversion wrapper from manifest.safe")


def decompress_measurements(safe: Path) -> None:
    """Planetary Computer stores the measurement images as ZSTD-compressed COGs, which
    SNAP cannot read. Rewrite them as plain uncompressed TIFFs (lossless, as ESA ships them)."""
    import rasterio

    for tif in sorted((safe / "measurement").glob("*.tiff")):
        with rasterio.open(tif) as src:
            if src.compression is None:
                continue
            gcps, gcp_crs = src.gcps
            profile = dict(driver="GTiff", width=src.width, height=src.height, count=1,
                           dtype=src.dtypes[0], compress=None, tiled=False, blockysize=1)
            tmp = tif.with_suffix(".tmp.tiff")
            with rasterio.open(tmp, "w", **profile) as dst:
                if gcps:
                    dst.gcps = (gcps, gcp_crs)
                step = 2048
                for row in range(0, src.height, step):
                    win = rasterio.windows.Window(0, row, src.width, min(step, src.height - row))
                    dst.write(src.read(1, window=win), 1, window=win)
        tmp.replace(tif)
        print("decompressed", tif.name, flush=True)


def restore_esa_names(safe: Path) -> None:
    """Planetary Computer shortens file names (iw-vv.tiff); rename them back to the
    names listed in manifest.safe (s1a-iw-grd-vv-...-001.tiff), which SNAP expects."""
    manifest = (safe / "manifest.safe").read_text()
    for href in re.findall(r'href="\./((?:annotation|measurement)/[^"]+)"', manifest):
        folder, name = href.rsplit("/", 1)
        m = re.match(r"(calibration-|noise-|rfi-)?s1[a-d]-(\w+)-grd-(\w\w)-.*(\.\w+)$", name)
        prefix, swath, pol, ext = m.group(1) or "", m.group(2), m.group(3), m.group(4)
        short = safe / folder / f"{prefix}{swath}-{pol}{ext}"
        if short.exists():
            short.rename(safe / href)
            print("renamed", short.name, "->", name)


if __name__ == "__main__":
    if "--rename-only" in sys.argv:
        prepare_safe(Path(sys.argv[1]))
        sys.exit(0)
    main(sys.argv[1], sys.argv[2], "--list-only" in sys.argv)
