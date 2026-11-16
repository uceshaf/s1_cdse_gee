"""Plot candidate-minus-GEE difference maps (linear sigma0). Usage: plot_diff.py <tag> <out png> [band order e.g. VH,VV]"""
import sys
import numpy as np
import rasterio
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

tag, out = sys.argv[1], sys.argv[2]
order = (sys.argv[3] if len(sys.argv) > 3 else "VV,VH").split(",")
fig, ax = plt.subplots(2, 2, figsize=(13, 12))
for i, n in enumerate(["flat", "steep"]):
    ref = rasterio.open(f"data/ref/gee_{n}.tif").read()
    c = rasterio.open(f"data/snap/{tag}_{n}.tif").read().astype("float64")
    for j, pol in enumerate(["VV", "VH"]):
        d = c[order.index(pol)] - ref[j]
        im = ax[i, j].imshow(d, vmin=np.percentile(d, 1), vmax=np.percentile(d, 99), cmap="viridis")
        ax[i, j].set_title(f"{n} {pol}: candidate minus GEE (linear sigma0)")
        plt.colorbar(im, ax=ax[i, j], fraction=0.046)
plt.tight_layout()
plt.savefig(out, dpi=70)
