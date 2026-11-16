"""Stage only the part of a Sentinel-1 GRD scene that a lon/lat window needs.

A GRD scene is two uncompressed images of about 880 MB (VV and VH), stored row by row. For a small window
SNAP reads only the image rows around it, so only those rows are fetched (HTTP range requests); the rest of
the local file stays empty. Everything else in the product (annotation, noise and calibration tables,
manifest; about 8 MB) is fetched in full. A 5 x 5 km window needs about 6% of the scene.

The result was checked to be identical, byte for byte, to processing the complete scene
(scripts/partial_stage.py <SAFE dir> west south east north  runs that check on a local scene).
"""
import re
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

# Image rows fetched beyond the window on each side. SNAP's terrain correction and border noise removal read
# a little outside the window; the check in main() finds how many rows are needed and this is several
# times that.
MARGIN_LINES = 500
MAX_METADATA_BYTES = 20_000_000

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 16: 8}
_TYPE_FMT = {3: "H", 4: "I", 16: "Q"}


def tiff_strips(read, size: int):
    """Row layout of an uncompressed, row-organised TIFF: (width, height, rows per strip, strip offsets,
    strip byte counts). `read(start, end)` returns the bytes [start, end). None if the file is not such a
    TIFF (tiled or compressed, e.g. a Copernicus COG)."""
    head = read(0, 16)
    order = {b"II": "<", b"MM": ">"}.get(head[:2])
    if order is None:
        return None
    magic = struct.unpack(order + "H", head[2:4])[0]
    if magic == 42:
        ifd, n_fmt, entry, cnt_fmt, inline = struct.unpack(order + "I", head[4:8])[0], "H", 12, "I", 4
    elif magic == 43:
        ifd, n_fmt, entry, cnt_fmt, inline = struct.unpack(order + "Q", head[8:16])[0], "Q", 20, "Q", 8
    else:
        return None
    n_size = struct.calcsize(n_fmt)
    n = struct.unpack(order + n_fmt, read(ifd, ifd + n_size))[0]
    raw = read(ifd + n_size, ifd + n_size + n * entry)
    tags = {}
    for i in range(n):
        e = raw[i * entry:(i + 1) * entry]
        tag, typ = struct.unpack(order + "HH", e[:4])
        count = struct.unpack(order + cnt_fmt, e[4:4 + inline])[0]
        if tag not in (256, 257, 259, 273, 278, 279, 322) or typ not in _TYPE_FMT:
            continue
        nbytes = count * _TYPE_SIZE[typ]
        value = e[4 + inline:4 + 2 * inline]
        if nbytes > inline:
            offset = struct.unpack(order + cnt_fmt, value)[0]
            value = read(offset, offset + nbytes)
        tags[tag] = np.frombuffer(value[:nbytes], dtype=np.dtype(order + _TYPE_FMT[typ])).astype("int64")
    if 322 in tags or int(tags.get(259, [1])[0]) != 1 or 273 not in tags or 279 not in tags:
        return None
    width, height = int(tags[256][0]), int(tags[257][0])
    rows_per_strip = int(tags[278][0]) if 278 in tags else height
    return width, height, min(rows_per_strip, height), tags[273], tags[279]


def write_partial_tiff(read, size: int, dest: Path, first_line: int, last_line: int) -> int:
    """Write a local file of the same size as the remote TIFF holding its metadata and the image rows
    first_line..last_line (inclusive); other rows are zero. Returns the bytes fetched, or -1 if the file is
    not a row-organised uncompressed TIFF."""
    layout = tiff_strips(read, size)
    if layout is None:
        return -1
    width, height, rps, offsets, counts = layout
    data_start, data_end = int(offsets.min()), int((offsets + counts).max())
    if np.any(np.diff(offsets) <= 0) or data_start + (size - data_end) > MAX_METADATA_BYTES:
        return -1
    s0 = min(height - 1, max(0, first_line)) // rps
    s1 = max(0, min(height - 1, last_line)) // rps
    ranges = [(0, data_start), (int(offsets[s0]), int(offsets[s1] + counts[s1])), (data_end, size)]
    fetched = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.truncate(size)                       # a sparse file on Linux: the rows not fetched take no space
        for start, end in ranges:
            pos = start
            while pos < end:                   # in pieces, to keep memory low
                chunk = read(pos, min(end, pos + 64_000_000))
                f.seek(pos)
                f.write(chunk)
                pos += len(chunk)
                fetched += len(chunk)
    return fetched


