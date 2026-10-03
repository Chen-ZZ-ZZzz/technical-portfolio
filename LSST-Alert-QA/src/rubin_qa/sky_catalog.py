"""
sky_catalog.py - local catalogue crossmatch for the transient monitor.

Legacy Surveys DR10 (NOIRLab Astro Data Lab TAP) is fetched in small tiles on
demand, cached on disk for good (the catalogue is static), and matched locally.
It replaces the brokers' catalogue matches as the filter: those are broker
specific, so switching broker would silently change what gets rejected, and
they are too shallow for Rubin (2MASS and VSX stop far above ~24 mag), so real
SNe in faint hosts come out "no match" while faint variable stars slip through.
DR10 reaches ~23-24 mag with morphology, Gaia astrometry, and WISE W1/W2.

Tiles, not the whole footprint: around the deep fields the footprints sit on, DR10
holds ~7M rows per 25 deg2, ~2M even at r < 24 (COSMOS and ECDFS, measured
2026-09-27). A candidate needs the few tiles around it. The 0.1 deg tiles are how
the catalogue is fetched, not a footprint shape; footprints are cones everywhere.

Large galaxies: DR10 fits the Siena Galaxy Atlas (SGA-2020) galaxies separately
(REF_CAT "L3"). The galaxy itself carries REF_ID = its SGA_ID. Every other source
inside its ellipse is a piece of it: the extended ones are "L3" with REF_ID -1
(frozen with the galaxy), the point-like ones are ordinary rows fitted on top
(REF_CAT empty) - NGC 873's nucleus is one, a PSF 1.3" from the galaxy model's
centre. What they share is maskbit 12 (inside an atlas ellipse). Pieces belong to
their galaxy in every rule: they are not host candidates, not point sources, not
point sources; a transient on one reaches the galaxy (NGC 873's nucleus -> host
NGC 873, flagged nuclear). The atlas galaxy uses the atlas ellipse (sga2020.ellipse,
joined into every tile).

Host candidates are collected in two steps, because no fixed search radius is both
small enough to be cheap and large enough for a galaxy arcminutes across (SN 2025zi
is 123" from NGC 1398):
  1. Tractor extended sources within SEARCH_RADIUS_ARCSEC (0.1 deg tiles), and
  2. every atlas galaxy whose scaled ellipse reaches the target, i.e.
     separation < HOST_DLR_MAX x its major-axis radius. The atlas is small (~900
     galaxies per footprint with its margin), so footprint_atlas() fetches all of it
     for the footprint cone plus ATLAS_MARGIN_DEG in one query, cached, and step 2 is
     an in-memory filter; the caller passes it to classify(atlas=...).

Catalogue gaps: where DR10's fitting gave up (maskbit BAILOUT) no sources were
written at all, so an empty sky there says nothing. Measured on brick 0532m280 at the
CDFS centre: 49% of its pixels BAILOUT, and its empty 36" cells are 97% BAILOUT. A
position with any BAILOUT pixel within step 1's search radius (GAP_RADIUS_ARCSEC), or
with part of that area in no DR10 brick, is "unavailable, catalogue gap", never an
orphan; the maskbits images come one per brick, cached, and every brick the search
circle overlaps is read.

Caches are keyed on the catalogue versions the rows come from (CATALOG_VERSION, from
TABLE and ATLAS_TABLE): pointing either at a new release refetches instead of mixing
versions.
Step 2 admits exactly the atlas galaxies that could have d_DLR <= HOST_DLR_MAX,
since the light radius in any direction is at most the major-axis radius.

Ellipse conventions, checked 2026-10-01 rather than taken from the docs, because
the docs disagree with themselves:
  - shape_r is the half-light radius along the major axis (Tractor's
    EllipseE.getRaDecBasis maps the unit circle to semi-axes shape_r and
    shape_r*b/a), not a circularised radius.
  - the major axis lies at 0.5*atan2(e2, e1), East of North. The DR10 catalogue
    page gives PA = 180 - that, and the atlas table calls its own pa "clockwise
    from North"; both are wrong in the sense that matters. 609 HyperLeda PAs
    (North through East) match the code's convention (97% within 15 deg, the
    mirrored one 0% on diagonal axes), the atlas's own measured pa matches it on
    992 elongated atlas galaxies (99%), and Legacy viewer cutouts confirm it by
    eye. The anchor is the image: tests/test_sky_catalog.py holds galaxies whose
    expected major axis was read from the cutout, for the Tractor and atlas paths.

Verdicts, strongest evidence first:
  stellar       a source within COUNTERPART_RADIUS with significant Gaia
                parallax or proper motion
  point_source  the counterpart (within COUNTERPART_RADIUS) is point-like with no
                Gaia star signature: a faint star, or a nuclear event in a compact
                galaxy or QSO (where TDEs and AGN flares in faint hosts show up);
                weak, so never rejected on. It outranks host: a point source
                within 1" is closer, more specific evidence than a DLR
                association stretching over arcseconds (a faint variable star
                beyond Gaia's reach on a galaxy's outskirts must not pass as SN-like)
  host          a galaxy within HOST_DLR_MAX directional light radii
                (the DES SN host-matching measure, Gupta et al. 2016)

No compact-host rule (a point-like source 1-3" away taken as host): ~10% of DES hosts
in ecdfs/C are PSF-typed in DR10, but 12 of 15 sit within 1", where point_source keeps
them out of the orphans, and the rule gained 0 of 15 while giving 10.4% of random
positions a host at 3" (tools/host_match_check.py, 2026-10-01). Dropped, not tuned.
  none          nothing: orphan
  unavailable   the catalogue could not be read, or a catalogue gap (never a
                reason to reject)

AGN is a flag, not a verdict: the WISE colour test (W1-W2 >= 0.8 Vega, Stern et al.
2012, i.e. >= AGN_W1W2_AB_MIN in DR10's AB fluxes) runs on the source the verdict
assigned (point source or host), never on whatever happens to sit
nearby before a host is known. "agn_nuclear" when that source is within
COUNTERPART_RADIUS of the transient, "agn_host" otherwise. WISE is shallow: a faint
source gets no AGN information, not "not AGN". (A catalogue match such as Milliquas
would be a separate, earlier check; none is wired in.)
"""

