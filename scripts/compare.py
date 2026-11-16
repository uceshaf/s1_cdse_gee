"""Compare candidate Sentinel-1 backscatter chips against Google Earth Engine reference chips.

The acceptance metric is the WorldCereal 16-bit scaling of Sentinel-1:
    DN = 10 ** ((sigma0_dB + 83) / 20)        (stored as uint16, 1..65535)
A pixel passes when |DN_candidate - DN_GEE| <= 1.

Usage:
    python compare.py <gee_chips.json> <candidate dir> <tag> [--json out.json]

Candidate files are <candidate dir>/<tag>_<chip>.tif with band 1 = sigma0 VV, band 2 = sigma0 VH
(linear), on the same CRS. Grid alignment with GEE is checked and reported, never silently fixed.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio

SLOPE_CLASSES = [("flat <5 deg", 0, 5), ("moderate 5-20 deg", 5, 20), ("steep >20 deg", 20, 91)]


def to_dn(linear: np.ndarray) -> np.ndarray:
    """WorldCereal uint16 scaling, kept as float so sub-DN differences stay visible."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 ** ((10.0 * np.log10(linear) + 83.0) / 20.0)


def stats(dn_cand: np.ndarray, dn_ref: np.ndarray, lin_cand: np.ndarray, lin_ref: np.ndarray) -> dict:
    if dn_ref.size == 0:
        return {"n": 0}
    d = dn_cand - dn_ref
    a = np.abs(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(lin_cand / lin_ref)
    db = db[np.isfinite(db)]
    rounded = np.abs(np.clip(np.rint(dn_cand), 1, 65535) - np.clip(np.rint(dn_ref), 1, 65535))
    return {
        "n": int(d.size),
        "pct_within_1dn": float((a <= 1.0).mean() * 100),
        "pct_within_1dn_rounded": float((rounded <= 1).mean() * 100),
        "pct_identical_rounded": float((rounded == 0).mean() * 100),
        "pct_bit_identical_float32": float((lin_cand.astype("float32") == lin_ref.astype("float32")).mean() * 100),
        "median_abs_dn": float(np.median(a)),
        "p99_abs_dn": float(np.percentile(a, 99)),
        "max_abs_dn": float(a.max()),
        "bias_dn": float(d.mean()),
        "median_abs_db": float(np.median(np.abs(db))) if db.size else None,
        "p99_abs_db": float(np.percentile(np.abs(db), 99)) if db.size else None,
    }


def compare_chip(ref_path: Path, cand_path: Path) -> dict:
    with rasterio.open(ref_path) as ref, rasterio.open(cand_path) as cand:
        ref_data = ref.read()
        rt, ct = ref.transform, cand.transform
        result = {
            "candidate": cand_path.name,
            "same_crs": ref.crs == cand.crs,
            "same_shape": (ref.width, ref.height) == (cand.width, cand.height),
            "origin_offset_m": [ct.c - rt.c, ct.f - rt.f],
            "pixel_size": [ct.a, ct.e],
            "candidate_bands": list(cand.descriptions),
        }
        aligned = (result["same_crs"] and result["same_shape"]
                   and abs(ct.c - rt.c) < 1e-3 and abs(ct.f - rt.f) < 1e-3
                   and abs(ct.a - rt.a) < 1e-9 and abs(ct.e - rt.e) < 1e-9)
        result["grid_identical"] = bool(aligned)
        if not aligned:
            return result
        cand_data = cand.read().astype("float64")

    valid_ref = ref_data[4] > 0
    slope = ref_data[5]
    result["bands"] = {}
    for i, pol in enumerate(["VV", "VH"]):
        lin_ref, lin_cand = ref_data[i], cand_data[i]
        valid_cand = np.isfinite(lin_cand) & (lin_cand > 0)
        both = valid_ref & valid_cand & (lin_ref > 0)
        dn_ref, dn_cand = to_dn(lin_ref), to_dn(lin_cand)
        band = {
            "valid_in_gee_only": int((valid_ref & ~valid_cand).sum()),
            "valid_in_candidate_only": int((~valid_ref & valid_cand).sum()),
            "all": stats(dn_cand[both], dn_ref[both], lin_cand[both], lin_ref[both]),
        }
        for label, lo, hi in SLOPE_CLASSES:
            m = both & (slope >= lo) & (slope < hi)
            band[label] = stats(dn_cand[m], dn_ref[m], lin_cand[m], lin_ref[m])
        result["bands"][pol] = band
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("chips", type=Path)
    ap.add_argument("candidate_dir", type=Path)
    ap.add_argument("tag")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    meta = json.loads(args.chips.read_text())
    report = {}
    for name, chip in meta["chips"].items():
        cand = args.candidate_dir / f"{args.tag}_{name}.tif"
        if not cand.exists():
            print(f"[{name}] missing candidate {cand}")
            continue
        r = compare_chip(args.chips.parent / chip["file"], cand)
        report[name] = r
        print(f"\n[{name}] grid identical: {r['grid_identical']}  origin offset (m): "
              f"{r['origin_offset_m'][0]:.6f}, {r['origin_offset_m'][1]:.6f}  bands: {r['candidate_bands']}")
        if not r["grid_identical"]:
            print(f"   same_crs={r['same_crs']} same_shape={r['same_shape']} pixel_size={r['pixel_size']}")
            continue
        for pol, band in r["bands"].items():
            print(f"  {pol}: valid only in GEE={band['valid_in_gee_only']}, only in candidate={band['valid_in_candidate_only']}")
            for label in ["all"] + [c[0] for c in SLOPE_CLASSES]:
                s = band[label]
                if not s.get("n"):
                    continue
                print(f"    {label:18s} n={s['n']:8d}  within 1 DN: {s['pct_within_1dn']:6.2f}%  "
                      f"bit-identical: {s['pct_bit_identical_float32']:6.2f}%  "
                      f"|dDN| median={s['median_abs_dn']:.3f} p99={s['p99_abs_dn']:.2f} max={s['max_abs_dn']:.1f}  "
                      f"|dB| median={s['median_abs_db']:.5f} p99={s['p99_abs_db']:.4f}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
