"""Diagnostic: does SNAP's ThermalNoiseRemoval react to the noiseAzimuthLut in the annotation?
Temporarily rewrites the VV noise annotation (always restored), runs the debug noise graph, prints the noise band."""
import glob, os, re, shutil, subprocess, sys
import numpy as np, rasterio

noise = glob.glob("data/*.SAFE/annotation/calibration/noise-*vv*.xml")[0]
backup = noise + ".orig"

def run(label):
    subprocess.run([os.path.abspath("tools/snap11/bin/gpt.exe"), os.path.abspath("data/snap/dbg_noise_graph.xml")], capture_output=True, text=True)
    a = rasterio.open("data/snap/dbg_noise.tif").read(2).astype("float64")
    print(f"{label:28s} noise band^2 at col 32, lines 0,400,..: {np.round(a[::400, 32] ** 2, 1)}")

shutil.copyfile(noise, backup)
try:
    text = open(backup).read()
    run("original")
    def scale(m):
        vals = " ".join(f"{float(v) * 1.5:.6e}" for v in m.group(2).split())
        return m.group(1) + vals + m.group(3)
    open(noise, "w").write(re.sub(r"(<noiseAzimuthLut[^>]*>)([^<]+)(</noiseAzimuthLut>)", scale, text))
    run("azimuth LUT x1.5")
    def ramp(m):
        vals = m.group(2).split()
        return m.group(1) + " ".join(f"{1.0 + i / len(vals):.6e}" for i in range(len(vals))) + m.group(3)
    open(noise, "w").write(re.sub(r"(<noiseAzimuthLut[^>]*>)([^<]+)(</noiseAzimuthLut>)", ramp, text))
    run("azimuth LUT ramp 1..2")
finally:
    shutil.move(backup, noise)
    print("annotation restored")