import functools
import io
import math
import sys
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests

from .config import PROJECT_ROOT, WARN_PREFIX

TAP_URL = "https://datalab.noirlab.edu/tap/sync"
TABLE = "ls_dr10.tractor"
COLUMNS = (
    "ra", "dec", "type", "flux_g", "flux_r", "flux_z", "flux_w1", "flux_w2",
    "flux_ivar_w1", "flux_ivar_w2", "shape_r", "shape_e1", "shape_e2",
    "parallax", "parallax_ivar", "pmra", "pmra_ivar", "pmdec", "pmdec_ivar", "ref_cat", "ref_id", "maskbits",
)
ATLAS_TABLE = "sga2020.ellipse"
ATLAS_COLUMNS = {"atlas_pa": "pa", "atlas_ba": "ba", "atlas_sma": "sma_moment"}  # ours: theirs
ATLAS_REF_CAT = "L3"
GALAXY_MASKBIT = 12  # DR10 maskbits: inside a Siena Galaxy Atlas ellipse
CATALOG_VERSION = f"{TABLE.split('.')[0]}+{ATLAS_TABLE.split('.')[0]}"  # ls_dr10+sga2020
CACHE_DIR = PROJECT_ROOT / "cache" / CATALOG_VERSION / "tiles"
ATLAS_CACHE_DIR = PROJECT_ROOT / "cache" / CATALOG_VERSION / "atlas"
BRICKS_TABLE = "ls_dr10.bricks"
BRICK_COLUMNS = ("brickname", "ra1", "ra2", "dec1", "dec2")
BRICKS_CACHE_DIR = PROJECT_ROOT / "cache" / CATALOG_VERSION / "bricks"
MASKBITS_CACHE_DIR = PROJECT_ROOT / "cache" / CATALOG_VERSION / "maskbits"
MASKBITS_URL = ("https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr10/south/coadd/"
                "{group}/{brick}/legacysurvey-{brick}-maskbits.fits.fz")
MASKBITS_TIMEOUT = 120.0
BAILOUT_BIT = 10  # DR10 maskbits: the fitting gave up; no sources were written there
PIXSCALE_ARCSEC = 0.262
TILE_DEG = 0.1  # ~3000 rows at deep-field depth
TAP_TIMEOUT = 120.0
# explicit, so a service default cannot cut a result short unseen; a result this long
# is treated as truncated (CSV carries no TAP OVERFLOW status). Tiles hold ~2-4k rows.
TAP_MAXREC = 200_000
ATLAS_TAP_TIMEOUT = 900.0  # once per footprint and catalogue version: a full-depth cone scan (~1 min)
# Step 2's margin around a footprint, and how far it looks from a target: comfortably
# above threshold x the largest major-axis radius, whatever the radius choice. Checked
# 2026-10-01: within 5 deg of every footprint edge no atlas galaxy has D26/2 > 15'
# (largest 6.2', ESO 358-063), so HOST_DLR_MAX x a stays under 0.42 deg even on D26/2.
# Widen this if a galaxy with a > 15' ever sits near a footprint.
ATLAS_MARGIN_DEG = 1.0
NMGY_ZP = 22.5  # DR10 fluxes are nanomaggies: m_AB = 22.5 - 2.5 log10(flux)

