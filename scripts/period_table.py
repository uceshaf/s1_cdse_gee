"""Write the per-scene result table of the README ("Older periods") from data/orbit_rule_*.json.

Usage: python period_table.py            (rewrites the rows between the period-rows markers in README.md)
"""
import glob
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLED = {"snap604": "6.0.4", "snap7": "7.0.0", "snap803": "8.0.3", "snap9": "9.0.0", "snap11": "11.0.1"}
BY_GEE = {"6.0.4": "11.0.1", "7.0.3": "7.0.0", "8.0.1": "8.0.3", "8.0.3": "8.0.3", "9.0.0": "9.0.0",
          "11.0.0": "11.0.1", "11.0.1": "11.0.1"}


def main() -> None:
    scenes = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "orbit_rule*.json"))):
        if "snap600" in path:           # the 6.0.0 installer build crashes; superseded by the 6.0.4 runs
            continue
        for scene, rec in json.loads(Path(path).read_text()).items():
            cands = [c for c in rec.get("candidates", []) if c.get("vv_bit_identical_pct") is not None]
            if not cands:
                continue
            best = max(c["vv_bit_identical_pct"] for c in cands)
            if scene not in scenes or best > scenes[scene][1]:
                scenes[scene] = (rec, best, cands)
    rows = []
    for scene, (rec, best, cands) in sorted(scenes.items(), key=lambda kv: kv[0].split("_")[4]):
        parts = scene.split("_")
        start = datetime.strptime(parts[4], "%Y%m%dT%H%M%S")
        used = INSTALLED.get(rec.get("snap_used"), BY_GEE.get(rec.get("snap"), "?"))
        ranked = sorted(cands, key=lambda c: c["generated"])
        if best > 99.99:
            k = next(i for i, c in enumerate(ranked) if c["vv_bit_identical_pct"] == best)
            which = f"{['first', 'second', 'third'][k]} of {len(ranked)}"
            result = "100%"
        else:
            which = "none matches"
            result = f"0% ({max(c['vv_within_1dn_pct'] for c in cands):.0f}% within 1 DN)"
        rows.append(f"| {parts[0]} {start:%Y-%m-%d %H:%M} `{parts[-1]}` | {rec.get('snap')} | {used} | {result} | {which} |")
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    a, b = "<!-- period-rows:start -->\n", "<!-- period-rows:end -->"
    i, j = text.index(a) + len(a), text.index(b)
    readme.write_text(text[:i] + "\n".join(rows) + "\n" + text[j:], encoding="utf-8")
    exact = sum(1 for _, (_, best, _) in scenes.items() if best > 99.99)
    print(f"{len(rows)} scenes, {exact} bit-identical")


if __name__ == "__main__":
    main()
