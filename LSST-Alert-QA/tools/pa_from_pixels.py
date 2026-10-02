"""
Measure a galaxy's major-axis position angle straight from Legacy Surveys pixels.

The independent anchor for the DR10 ellipse conventions: the image itself. A FITS
cutout (r band) is background-subtracted, the pixels connected to the galaxy's
centre above SEG_SIGMA are kept (so neighbours do not pull the moments), and the
flux-weighted second moments give the major axis in pixel coordinates. The FITS
WCS (CD matrix) turns that into a sky PA, East of North. No catalogue convention
(shape_e1/e2, atlas pa, HyperLeda) is involved, so the result can arbitrate between
them.

The expected angles of the ellipse fixtures in tests/test_sky_catalog.py come from
this script; rerun it to regenerate them (new fixtures, a new Legacy release or atlas
version). It is a standalone tool: nothing in the monitor imports it.

Usage:
    python tools/pa_from_pixels.py RA DEC [RA DEC ...]       # PA, b/a per position
    python tools/pa_from_pixels.py --radius 62.3 RA DEC      # cutout sized for a 62" galaxy
    python tools/pa_from_pixels.py --check FILE.csv           # compare with catalogue
        (FILE.csv: ra, dec, shape_r, shape_e1, shape_e2[, atlas_pa])
"""

from __future__ import annotations

import argparse
import io
import math
import sys
from collections import deque

import numpy as np
import requests
from astropy.io import fits

CUTOUT_URL = "https://www.legacysurvey.org/viewer/cutout.fits"
LAYER = "ls-dr10"
PIXSCALE = 0.262  # arcsec, native DECam/Legacy pixel
MIN_SIZE_PIX = 64
MAX_SIZE_PIX = 512
SIZE_PER_RADIUS = 8  # cutout width in units of the galaxy's catalogue radius
SEG_SIGMA = 3.0      # keep pixels above this many background sigmas
REQUEST_TIMEOUT = 60


def fetch_cutout(ra: float, dec: float, size_pix: int, band: str = "r") -> tuple[np.ndarray, np.ndarray]:
    """(image, CD matrix) of a FITS cutout centred on ra, dec."""
    r = requests.get(CUTOUT_URL, params={"ra": ra, "dec": dec, "layer": LAYER, "pixscale": PIXSCALE,
                                         "size": size_pix, "bands": band}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    hdu = fits.open(io.BytesIO(r.content))[0]
    h = hdu.header
    cd = np.array([[h.get("CD1_1", 0.0), h.get("CD1_2", 0.0)], [h.get("CD2_1", 0.0), h.get("CD2_2", 0.0)]])
    data = np.asarray(hdu.data, dtype=float)
    return (data[0] if data.ndim == 3 else data), cd


def segment(img: np.ndarray, threshold: float) -> np.ndarray:
    """Boolean mask of the pixels above threshold connected (4-neighbour) to the central pixel."""
    ny, nx = img.shape
    mask = np.zeros_like(img, dtype=bool)
    seed = (ny // 2, nx // 2)
    if not img[seed] > threshold:
        return mask
    queue = deque([seed])
    mask[seed] = True
    while queue:
        y, x = queue.popleft()
        for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= yy < ny and 0 <= xx < nx and not mask[yy, xx] and img[yy, xx] > threshold:
                mask[yy, xx] = True
                queue.append((yy, xx))
    return mask


def moments_pa(img: np.ndarray, cd: np.ndarray) -> tuple[float, float, int]:
    """(major-axis PA deg East of North, b/a, pixels used) from second moments of the central segment."""
    finite = img[np.isfinite(img)]
    bg = np.median(finite)
    sigma = 1.4826 * np.median(np.abs(finite - bg))
    sub = np.nan_to_num(img - bg)
    mask = segment(sub, SEG_SIGMA * sigma)
    if mask.sum() < 10:
        return math.nan, math.nan, int(mask.sum())
    y, x = np.nonzero(mask)
    w = sub[y, x]
    xm, ym = np.average(x, weights=w), np.average(y, weights=w)
    ixx = np.average((x - xm) ** 2, weights=w)
    iyy = np.average((y - ym) ** 2, weights=w)
    ixy = np.average((x - xm) * (y - ym), weights=w)
    theta = 0.5 * math.atan2(2 * ixy, ixx - iyy)  # major axis, from +x (FITS column) towards +y (row)
    lam = np.linalg.eigvalsh(np.array([[ixx, ixy], [ixy, iyy]]))
    # FITS pixel step -> (dRA cos dec, dDec): RA increasing is East
    east, north = cd @ np.array([math.cos(theta), math.sin(theta)])
    return math.degrees(math.atan2(east, north)) % 180, math.sqrt(lam[0] / lam[1]), int(mask.sum())


def measure(ra: float, dec: float, radius_arcsec: float = 5.0) -> tuple[float, float, int]:
    size = int(min(MAX_SIZE_PIX, max(MIN_SIZE_PIX, SIZE_PER_RADIUS * radius_arcsec / PIXSCALE)))
    img, cd = fetch_cutout(ra, dec, size)
    return moments_pa(img, cd)


def pa_difference(a: float, b: float) -> float:
    """Smallest difference of two axis angles (mod 180), in degrees."""
    return abs((a - b + 90) % 180 - 90)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("coords", nargs="*", type=float, help="RA DEC pairs (deg)")
    p.add_argument("--radius", type=float, default=5.0,
                   help="galaxy major-axis radius in arcsec; the cutout is SIZE_PER_RADIUS times it (default 5)")
    p.add_argument("--check", help="CSV with ra, dec, shape_r, shape_e1, shape_e2[, atlas_pa]")
    args = p.parse_args()
    if args.check:
        import pandas as pd
        df = pd.read_csv(args.check)
        for _, g in df.iterrows():
            sma = g.get("atlas_sma")
            radius = float(sma) if pd.notna(sma) and sma > 0 else float(g.shape_r)  # NaN must not reach max()
            pa, ba, n = measure(g.ra, g.dec, radius)
            code = math.degrees(0.5 * math.atan2(g.shape_e2, g.shape_e1)) % 180
            line = (f"{g.ra:10.5f} {g.dec:+9.5f}  pixels PA {pa:6.1f} b/a {ba:4.2f} ({n:5d} px)   "
                    f"code 0.5*atan2(e2,e1) {code:6.1f} (diff {pa_difference(pa, code):4.1f})   "
                    f"docs 180-phi {(180 - code) % 180:6.1f} (diff {pa_difference(pa, 180 - code):4.1f})")
            if "atlas_pa" in g and g.atlas_pa == g.atlas_pa:
                line += f"   atlas pa {g.atlas_pa:6.1f} (diff {pa_difference(pa, g.atlas_pa):4.1f})"
            print(line, flush=True)
        return 0
    if len(args.coords) % 2:
        p.error("coordinates come in RA DEC pairs")
    for ra, dec in zip(args.coords[::2], args.coords[1::2]):
        pa, ba, n = measure(ra, dec, args.radius)
        print(f"{ra:10.5f} {dec:+9.5f}  PA {pa:6.1f}  b/a {ba:4.2f}  ({n} px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