SEARCH_RADIUS_ARCSEC = 30.0
# the gap check covers step 1's whole search area: an SN just outside a gap still loses
# a host whose centre lies inside it
GAP_RADIUS_ARCSEC = SEARCH_RADIUS_ARCSEC
COUNTERPART_RADIUS_ARCSEC = 1.0
NUCLEAR_RADIUS_ARCSEC = 1.0  # transient this close to its host's centre: flagged nuclear
# atlas hosts: DR10 can split the nucleus off as a point source offset from the model
# centre (NGC 873: 1.3"), so their nuclear radius is wider
ATLAS_NUCLEAR_RADIUS_ARCSEC = 2.0
# Chosen 2026-10-01 from tools/host_match_check.py: at 4, 99% of DES hosts get a host
# and 14.8% of random positions do; at 3, 95% and 8.5%. Hosted SNe outnumber hostless
# ones ~20:1 at Legacy depth, so false orphans (which fill the bucket looked at first)
# cost more than chance hosts (which only relabel the rare hostless case, still kept):
# per 100 transients, orphan-bucket purity ~80% at 4 against ~50% at 3. The knee plus
# margin rather than a precise optimum: 1% of 146 is one or two objects. DES's own cut
# does not carry over (our d_DLR runs ~1.4x theirs).
HOST_DLR_MAX = 4.0
MIN_HALF_LIGHT_ARCSEC = 0.2  # floor on shape_r so a tiny galaxy cannot claim nothing
PARALLAX_SNR_MIN = 5.0
PM_SNR_MIN = 5.0
WISE_SNR_MIN = 5.0
# W1-W2 >= 0.8 Vega; Vega->AB offsets 2.699 (W1) and 3.339 (W2) move it to 0.16 AB
AGN_W1W2_AB_MIN = 0.16
GALAXY_TYPES = {"REX", "EXP", "DEV", "SER"}
# the atlas galaxy's major-axis radius: its second moment (DES's DLR uses second
# moments) or the Tractor half-light radius. sma_moment runs ~2.6x shape_r (median
# over 1756 atlas galaxies). Chosen 2026-10-01: the two big-host rescues step 2 exists
# for (SN 2025zi / NGC 1398, SN 2023cr / ESO 419-003) come out at d_DLR ~0.7 with it,
# and barely qualify (2.1-2.4) with shape_r; cost ~1 point of chance hosts
ATLAS_RADIUS = "sma_moment"


class CatalogError(RuntimeError):
    pass


@dataclass
class Crossmatch:
    verdict: str  # stellar | point_source | host | none | unavailable
    evidence: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # agn_nuclear | agn_host


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


def _select(where: str) -> str:
    cols = [f"t.{c}" for c in COLUMNS] + [f"s.{theirs} AS {ours}" for ours, theirs in ATLAS_COLUMNS.items()]
    return (
        f"SELECT {', '.join(cols)} FROM {TABLE} AS t "
        f"LEFT JOIN {ATLAS_TABLE} AS s ON t.ref_id = s.sga_id AND t.ref_cat = '{ATLAS_REF_CAT}' WHERE {where}"
    )


def tile_query(i: int, j: int) -> str:
    ra0, dec0 = i * TILE_DEG, j * TILE_DEG - 90
    return _select(f"t.ra >= {ra0:.6f} AND t.ra < {ra0 + TILE_DEG:.6f} "
                   f"AND t.dec >= {dec0:.6f} AND t.dec < {dec0 + TILE_DEG:.6f} AND t.type != 'DUP'")


def footprint_atlas_query(cone: tuple[float, float, float]) -> str:
    ra, dec, radius = cone
    return _select(f"'t' = q3c_radial_query(t.ra, t.dec, {ra:.6f}, {dec:.6f}, {radius + ATLAS_MARGIN_DEG:.6f}) "
                   f"AND t.ref_cat = '{ATLAS_REF_CAT}' AND t.ref_id > 0")


def _fetch_cached(path, query: str, label: str, get=None, timeout: float = TAP_TIMEOUT,
                  columns: tuple[str, ...] | None = None) -> pd.DataFrame:
    """
    A query's rows, from the disk cache or from the TAP service. Only a complete,
    non-empty answer is cached: any failure raises CatalogError (never an empty
    frame), and an empty answer is returned but asked again next time, so a glitch
    cannot become a permanent hole that turns every SN in it into an orphan.
    """
    columns = columns or (*COLUMNS, *ATLAS_COLUMNS)
    df = _read_cached_csv(path, columns)
    if df is not None:
        return df
    get = get or requests.get
    try:
        r = get(TAP_URL, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "MAXREC": TAP_MAXREC,
                                 "QUERY": query}, timeout=timeout)
        text = r.text
    except requests.RequestException as e:
        raise CatalogError(f"{label}: {type(e).__name__}") from e
    # the service can answer 200 with a VOTable error document instead of CSV
    if r.status_code != 200 or not text.startswith(f"{columns[0]},"):
        raise CatalogError(f"{label}: HTTP {r.status_code} {text[:80]!r}")
    df = pd.read_csv(io.StringIO(text))
    if len(df) >= TAP_MAXREC:
        raise CatalogError(f"{label}: {len(df)} rows, at MAXREC: possibly truncated")
    if df.empty:
        return df
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, compression="gzip")
    tmp.rename(path)
    return df