def line_range(annotation_xml: str, bbox: tuple, margin: int = MARGIN_LINES) -> tuple:
    """First and last image row (inclusive, not clipped) that a lon/lat window (west, south, east, north)
    falls on, from the geolocation grid of the product annotation, plus the margin."""
    pts = re.findall(r"<geolocationGridPoint>.*?<line>(\d+)</line>.*?<latitude>([-\d.eE+]+)</latitude>"
                     r"\s*<longitude>([-\d.eE+]+)</longitude>", annotation_xml, flags=re.S)
    line, lat, lon = (np.array(v, dtype="float64") for v in zip(*pts))
    lon = np.unwrap(np.radians(lon), period=2 * np.pi)          # scenes across the date line
    lat = np.radians(lat)
    lon0, lat0 = lon.mean(), lat.mean()

    def terms(x, y):
        x, y = x - lon0, y - lat0
        return np.stack([np.ones_like(x), x, y, x * y, x * x, y * y], axis=-1)

    coef, *_ = np.linalg.lstsq(terms(lon, lat), line, rcond=None)
    fit_error = float(np.abs(terms(lon, lat) @ coef - line).max())
    w, s, e, n = bbox
    xs, ys = np.meshgrid(np.linspace(w, e, 5), np.linspace(s, n, 5))
    xs = np.radians(xs)
    xs = xs + 2 * np.pi * np.round((lon0 - xs) / (2 * np.pi))    # same branch as the unwrapped grid
    rows = terms(xs.ravel(), np.radians(ys.ravel())) @ coef
    pad = margin + fit_error
    return int(np.floor(rows.min() - pad)), int(np.ceil(rows.max() + pad))


def stage_window(files: dict, read, dest_safe: Path, bbox: tuple, margin: int = MARGIN_LINES) -> dict:
    """Build a local .SAFE holding what a window needs.
    files: {path inside the product: size in bytes}; read(path, start, end) returns bytes.
    Returns {"bytes": fetched, "of": product size, "lines": (first, last)}; "lines" is None if the product
    had to be fetched in full (measurement images not row-organised)."""
    fetched = 0
    annotation = None
    for rel, size in sorted(files.items()):
        if rel.startswith("measurement/") or rel.endswith("/") or size == 0:
            continue
        target = dest_safe / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read(rel, 0, size))
        fetched += size
        if re.fullmatch(r"annotation/s1.-iw-grd-v[vh]-[^/]*\.xml", rel) and annotation is None:
            annotation = target.read_text()
    first, last = line_range(annotation, bbox, margin)
    lines = (first, last)
    for rel, size in sorted(files.items()):
        if not rel.startswith("measurement/") or rel.endswith("/") or size == 0:
            continue
        got = write_partial_tiff(lambda a, b, rel=rel: read(rel, a, b), size, dest_safe / rel, first, last)
        if got < 0:                            # not row-organised: take the whole file
            lines = None
            with open(dest_safe / rel, "wb") as f:
                pos = 0
                while pos < size:
                    chunk = read(rel, pos, min(size, pos + 64_000_000))
                    f.write(chunk)
                    pos += len(chunk)
            got = size
        fetched += got
    return {"bytes": fetched, "of": sum(files.values()), "lines": lines}


def stage_window_from_folder(source_safe: Path, dest_safe: Path, bbox: tuple, margin: int = MARGIN_LINES) -> dict:
    """The same from a complete local product (for checking)."""
    files = {p.relative_to(source_safe).as_posix(): p.stat().st_size for p in source_safe.rglob("*") if p.is_file()}

    def read(rel, start, end):
        with open(source_safe / rel, "rb") as f:
            f.seek(start)
            return f.read(end - start)

    shutil.rmtree(dest_safe, ignore_errors=True)
    return stage_window(files, read, dest_safe, bbox, margin)


def main() -> None:
    """Check on a complete local scene: process the window from the complete product and from partial
    copies with several margins, and compare the outputs."""
    import subprocess

    import rasterio

    source = Path(sys.argv[1]).resolve()
    bbox = tuple(float(v) for v in sys.argv[2:6])
    margins = [int(v) for v in sys.argv[6:]] or [MARGIN_LINES]
    work = source.parent / "partial_check"
    scripts = Path(__file__).resolve().parent

    def process(safe: Path, out: Path):
        shutil.rmtree(out, ignore_errors=True)
        subprocess.run([sys.executable, str(scripts / "process_scene.py"), str(safe), str(out),
                        "--bbox", *map(str, bbox)], check=True, stdout=subprocess.DEVNULL)
        with rasterio.open(next(out.glob("*_sigma0.tif"))) as src:
            return src.read(), src.transform

    full, full_transform = process(source, work / "out_full")
    for margin in margins:
        partial = work / source.name
        info = stage_window_from_folder(source, partial, bbox, margin)
        got, transform = process(partial, work / "out_partial")
        same = got.shape == full.shape and transform == full_transform and np.array_equal(got, full, equal_nan=True)
        differing = int((got != full).sum()) if got.shape == full.shape else -1
        print(f"margin {margin:5d} lines: rows {info['lines']}, fetched {info['bytes'] / 1e6:7.1f} MB of "
              f"{info['of'] / 1e6:.0f} MB ({info['bytes'] / info['of']:.1%}); output "
              f"{'IDENTICAL to the full scene' if same else f'DIFFERENT ({differing} values)'}", flush=True)
        shutil.rmtree(partial, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
