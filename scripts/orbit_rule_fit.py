"""Summarise the orbit-file experiments: generation time of each candidate relative to the product's
publication in the Copernicus catalogue, and which one Google Earth Engine used (marked *).

Usage: python orbit_rule_fit.py
"""
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orbit_rule import choose, publication_time  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "publication_times.json"


def main() -> None:
    pubs = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    scenes = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "orbit_rule*.json"))):
        for scene, rec in json.loads(Path(path).read_text()).items():
            cands = [c for c in rec.get("candidates", []) if c.get("vv_bit_identical_pct") is not None]
            if cands and (scene not in scenes or max(c["vv_bit_identical_pct"] for c in cands) > 99.99):
                scenes[scene] = rec
                if rec.get("published"):
                    pubs[scene] = rec["published"]
    right = total = 0
    for scene, rec in sorted(scenes.items(), key=lambda kv: kv[0].split("_")[4]):
        if scene not in pubs:
            try:
                pubs[scene] = publication_time(scene).isoformat()
            except Exception:
                continue
            CACHE.write_text(json.dumps(pubs, indent=1))
        pub = datetime.fromisoformat(pubs[scene])
        parts = scene.split("_")
        sensed = datetime.strptime(parts[4], "%Y%m%dT%H%M%S")
        cands = sorted(({**c, "generated": datetime.fromisoformat(c["generated"])} for c in rec["candidates"]
                        if c.get("vv_bit_identical_pct") is not None), key=lambda c: c["generated"])
        used = [c for c in cands if c["vv_bit_identical_pct"] > 99.99]
        rel = "  ".join(f"{(c['generated'] - pub).total_seconds() / 60:+.0f}{'*' if c in used else ''}" for c in cands)
        verdict = "no candidate matches"
        if used:
            total += 1
            ok = choose(cands, pub, scene)["file"] == used[0]["file"]
            right += ok
            verdict = "rule ok" if ok else "RULE WRONG"
        print(f"{parts[0]} {sensed:%Y-%m-%d %H:%M} GEE {rec.get('snap'):7} published {(pub - sensed).total_seconds() / 60:+5.0f} min after sensing"
              f" | candidates vs publication: {rel:22} | {verdict}")
    print(f"rule right for {right} of {total} scenes")


if __name__ == "__main__":
    main()