# What a damaged cache file raises on read. Measured 2026-10-02 by truncating cached files:
# a gzip tile cut anywhere raises EOFError (gzip's trailer holds length and CRC, so it
# can never read back as a shorter tile); a maskbits file cut inside its MASKBITS image
# raises TypeError, OSError or StopIteration (344 cuts over 3 bricks, never a silently
# wrong image), and cut after it reads back identical (only the WISE masks are lost).
DAMAGED_FILE_ERRORS = (OSError, EOFError, ValueError, TypeError, UnicodeDecodeError, StopIteration, zlib.error)


def _discard_damaged(path, e: BaseException) -> None:
    print(f"{WARN_PREFIX}damaged cache file {path} ({type(e).__name__}: {e}); deleted, fetched again", file=sys.stderr)
    path.unlink()


def _read_cached_csv(path, columns) -> pd.DataFrame | None:
    """
    A cached file's rows, or None to fetch again: absent, cached before a column existed,
    or damaged (then deleted). Writes are atomic, so damage means a disk fault or a file
    from before that; either way it must be refetched, never crash the run.
    """
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except DAMAGED_FILE_ERRORS as e:
        _discard_damaged(path, e)
        return None
    return df if set(columns) <= set(df.columns) else None


def tile_is_cached(i: int, j: int) -> bool:
    """A current, readable cache file exists for the tile (the whole file is read: a damaged one keeps its header)."""
    return _read_cached_csv(CACHE_DIR / f"{i}_{j}.csv.gz", (*COLUMNS, *ATLAS_COLUMNS)) is not None


def fetch_tile(i: int, j: int, get=None) -> pd.DataFrame:
    """One DR10 tile (TILE_DEG), every source."""
    return _fetch_cached(CACHE_DIR / f"{i}_{j}.csv.gz", tile_query(i, j), f"tile {i},{j}", get)


def footprint_atlas(cone: tuple[float, float, float], get=None) -> pd.DataFrame:
    """Every atlas galaxy within the footprint cone plus ATLAS_MARGIN_DEG, with its ellipse; one query, cached."""
    ra, dec, radius = cone
    path = ATLAS_CACHE_DIR / f"cone_{ra:g}_{dec:g}_{radius:g}_margin_{ATLAS_MARGIN_DEG:g}.csv.gz"
    return _fetch_cached(path, footprint_atlas_query(cone), f"atlas for cone {ra:g} {dec:g} {radius:g}", get,
                         timeout=ATLAS_TAP_TIMEOUT)


def footprint_bricks_query(cone: tuple[float, float, float]) -> str:
    """
    The bricks whose centres lie in the footprint cone plus the margin: a cone like the
    atlas query, so no cos(dec) widening and no RA 0/360 wrap (it replaced an RA/Dec box,
    2026-10-02). A brick touching the footprint plus the gap check's 30" area has its
    centre within radius + 30" + its half-diagonal (0.18 deg), well inside the margin.
    """
    ra, dec, radius = cone
    return (f"SELECT {', '.join(BRICK_COLUMNS)} FROM {BRICKS_TABLE} AS t "
            f"WHERE 't' = q3c_radial_query(t.ra, t.dec, {ra:.6f}, {dec:.6f}, {radius + ATLAS_MARGIN_DEG:.6f})")


def footprint_bricks(cone: tuple[float, float, float], get=None) -> pd.DataFrame:
    """The DR10 bricks covering the footprint cone plus its margin; one query, cached."""
    ra, dec, radius = cone
    path = BRICKS_CACHE_DIR / f"centres_in_cone_{ra:g}_{dec:g}_{radius:g}_margin_{ATLAS_MARGIN_DEG:g}.csv.gz"
    return _fetch_cached(path, footprint_bricks_query(cone), f"bricks for cone {ra:g} {dec:g} {radius:g}", get,
                         columns=BRICK_COLUMNS)


