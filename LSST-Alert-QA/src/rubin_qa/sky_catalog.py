"""
sky_catalog.py - local catalogue crossmatch for the transient monitor.

Legacy Surveys DR10 (NOIRLab Astro Data Lab TAP) is fetched in small tiles on
demand, cached on disk for good (the catalogue is static), and matched locally.
It replaces the brokers' catalogue matches as the filter: those are broker
specific, so switching broker would silently change what gets rejected, and
they are too shallow for Rubin (2MASS and VSX stop far above ~24 mag), so real
SNe in faint hosts come out "no match" while faint variable stars slip through.
DR10 reaches ~23-24 mag with morphology, Gaia astrometry, and WISE W1/W2.

Tiles, not the whole box: around the deep fields the boxes sit on, DR10 holds
~7M rows per 25 deg2, ~2M even at r < 24 (COSMOS and ECDFS, measured
2026-09-27). A candidate needs the few tiles around it.

Verdicts, strongest evidence first:
  stellar       a source within COUNTERPART_RADIUS with significant Gaia
                parallax or proper motion
  agn           the counterpart has WISE W1-W2 >= 0.8 (Vega; Stern et al. 2012),
                i.e. >= AGN_W1W2_AB_MIN in DR10's AB fluxes. WISE is shallow:
                a faint counterpart gets no AGN information, not "not AGN"
  host          a galaxy within HOST_DLR_MAX directional light radii
                (the DES SN host-matching measure, Gupta et al. 2016)
  point_source  the counterpart is point-like with no Gaia astrometry: a faint
                star, a QSO or a compact galaxy; weak, so never rejected on
  none          nothing: orphan
  unavailable   the catalogue could not be read (never a reason to reject)
"""

import io
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests

from .config import PROJECT_ROOT

TAP_URL = "https://datalab.noirlab.edu/tap/sync"
TABLE = "ls_dr10.tractor"
COLUMNS = (
    "ra", "dec", "type", "flux_g", "flux_r", "flux_z", "flux_w1", "flux_w2",
    "flux_ivar_w1", "flux_ivar_w2", "shape_r", "shape_e1", "shape_e2",
    "parallax", "parallax_ivar", "pmra", "pmra_ivar", "pmdec", "pmdec_ivar",
)
TILE_DEG = 0.1  # ~3000 rows at deep-field depth
TAP_TIMEOUT = 120.0
CACHE_DIR = PROJECT_ROOT / "cache" / "ls_dr10"
NMGY_ZP = 22.5  # DR10 fluxes are nanomaggies: m_AB = 22.5 - 2.5 log10(flux)

SEARCH_RADIUS_ARCSEC = 30.0
COUNTERPART_RADIUS_ARCSEC = 1.0
HOST_DLR_MAX = 4.0
MIN_HALF_LIGHT_ARCSEC = 0.2  # floor on shape_r so a tiny galaxy cannot claim nothing
PARALLAX_SNR_MIN = 5.0
PM_SNR_MIN = 5.0
WISE_SNR_MIN = 5.0
# W1-W2 >= 0.8 Vega; Vega->AB offsets 2.699 (W1) and 3.339 (W2) move it to 0.16 AB
AGN_W1W2_AB_MIN = 0.16
GALAXY_TYPES = {"REX", "EXP", "DEV", "SER"}


class CatalogError(RuntimeError):
    pass


@dataclass
class Crossmatch:
    verdict: str  # stellar | agn | host | point_source | none | unavailable
    evidence: list[str] = field(default_factory=list)


def nmgy_to_mag(flux: float) -> float | None:
    return NMGY_ZP - 2.5 * math.log10(flux) if flux and flux > 0 else None


def tile_of(ra: float, dec: float) -> tuple[int, int]:
    return int(math.floor((ra % 360) / TILE_DEG)), int(math.floor((dec + 90) / TILE_DEG))


def tiles_around(ra: float, dec: float, radius_arcsec: float) -> set[tuple[int, int]]:
    r = radius_arcsec / 3600
    dra = min(180.0, r / max(math.cos(math.radians(dec)), 1e-6))
    n_ra = round(360 / TILE_DEG)
    j0, j1 = tile_of(ra, max(-90.0, dec - r))[1], tile_of(ra, min(89.999999, dec + r))[1]
    i0 = int(math.floor((ra - dra) / TILE_DEG))
    i1 = int(math.floor((ra + dra) / TILE_DEG))
    return {(i % n_ra, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}


def tile_query(i: int, j: int) -> str:
    ra0, dec0 = i * TILE_DEG, j * TILE_DEG - 90
    return (
        f"SELECT {', '.join(COLUMNS)} FROM {TABLE} "
        f"WHERE ra >= {ra0:.6f} AND ra < {ra0 + TILE_DEG:.6f} "
        f"AND dec >= {dec0:.6f} AND dec < {dec0 + TILE_DEG:.6f} AND type != 'DUP'"
    )


def fetch_tile(i: int, j: int, get=None) -> pd.DataFrame:
    """One tile, from the disk cache or, once, from the TAP service."""
    path = CACHE_DIR / f"{i}_{j}.csv.gz"
    if path.exists():
        return pd.read_csv(path)
    get = get or requests.get
    try:
        r = get(TAP_URL, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                 "QUERY": tile_query(i, j)}, timeout=TAP_TIMEOUT)
    except requests.RequestException as e:
        raise CatalogError(f"tile {i},{j}: {type(e).__name__}") from e
    text = r.text
    # the service can answer 200 with a VOTable error document instead of CSV
    if r.status_code != 200 or not text.startswith("ra,"):
        raise CatalogError(f"tile {i},{j}: HTTP {r.status_code} {text[:80]!r}")
    df = pd.read_csv(io.StringIO(text))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, compression="gzip")
    tmp.rename(path)
    return df


