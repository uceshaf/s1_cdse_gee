"""Tabulate the orbit-rule experiment: candidate generation times relative to the product's
ESA completion time and its Copernicus catalogue publication time. Usage: orbit_rule_table.py <results.json>"""
import json
import sys
from datetime import datetime

import requests

results = json.load(open(sys.argv[1]))
URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
rows = []
for sid, rec in results.items():
    if "error" in rec:
        continue
    r = requests.get(URL, params={"$filter": f"startswith(Name,'{sid}')", "$top": 5}, timeout=90).json()["value"]
    pub = min((p["PublicationDate"] for p in r), default=None)
    orig = min((p["OriginDate"] for p in r), default=None)
    fin = datetime.fromisoformat(rec["finished"])
    pubt = datetime.fromisoformat(pub[:19]) if pub else None
    origt = datetime.fromisoformat(orig[:19]) if orig else None
    cands = rec["candidates"]
    used = [i for i, c in enumerate(cands) if (c.get("vv_bit_identical_pct") or 0) > 99.9]
    desc = []
    for i, c in enumerate(cands):
        gen = datetime.fromisoformat(c["generated"])
        rel_pub = f"{(gen - pubt).total_seconds() / 60:+.0f}" if pubt else "?"
        desc.append(f"{'*' if i in used else ' '}gen {gen:%H:%M} (fin{c['generated_minus_finished_min']:+d}, pub{rel_pub})")
    print(f"{sid[:3]} {sid[17:32]} fin {fin:%H:%M} origin {origt:%d %H:%M} pub {pubt:%d %H:%M} | " + "   ".join(desc))