def fetch_maskbits(brick: str, get=None) -> tuple[np.ndarray, "WCS"] | None:
    """
    A brick's maskbits image and WCS, from the cache or the Legacy Surveys portal. None
    means the portal has no such brick (404: no DR10 coverage), which is not cached;
    any other failure raises CatalogError. Only a complete FITS file is cached.
    """
    path = MASKBITS_CACHE_DIR / f"{brick}.fits.fz"
    if path.exists():
        try:
            return _read_maskbits(str(path))
        except DAMAGED_FILE_ERRORS as e:
            _discard_damaged(path, e)
    if not path.exists():
        get = get or requests.get
        try:
            r = get(MASKBITS_URL.format(group=brick[:3], brick=brick), timeout=MASKBITS_TIMEOUT)
            content = r.content
        except requests.RequestException as e:
            raise CatalogError(f"maskbits {brick}: {type(e).__name__}") from e
        if r.status_code == 404:
            return None
        if r.status_code != 200 or not content.startswith(b"SIMPLE"):
            raise CatalogError(f"maskbits {brick}: HTTP {r.status_code} {content[:60]!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        tmp.rename(path)
    return _read_maskbits(str(path))


def maskbits_is_cached(brick: str) -> bool:
    """
    A complete maskbits file is cached: as long as its own headers say (data start + span
    of the last image). Cheap (headers only, ~ms); a full read costs ~0.2 s per brick. A
    file cut short is deleted, so the prefetch fetches it again instead of the judging loop.
    """
    import warnings

    from astropy.io import fits
    from astropy.utils.exceptions import AstropyWarning

    path = MASKBITS_CACHE_DIR / f"{brick}.fits.fz"
    if not path.exists():
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AstropyWarning)  # a short file's header complaints: our own WARN says it
            with fits.open(path) as hdul:
                info = hdul.fileinfo(len(hdul) - 1)
                # a file cut inside the first image header shows only the empty primary HDU, whose own
                # span is trivially complete (27 such cuts in the 2026-10-02 sweep): require the image
                has_image = any(h.header.get("ZNAXIS", h.header.get("NAXIS", 0)) >= 2 for h in hdul)
                complete = has_image and info["datLoc"] + info["datSpan"] <= path.stat().st_size
    except DAMAGED_FILE_ERRORS as e:
        _discard_damaged(path, e)
        return False
    if not complete:
        _discard_damaged(path, EOFError(f"file ends before its last image ({path.stat().st_size} bytes)"))
    return complete


@functools.lru_cache(maxsize=8)  # ~50 MB each in memory
def _read_maskbits(path: str):
    from astropy.io import fits
    from astropy.wcs import WCS

    with fits.open(path) as hdul:
        hdu = next(h for h in hdul if h.data is not None)
        return np.asarray(hdu.data), WCS(hdu.header)


def catalogue_gap(ra: float, dec: float, bricks: pd.DataFrame, fetch_maskbits_=None,
                  radius_arcsec: float = GAP_RADIUS_ARCSEC) -> str | None:
    """
    Why DR10 cannot vouch for an empty sky around (ra, dec), or None: a BAILOUT pixel
    within radius_arcsec, or part of that circle in no DR10 brick. Every brick the
    circle overlaps is read (brick images overlap their neighbours by ~20", so the
    circle is covered). Errors propagate (CatalogError): a check that could not be
    made is never read as "no gap".
    """
    fetch_maskbits_ = fetch_maskbits_ or fetch_maskbits
    r_deg = radius_arcsec / 3600
    dra = r_deg / max(math.cos(math.radians(dec)), 1e-6)
    inside = (bricks["ra1"] <= ra) & (bricks["ra2"] > ra) & (bricks["dec1"] <= dec) & (bricks["dec2"] > dec)
    if not inside.any():
        return "no DR10 brick here"
    touched = bricks[(bricks["ra1"] < ra + dra) & (bricks["ra2"] > ra - dra)
                     & (bricks["dec1"] < dec + r_deg) & (bricks["dec2"] > dec - r_deg)]
    r_px = radius_arcsec / PIXSCALE_ARCSEC
    for brick in touched["brickname"]:
        got = fetch_maskbits_(brick)
        if got is None:
            return f'no DR10 brick for part of the {radius_arcsec:g}" search area ({brick})'
        image, wcs = got
        x, y = (float(v) for v in wcs.all_world2pix([[ra, dec]], 0)[0])
        x0, x1 = max(0, int(x - r_px)), min(image.shape[1], int(x + r_px) + 2)
        y0, y1 = max(0, int(y - r_px)), min(image.shape[0], int(y + r_px) + 2)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        circle = (xx - x) ** 2 + (yy - y) ** 2 <= r_px * r_px
        if ((image[y0:y1, x0:x1][circle].astype(np.int64) >> BAILOUT_BIT) & 1).any():
            return f'DR10 catalogue gap: the fitting gave up within {radius_arcsec:g}" (BAILOUT, brick {brick})'
    return None


def neighbours(ra: float, dec: float, radius_arcsec: float = SEARCH_RADIUS_ARCSEC, fetch=None) -> pd.DataFrame:
    """Catalogue rows within radius, with sep_arcsec and pa_deg (from the row to the target, E of N)."""
    fetch = fetch or fetch_tile
    tiles = [fetch(i, j) for i, j in sorted(tiles_around(ra, dec, radius_arcsec))]
    df = pd.concat([t for t in tiles if len(t)], ignore_index=True) if any(len(t) for t in tiles) else pd.DataFrame(columns=COLUMNS)
    if df.empty:
        df.attrs["tile_rows"] = 0
        return df.assign(sep_arcsec=[], pa_deg=[])
    out = with_separation(df, ra, dec)
    out = out[out["sep_arcsec"] <= radius_arcsec].sort_values("sep_arcsec").reset_index(drop=True)
    out.attrs["tile_rows"] = len(df)
    return out