def neighbours(ra: float, dec: float, radius_arcsec: float = SEARCH_RADIUS_ARCSEC, fetch=None) -> pd.DataFrame:
    """Catalogue rows within radius, with sep_arcsec and pa_deg (from the row to the target, E of N)."""
    fetch = fetch or fetch_tile
    tiles = [fetch(i, j) for i, j in sorted(tiles_around(ra, dec, radius_arcsec))]
    df = pd.concat([t for t in tiles if len(t)], ignore_index=True) if any(len(t) for t in tiles) else pd.DataFrame(columns=COLUMNS)
    if df.empty:
        df.attrs["tile_rows"] = 0
        return df.assign(sep_arcsec=[], pa_deg=[])
    r1, d1 = np.radians(df["ra"].to_numpy(float)), np.radians(df["dec"].to_numpy(float))
    r2, d2 = math.radians(ra), math.radians(dec)
    cos_sep = np.sin(d1) * math.sin(d2) + np.cos(d1) * math.cos(d2) * np.cos(r2 - r1)
    sep = np.degrees(np.arccos(np.clip(cos_sep, -1, 1))) * 3600
    east = math.cos(d2) * np.sin(r2 - r1)
    north = np.cos(d1) * math.sin(d2) - np.sin(d1) * math.cos(d2) * np.cos(r2 - r1)
    pa = np.degrees(np.arctan2(east, north)) % 360
    out = df.assign(sep_arcsec=sep, pa_deg=pa)
    out = out[out["sep_arcsec"] <= radius_arcsec].sort_values("sep_arcsec").reset_index(drop=True)
    out.attrs["tile_rows"] = len(df)
    return out


def directional_light_radius(shape_r: float, e1: float, e2: float, pa_to_target_deg: float) -> float:
    """
    The galaxy's light radius in the direction of the target (arcsec).
    DR10: |e| = hypot(e1, e2), axis ratio b/a = (1-|e|)/(1+|e|), major-axis
    position angle 0.5*atan2(e2, e1), taken here as East of North.
    """
    a = max(float(shape_r), MIN_HALF_LIGHT_ARCSEC)
    e = min(math.hypot(e1, e2), 0.999)
    b = a * (1 - e) / (1 + e)
    phi = 0.5 * math.atan2(e2, e1)
    d = math.radians(pa_to_target_deg) - phi
    return a * b / math.sqrt((a * math.sin(d)) ** 2 + (b * math.cos(d)) ** 2)


def _snr(value, ivar) -> float:
    return abs(value) * math.sqrt(ivar) if ivar and ivar > 0 else 0.0


def _gaia_star(row) -> str | None:
    plx = _snr(row["parallax"], row["parallax_ivar"])
    pm = math.hypot(_snr(row["pmra"], row["pmra_ivar"]), _snr(row["pmdec"], row["pmdec_ivar"]))
    if plx >= PARALLAX_SNR_MIN:
        return f'Gaia parallax/err={plx:.1f} at {row["sep_arcsec"]:.1f}"'
    if pm >= PM_SNR_MIN:
        return f'Gaia proper motion {pm:.1f} sigma at {row["sep_arcsec"]:.1f}"'
    return None


def _agn_colour(row) -> str | None:
    w1, w2 = row["flux_w1"], row["flux_w2"]
    if _snr(w1, row["flux_ivar_w1"]) < WISE_SNR_MIN or _snr(w2, row["flux_ivar_w2"]) < WISE_SNR_MIN:
        return None
    if w1 <= 0 or w2 <= 0:
        return None
    colour = -2.5 * math.log10(w1 / w2)
    return f"W1-W2={colour:.2f} AB" if colour >= AGN_W1W2_AB_MIN else None


def _label(row) -> str:
    mag = nmgy_to_mag(row["flux_r"])
    return f'{row["type"]} r={mag:.1f}' if mag is not None else f'{row["type"]} r=?'


def classify(ra: float, dec: float, fetch=None) -> Crossmatch:
    try:
        rows = neighbours(ra, dec, SEARCH_RADIUS_ARCSEC, fetch)
    except CatalogError as e:
        return Crossmatch("unavailable", [str(e)])
    if rows.attrs.get("tile_rows", 0) == 0:
        return Crossmatch("unavailable", ["no DR10 sources in the surrounding tiles (outside coverage?)"])

    near = rows[rows["sep_arcsec"] <= COUNTERPART_RADIUS_ARCSEC]
    for _, row in near.iterrows():
        star = _gaia_star(row)
        if star:
            return Crossmatch("stellar", [star])
    counterpart = near.iloc[0] if len(near) else None
    if counterpart is not None:
        agn = _agn_colour(counterpart)
        if agn:
            return Crossmatch("agn", [f'{_label(counterpart)} at {counterpart["sep_arcsec"]:.1f}", {agn}'])

    best = None
    for _, row in rows[rows["type"].isin(GALAXY_TYPES)].iterrows():
        dlr = directional_light_radius(row["shape_r"], row["shape_e1"], row["shape_e2"], row["pa_deg"])
        d = row["sep_arcsec"] / dlr
        if d <= HOST_DLR_MAX and (best is None or d < best[0]):
            best = (d, row)
    if best is not None:
        d, row = best
        return Crossmatch("host", [f'{_label(row)} sep={row["sep_arcsec"]:.1f}" d_DLR={d:.1f}'])

    if counterpart is not None and counterpart["type"] == "PSF":
        return Crossmatch("point_source", [f'{_label(counterpart)} at {counterpart["sep_arcsec"]:.1f}", no Gaia astrometry'])
    return Crossmatch("none")