def atlas_near(atlas: pd.DataFrame, ra: float, dec: float) -> pd.DataFrame:
    """The atlas galaxies within ATLAS_MARGIN_DEG of the target, with sep_arcsec and pa_deg (step 2's input)."""
    if atlas.empty:
        return atlas.assign(sep_arcsec=[], pa_deg=[])
    out = with_separation(atlas, ra, dec)
    return out[out["sep_arcsec"] <= ATLAS_MARGIN_DEG * 3600].sort_values("sep_arcsec").reset_index(drop=True)


def with_separation(df: pd.DataFrame, ra: float, dec: float) -> pd.DataFrame:
    """df plus sep_arcsec and pa_deg (from each row to the target, E of N)."""
    r1, d1 = np.radians(df["ra"].to_numpy(float)), np.radians(df["dec"].to_numpy(float))
    r2, d2 = math.radians(ra), math.radians(dec)
    cos_sep = np.sin(d1) * math.sin(d2) + np.cos(d1) * math.cos(d2) * np.cos(r2 - r1)
    sep = np.degrees(np.arccos(np.clip(cos_sep, -1, 1))) * 3600
    east = math.cos(d2) * np.sin(r2 - r1)
    north = np.cos(d1) * math.sin(d2) - np.sin(d1) * math.cos(d2) * np.cos(r2 - r1)
    pa = np.degrees(np.arctan2(east, north)) % 360
    return df.assign(sep_arcsec=sep, pa_deg=pa)


def tractor_ellipse(shape_r: float, e1: float, e2: float) -> tuple[float, float, float]:
    """(a arcsec, b/a, major-axis PA deg East of North) from DR10 shape_r, shape_e1, shape_e2."""
    e = min(math.hypot(e1, e2), 0.999)
    # Do not "fix" this to the DR10 docs' PA = 180 - phi: the documented formula is the
    # mirror image. Checked 2026-10-01 against the pixels (moments on FITS cutouts with
    # the WCS orientation, 92% within 15 deg vs 2% for the docs; cutouts by eye) and the
    # Tractor source. test_tractor_major_axis_matches_the_image is the proof and fails
    # on a mirror or a 90 deg swap.
    return float(shape_r), (1 - e) / (1 + e), math.degrees(0.5 * math.atan2(e2, e1)) % 180


def ellipse_radius(a: float, ba: float, major_pa_deg: float, pa_to_target_deg: float) -> float:
    """Radius of the ellipse (semi-axes a, ba*a, major axis at major_pa_deg) towards pa_to_target_deg."""
    a = max(float(a), MIN_HALF_LIGHT_ARCSEC)
    b = a * min(max(float(ba), 0.001), 1.0)
    d = math.radians(pa_to_target_deg - major_pa_deg)
    return a * b / math.sqrt((a * math.sin(d)) ** 2 + (b * math.cos(d)) ** 2)


def directional_light_radius(shape_r: float, e1: float, e2: float, pa_to_target_deg: float) -> float:
    """The galaxy's half-light radius in the direction of the target (arcsec), from the Tractor shape."""
    return ellipse_radius(*tractor_ellipse(shape_r, e1, e2), pa_to_target_deg)


def _is_atlas(row) -> bool:
    return row.get("ref_cat") == ATLAS_REF_CAT


def host_ellipse(row, atlas_radius: str = ATLAS_RADIUS) -> tuple[float, float, float]:
    """
    (a, b/a, major PA) to measure d_DLR with: the atlas's own measured ellipse for an
    atlas galaxy, else the Tractor shape. The atlas fills pa/ba from HyperLeda where its
    ellipse moments could not be measured (sma_moment = -1; 34 of 1790 in the
    footprints); a copied value is not a measurement, so those use the Tractor shape.
    """
    sma = row.get("atlas_sma")
    if _is_atlas(row) and pd.notna(sma) and sma > 0:
        a = sma if atlas_radius == "sma_moment" else row["shape_r"]
        # atlas pa is used as is, East of North, although the atlas docs say "clockwise
        # from North": checked against the pixels like the Tractor angle above;
        # test_atlas_major_axis_matches_the_image is the proof
        return float(a), float(row["atlas_ba"]), float(row["atlas_pa"])
    return tractor_ellipse(row["shape_r"], row["shape_e1"], row["shape_e2"])


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


def wise_colour(row) -> tuple[str, str]:
    """
    (outcome, text): "agn" or "not_agn" when both W1 and W2 reach WISE_SNR_MIN, else
    "no_measurement". Most faint DR10 sources have noisy WISE fluxes; a colour cut
    on noise flags at random, so below the gate there is no verdict either way.
    The cut is Stern et al.'s W1-W2 >= 0.8 Vega, i.e. AGN_W1W2_AB_MIN = 0.16 on the AB
    fluxes DR10 gives (0.8 applied to AB colours would quietly miss most AGN).
    """
    w1, w2 = row["flux_w1"], row["flux_w2"]
    if (_snr(w1, row["flux_ivar_w1"]) < WISE_SNR_MIN or _snr(w2, row["flux_ivar_w2"]) < WISE_SNR_MIN
            or w1 <= 0 or w2 <= 0):
        return "no_measurement", f"WISE colour: no measurement (S/N < {WISE_SNR_MIN:g} in W1 or W2)"
    colour = -2.5 * math.log10(w1 / w2)
    if colour >= AGN_W1W2_AB_MIN:
        return "agn", f"W1-W2={colour:.2f} AB"
    return "not_agn", f"WISE W1-W2={colour:.2f} AB, not AGN-like"


def _label(row) -> str:
    mag = nmgy_to_mag(row["flux_r"])
    label = f'{row["type"]} r={mag:.1f}' if mag is not None else f'{row["type"]} r=?'
    return f'{label} SGA {int(row["ref_id"])}' if _is_atlas(row) and row.get("ref_id", 0) > 0 else label


def atlas_central(rows: pd.DataFrame) -> pd.Series:
    if "ref_cat" not in rows:
        return pd.Series(False, index=rows.index)
    return (rows["ref_cat"] == ATLAS_REF_CAT) & (rows["ref_id"] > 0)


def pieces(rows: pd.DataFrame) -> pd.Series:
    """Rows inside an atlas galaxy that are not the galaxy itself: L3 with REF_ID -1, or maskbit 12."""
    if "ref_cat" not in rows:
        return pd.Series(False, index=rows.index)
    frozen = (rows["ref_cat"] == ATLAS_REF_CAT) & (rows["ref_id"] <= 0)
    if "maskbits" in rows:
        inside = (pd.to_numeric(rows["maskbits"], errors="coerce").fillna(0).astype(np.int64) & (1 << GALAXY_MASKBIT)) > 0
    else:
        inside = pd.Series(False, index=rows.index)
    return (frozen | inside) & ~atlas_central(rows)


def host_candidates(rows: pd.DataFrame) -> pd.DataFrame:
    """Galaxies, less the pieces of atlas galaxies."""
    galaxies = rows[rows["type"].isin(GALAXY_TYPES)]
    return galaxies[~pieces(galaxies)]


def stellar_counterpart(rows: pd.DataFrame) -> tuple[pd.Series, str] | None:
    """A source within COUNTERPART_RADIUS with a Gaia star signature (pieces included: a star is a star)."""
    for _, row in rows[rows["sep_arcsec"] <= COUNTERPART_RADIUS_ARCSEC].iterrows():
        star = _gaia_star(row)
        if star:
            return row, star
    return None


def point_counterpart(rows: pd.DataFrame) -> pd.Series | None:
    """The nearest source within COUNTERPART_RADIUS that is not a piece of an atlas galaxy, if it is point-like."""
    near = rows[(rows["sep_arcsec"] <= COUNTERPART_RADIUS_ARCSEC) & ~pieces(rows)]
    if near.empty:
        return None
    first = near.sort_values("sep_arcsec").iloc[0]
    return first if first["type"] == "PSF" else None


SNR_BANDS = ("g", "r", "i", "z")
SNR_MATCH_ARCSEC = 0.1  # the counterpart's own catalogue row


def combined_snr(row) -> float:
    """S/N over g, r, i, z combined: sqrt(sum of flux^2 * ivar), negative fluxes counted as 0. Not magnitude:
    DR10 measures r for sources detected in other bands, and depth varies across the sky."""
    return math.sqrt(sum(max(float(row[f"flux_{b}"] or 0), 0.0) ** 2 * float(row[f"flux_ivar_{b}"] or 0)
                         for b in SNR_BANDS))


def counterpart_snr(ra: float, dec: float, fetch=None, get=None) -> float | None:
    """
    Combined S/N of the point source the point-source rule takes at (ra, dec); None when
    there is none. The tiles carry no optical flux_ivar, so its one row is fetched (the
    point-source watch asks only for TNS-classified supernovae: rare). Raises CatalogError.
    """
    psf = point_counterpart(neighbours(ra, dec, SEARCH_RADIUS_ARCSEC, fetch))
    if psf is None:
        return None
    cols = [f"flux_{b}" for b in SNR_BANDS] + [f"flux_ivar_{b}" for b in SNR_BANDS]
    query = (f"SELECT {', '.join(cols)} FROM {TABLE} WHERE 't' = q3c_radial_query(ra, dec, "
             f"{psf['ra']:.6f}, {psf['dec']:.6f}, {SNR_MATCH_ARCSEC / 3600:.8f})")
    label = f"S/N of the point source at {psf['ra']:.6f},{psf['dec']:.6f}"
    try:
        r = (get or requests.get)(TAP_URL, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                                   "MAXREC": 10, "QUERY": query}, timeout=TAP_TIMEOUT)
    except requests.RequestException as e:
        raise CatalogError(f"{label}: {type(e).__name__}") from e
    if r.status_code != 200 or not r.text.startswith(f"{cols[0]},"):
        raise CatalogError(f"{label}: HTTP {r.status_code} {r.text[:80]!r}")
    rows = pd.read_csv(io.StringIO(r.text))
    if len(rows) != 1:
        raise CatalogError(f"{label}: {len(rows)} catalogue rows within {SNR_MATCH_ARCSEC}\"")
    return combined_snr(rows.iloc[0])


def reached_atlas(atlas_rows: pd.DataFrame, dlr_max: float = HOST_DLR_MAX,
                  atlas_radius: str = ATLAS_RADIUS) -> pd.DataFrame:
    """Step 2: atlas galaxies whose ellipse, scaled by dlr_max, reaches the target."""
    if atlas_rows is None or atlas_rows.empty:
        return pd.DataFrame(columns=[*COLUMNS, *ATLAS_COLUMNS, "sep_arcsec", "pa_deg"])
    a = atlas_rows.apply(lambda r: host_ellipse(r, atlas_radius)[0], axis=1)
    return atlas_rows[atlas_rows["sep_arcsec"] < dlr_max * a]


def best_host(rows: pd.DataFrame, dlr_max: float = HOST_DLR_MAX, atlas_radius: str = ATLAS_RADIUS,
              atlas_rows: pd.DataFrame | None = None) -> tuple[float, pd.Series] | None:
    """
    (d_DLR, row) of the candidate nearest in light radii, if within dlr_max. Candidates:
    host_candidates(rows) (step 1, rows as from neighbours()) plus the atlas galaxies of
    atlas_rows (as from atlas_near()) that reach the target (step 2); an atlas galaxy
    found by both steps counts once.
    """
    cands = host_candidates(rows)
    reached = reached_atlas(atlas_rows, dlr_max, atlas_radius)
    if len(reached):
        seen = set(cands.loc[cands["ref_cat"] == ATLAS_REF_CAT, "ref_id"]) if "ref_cat" in cands else set()
        reached = reached[~reached["ref_id"].isin(seen)]
        cands = pd.concat([cands, reached], ignore_index=True) if len(cands) else reached
    best = None
    for _, row in cands.iterrows():
        d = row["sep_arcsec"] / ellipse_radius(*host_ellipse(row, atlas_radius), row["pa_deg"])
        if d <= dlr_max and (best is None or d < best[0]):
            best = (d, row)
    return best


def classify(ra: float, dec: float, fetch=None, atlas: pd.DataFrame | None = None, coverage=None) -> Crossmatch:
    """
    The DR10 verdict for a position. atlas is footprint_atlas() for the footprint the
    position lies in; None runs step 1 only (the caller says so once per run).
    coverage(ra, dec) -> reason | None is the catalogue-gap check (catalogue_gap bound
    to the footprint's bricks); a gap makes the verdict "unavailable", never "none".
    """
    try:
        gap = coverage(ra, dec) if coverage is not None else None
        if gap:
            return Crossmatch("unavailable", [gap])
        rows = neighbours(ra, dec, SEARCH_RADIUS_ARCSEC, fetch)
    except CatalogError as e:
        return Crossmatch("unavailable", [str(e)])
    if rows.attrs.get("tile_rows", 0) == 0:
        return Crossmatch("unavailable", ["no DR10 sources in the surrounding tiles (outside coverage?)"])
    return _verdict(rows, atlas_near(atlas, ra, dec) if atlas is not None else None)


def _verdict(rows: pd.DataFrame, atlas_rows: pd.DataFrame | None) -> Crossmatch:
    star = stellar_counterpart(rows)
    if star is not None:
        return Crossmatch("stellar", [star[1]])
    counterpart = point_counterpart(rows)
    if counterpart is not None:
        return _agn_flag(Crossmatch(
            "point_source", [f'{_label(counterpart)} at {counterpart["sep_arcsec"]:.1f}", no Gaia astrometry']), counterpart)

    best = best_host(rows, atlas_rows=atlas_rows)
    if best is not None:
        d, row = best
        xm = Crossmatch("host", [f'{_label(row)} sep={row["sep_arcsec"]:.1f}" d_DLR={d:.1f}'])
        radius = ATLAS_NUCLEAR_RADIUS_ARCSEC if _is_atlas(row) else NUCLEAR_RADIUS_ARCSEC
        if row["sep_arcsec"] <= radius:
            xm.flags.append("nuclear")
            xm.evidence.append(f'nuclear: within {radius:g}" of the host centre')
        return _agn_flag(xm, row)

    return Crossmatch("none")


def _agn_flag(xm: Crossmatch, source) -> Crossmatch:
    """The WISE colour of the source the verdict assigned: a flag if AGN-like (nuclear within COUNTERPART_RADIUS), always stated."""
    outcome, text = wise_colour(source)
    if outcome == "agn":
        nuclear = source["sep_arcsec"] <= COUNTERPART_RADIUS_ARCSEC
        xm.flags.append("agn_nuclear" if nuclear else "agn_host")
        xm.evidence.append(f"AGN colours ({text}){' at the transient' if nuclear else ' of the host'}")
    else:
        xm.evidence.append(text)
    return xm
