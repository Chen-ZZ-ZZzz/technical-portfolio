"""
Validate sky_catalog's host matching (directional light radius on Legacy Surveys DR10).

Two directions, then a threshold chosen from both (user's spec, rubin_note 2026-10-01):
  1. known hosts: spectroscopically classified TNS supernovae in the footprints that
     have a reported host galaxy. Run the matcher; count correct hosts, wrong hosts and
     false orphans.
  2. random positions in the footprints: the fraction that get a "host" below the
     threshold is the chance-coincidence rate (does the matcher invent hosts?).

Where each piece comes from, and why:
  - SN sample: ANTARES loci in the footprint cones that carry a TNS crossmatch
    (catalog tns_public_objects) give name, type, position and redshift. Only ZTF-era
    objects reach ANTARES, so this is the bright, nearby end, where big hosts are.
  - Reported host: ALeRCE's TNS service (tns.alerce.online/search, by position)
    returns the full TNS record including `hostname` and `host_redshift`; the TNS site
    itself refuses scripted queries and ANTARES's TNS rows carry no host.
  - Host position: from the J-coordinates embedded in most survey names (SDSS J,
    2MASX J, WISEA J, ...), else CDS Sesame. The true host is the DR10 galaxy nearest
    that position.
  - Second reference (user's suggestion): DES-SN5YR (Sanchez+ 2024) publishes, for every
    transient in its ten SN fields, the host DES chose with this same DLR method
    (HOSTGAL_RA/DEC, HOSTGAL_DDLR; Wiseman+ 2020). C1-C3 lie in ecdfs, part of X2 in C.
    Its spectroscopic SNe join the known-host set; it has nothing in D.
  - Truth -> DR10 row: the row nearest the host position within HOST_MATCH_ARCSEC. A
    position that lands on a piece of an atlas galaxy counts as that atlas galaxy. A
    DES host with no Legacy source within 1" is "below Legacy depth" (DES stacks go
    deeper): counted apart, never as a matcher miss, so depth cannot drag the threshold.
  - DES measures agreement with DES, not truth: same DLR method, radii measured on its
    own stacks. Its curve shows where agreement levels off; its own cut does not carry.

Everything is scored through the production code (sky_catalog.neighbours, atlas_near,
host_candidates, host_ellipse, ellipse_radius), for each variant in VARIANTS and each
threshold in THRESHOLDS; at the production settings each position is also run through
sky_catalog.best_host itself, and any disagreement is reported.

Usage:
    python tools/host_match_check.py sample     # TNS SNe with reported hosts (network, cached)
    python tools/host_match_check.py evaluate   # score TNS + DES known hosts (DR10 tiles, cached)
    python tools/host_match_check.py randoms    # score random positions (DR10 tiles, cached)
    python tools/host_match_check.py report     # the tables, no network
"""

from __future__ import annotations

import argparse
import datetime
import functools
import hashlib
import json
import math
import pathlib
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from rubin_qa import sky_catalog as sc  # noqa: E402
from rubin_qa.transient_monitor import FOOTPRINTS  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "logs" / "host_check"
ALERCE_TNS_URL = "https://tns.alerce.online/search"
SESAME_URL = "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/SNV"
SN_TYPE_PREFIXES = ("SN", "SLSN")
REQUEST_PAUSE = 1.0  # seconds between calls to the public services
# A TNS object ALeRCE's TNS service must know, asked to tell "not in TNS" from "throttled".
# The service runs on a quota of 10 answers per 60 s window and answers 200 with an empty
# record once it is spent, until the window turns (measured 2026-10-01: exactly 10 full
# answers per window, in fixed windows that turned at :23 past the minute that day).
# Within a window, answers only go full -> empty, so an empty answer followed by a full
# canary in the *same* window is genuine. Where the windows turn is never used: a server
# restart can move it. Ask twice instead: two (empty answer, full canary) pairs within
# one window length cannot both straddle a turn, so at least one shares a window and
# vouches for "not in TNS". A canary from before the answer proves nothing (the quota can
# run out in between). The rule assumes fixed windows at least TNS_WINDOW_SECONDS long;
# tests sweep every phase against a simulated service, and a shorter window fools it. (User's design, 2026-10-01, the canary at the scale of the cycle; it replaced
# two 10 s retries per object, then a canary bracketing the whole batch, which a
# throttled minute in the middle slips past.)
TNS_CANARY = {"name": "2025zi", "ra": 54.727480, "dec": -26.370617}
TNS_WINDOW_SECONDS = 60
TNS_PAUSE = 6.5  # before every call, canaries included: under 10 a window, so we do not spend the quota ourselves
TNS_THROTTLE_WAIT = 15.0  # a throttled canary is asked again this often, until it answers
TNS_MAX_WAIT = 180.0  # throttled for longer than this: "unavailable"
PENDING_FILE = "pending.jsonl"
REQUEST_TIMEOUT = 60
WARN_PREFIX = "WARN: "
DES_HEAD = pathlib.Path(__file__).resolve().parents[1] / "cache" / "des_sn5yr" / "DES-SN5YR_DES_HEAD.FITS.gz"
# DES-SN5YR SNTYPE codes of spectroscopically classified SNe (from its README)
DES_SN_TYPES = {1: "SNIa", 4: "SNIa-pec", 5: "SNI", 23: "SNIIb", 29: "SNII", 32: "SNIb", 33: "SNIc", 39: "SNIbc",
                41: "SLSN-I", 66: "SLSN-II", 122: "SNIIn?", 129: "SNII?", 139: "SNIbc?", 141: "SLSN-I?"}
# DES: the user's depth test (no Legacy source within ~1"); TNS: host positions from
# survey names or Sesame are coarser
HOST_MATCH_ARCSEC = {"DES": 1.0, "TNS": 2.0}
TRUTH_PAD_ARCSEC = 10.0  # DR10 rows are read out to the reported host position plus this
# known-host records judged unreliable, kept out of the calibration and counted with the reason
SUSPECT_RECORDS = {
    "2021adwh": "TNS host redshift 0.0066 vs SN redshift 0.15; reported host 14.7\" away, point-like in DR10",
}
# Catalogue gaps come from sky_catalog.catalogue_gap: any BAILOUT pixel in step 1's 30"
# search area around the SN, or part of it in no brick. That covers every host step 1
# could find; hosts farther out are atlas galaxies, whose DR10 rows survive gaps (atlas
# table = L3 join in all three footprints, tools/atlas_query_contract.py). Positions in
# a gap are neither orphans nor hosts below depth and stay out of every score. A position
# with no source at all within EMPTY_RADIUS_ARCSEC but no BAILOUT is counted too, as a
# check that BAILOUT explains the empty sky (at the sparsest normal density 15" holds ~6).
EMPTY_RADIUS_ARCSEC = 15.0
VARIANTS = {  # name: (step 2 on?, atlas radius)
    'step 1 only, 30" (before the fix)': (False, "sma_moment"),
    "two-step, atlas radius sma_moment": (True, "sma_moment"),
    "two-step, atlas radius shape_r": (True, "shape_r"),
}
PRODUCTION_VARIANT = "two-step, atlas radius sma_moment"  # = sky_catalog defaults
THRESHOLDS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)
# A compact-host rule (a star-free PSF 1"-r_c away as host) was measured and dropped
# 2026-10-01: r_c 3" gave random positions a host 10.4% of the time and gained 0 of 15
# PSF-typed hosts (12 sit within 1", where point_source keeps them out of the orphans).
# The measurement stays here so the decision can be re-checked; production has no such rule.
COMPACT_RADII = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
RANDOM_TILES = 40      # per footprint, drawn uniformly inside the cone
RANDOM_PER_TILE = 25   # positions per tile: 1000 per footprint, tiles shared
RANDOM_SEED = 20261001
TILE_WORKERS = 4
# What decides the scores, fingerprinted into every stage's sidecar and the report header,
# so a report names its version without version control and refuses to pass as clean
# when its stages came from different code (2026-10-01: they once did, mid-run edits).
ROOT = pathlib.Path(__file__).resolve().parents[1]
FINGERPRINTED = {
    "sky_catalog.py": ROOT / "src" / "rubin_qa" / "sky_catalog.py",
    "host_match_check.py": pathlib.Path(__file__).resolve(),
    "transient_monitor.py (footprints)": ROOT / "src" / "rubin_qa" / "transient_monitor.py",
    "sne.jsonl (TNS sample)": OUT_DIR / "sne.jsonl",
    "DES-SN5YR_DES_HEAD.FITS.gz": DES_HEAD,
}
TILE_RETRY_WAITS = (30, 60)  # Data Lab answers slowly at times; a run should not die on one tile
# sexagesimal (SDSS J095937.98+021934.5; 2MASX J10001234-0123456, decimals without a point)
J_HMS = re.compile(r"J(\d{6})\.?(\d*)([+-])(\d{6})\.?(\d*)")
# decimal degrees (PSO J149.9082+02.3263)
J_DEG = re.compile(r"J(\d{1,3}\.\d+)([+-]\d{1,2}\.\d+)")


def antares_tns_sne(footprint: str) -> list[dict]:
    """Classified TNS supernovae among ANTARES loci in the footprint cone."""
    from antares_client.search import search

    ra, dec, radius = FOOTPRINTS[footprint][0]
    query = {"query": {"bool": {"filter": [
        {"sky_distance": {"distance": f"{radius} degree", "htm16": {"center": f"{ra} {dec}"}}},
        {"term": {"catalogs": "tns_public_objects"}}]}}}
    out = {}
    for locus in search(query):
        for r in (locus.catalog_objects or {}).get("tns_public_objects", []):
            if str(r.get("type") or "").startswith(SN_TYPE_PREFIXES):
                out[r["name"]] = {"footprint": footprint, "name": r["name"], "type": r["type"],
                                  "z": r.get("redshift"), "ra": r["ra"], "dec": r["declination"],
                                  "locus": locus.locus_id}
    return list(out.values())


def tns_once(ra: float, dec: float) -> dict:
    """The TNS record at a position, via ALeRCE; {} when it answers without one. One call, no retry."""
    r = requests.post(ALERCE_TNS_URL, json={"ra": ra, "dec": dec}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("object_data") or {}


def tns_canary_ok() -> bool:
    time.sleep(TNS_PAUSE)
    try:
        return tns_once(TNS_CANARY["ra"], TNS_CANARY["dec"]).get("objname") == TNS_CANARY["name"]
    except requests.RequestException:
        return False


def _now() -> float:
    return time.time()


def tns_lookup(ra: float, dec: float) -> dict | None:
    """
    The TNS record at a position; {} only when vouched for as "not in TNS" (see
    TNS_CANARY); None when unavailable: the request failed, or the service stayed
    throttled past TNS_MAX_WAIT.
    """
    pairs: list[float] = []  # when each (empty answer, full canary) pair started
    waited = 0.0
    while True:
        time.sleep(TNS_PAUSE)
        asked = _now()
        try:
            rec = tns_once(ra, dec)
        except requests.RequestException:
            return None
        if rec:
            return rec
        if tns_canary_ok():
            pairs.append(asked)
            if len(pairs) >= 2 and _now() - pairs[-2] < TNS_WINDOW_SECONDS:
                return {}
            continue
        # throttled: wait until the canary answers again, then ask again (earlier pairs
        # still count: the window argument holds for any two)
        while True:
            if waited >= TNS_MAX_WAIT:
                return None
            time.sleep(TNS_THROTTLE_WAIT)
            waited += TNS_THROTTLE_WAIT
            if tns_canary_ok():
                break


def tns_batch(positions: list[tuple[float, float]]) -> list[dict | None]:
    """tns_lookup for each position (paced inside)."""
    return [tns_lookup(ra, dec) for ra, dec in positions]


def j_coords(name: str) -> tuple[float, float] | None:
    name = name.replace(" ", "")
    m = J_DEG.search(name)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = J_HMS.search(name)
    if not m:
        return None
    ra_s, ra_dec, sign, de_s, de_dec = m.groups()
    sec = lambda whole, frac: float(f"{whole[4:6]}.{frac or 0}")
    ra = 15 * (int(ra_s[:2]) + int(ra_s[2:4]) / 60 + sec(ra_s, ra_dec) / 3600)
    dec = (int(de_s[:2]) + int(de_s[2:4]) / 60 + sec(de_s, de_dec) / 3600) * (-1 if sign == "-" else 1)
    return ra, dec


def sesame(name: str) -> tuple[float, float] | None:
    r = requests.get(f"{SESAME_URL}?{requests.utils.quote(name)}", timeout=REQUEST_TIMEOUT)
    ra, dec = re.search(r"<jradeg>([-\d.]+)</jradeg>", r.text), re.search(r"<jdedeg>([-\d.]+)</jdedeg>", r.text)
    return (float(ra.group(1)), float(dec.group(1))) if ra and dec else None


def resolve_host(hostname: str) -> tuple[float, float, str] | None:
    """
    (ra, dec, how) for the first alias that resolves; embedded J-coordinates before
    Sesame. None only when Sesame answered and knows none of them; if a lookup failed
    and nothing resolved, the RequestException propagates, so the SN is retried next
    run instead of being saved without a host.
    """
    # TNS sometimes stores names URL-encoded: 2026fgl's host came as 2MASXJ09565234%2B0328119
    aliases = [unquote(a).strip() for a in hostname.split(",") if unquote(a).strip()]
    for alias in aliases:
        c = j_coords(alias)
        if c:
            return c[0], c[1], f"name:{alias}"
    failure = None
    for alias in aliases:
        time.sleep(REQUEST_PAUSE)
        try:
            c = sesame(alias)
        except requests.RequestException as e:
            failure = e
            continue
        if c:
            return c[0], c[1], f"sesame:{alias}"
    if failure is not None:
        raise failure
    return None


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a = math.radians
    c = math.sin(a(dec1)) * math.sin(a(dec2)) + math.cos(a(dec1)) * math.cos(a(dec2)) * math.cos(a(ra2 - ra1))
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600


def _sample_one(sn: dict, rec: dict | None, path: pathlib.Path) -> str | None:
    """Save one SN from its TNS answer; the reason it stays pending if it cannot be (nothing saved)."""
    if rec is None:
        return f"TNS unavailable (request failed, or still throttled after {TNS_MAX_WAIT:.0f} s)"
    if not rec:
        # these SNe come from a TNS crossmatch, so an empty answer is never "no host"
        return "not found at ALeRCE's TNS service (vouched for by the canary in the same window)"
    if rec.get("objname") != sn["name"]:
        sn["tns_mismatch"] = rec.get("objname")
    sn["hostname"] = (rec.get("hostname") or "").strip()
    sn["host_z"] = rec.get("host_redshift")
    try:
        host = resolve_host(sn["hostname"]) if sn["hostname"] else None
    except requests.RequestException as e:
        return f"host lookup failed: {type(e).__name__} {e}"
    if host:
        sn["host_ra"], sn["host_dec"], sn["host_how"] = host
        sn["host_sep"] = round(sep_arcsec(sn["ra"], sn["dec"], host[0], host[1]), 2)
    with open(path, "a") as f:
        f.write(json.dumps(sn) + "\n")
    print(f"  {sn['name']:10s} {sn['type']:12s} z={sn['z']}  host={sn['hostname'] or '-':40.40s} "
          f"{sn.get('host_how', '')} sep={sn.get('host_sep', '')}", flush=True)
    return None


def run_sample(footprints: list[str]) -> int:
    """
    SNe whose TNS answer cannot be used go on PENDING (with the reason) and are asked
    first by the next run, independently of the ANTARES listing. The TNS answers come
    in one canary-bracketed batch. Exit 1 while any are pending.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path, pending_path = OUT_DIR / "sne.jsonl", OUT_DIR / PENDING_FILE
    have = {json.loads(l)["name"] for l in path.read_text().splitlines()} if path.exists() else set()
    pending = {json.loads(l)["name"]: json.loads(l) for l in pending_path.read_text().splitlines()} \
        if pending_path.exists() else {}
    todo: dict[str, dict] = {}
    for sn in pending.values():  # pending first
        if sn["name"] not in have:
            todo[sn["name"]] = {k: v for k, v in sn.items() if k not in ("pending_reason", "pending_runs")}
    if pending:
        print(f"{len(pending)} pending from earlier runs, asked first", flush=True)
    for fp in footprints:
        sne = antares_tns_sne(fp)
        print(f"{fp}: {len(sne)} classified SNe with an ANTARES locus", flush=True)
        for sn in sne:
            if sn["name"] not in have and sn["name"] not in todo:
                todo[sn["name"]] = sn

    sns = list(todo.values())
    recs = tns_batch([(sn["ra"], sn["dec"]) for sn in sns])
    still: dict[str, dict] = {}
    for sn, rec in zip(sns, recs):
        reason = _sample_one(sn, rec, path)
        if reason is None:
            have.add(sn["name"])
        else:
            print(f"{WARN_PREFIX}{sn['name']}: {reason}; not saved, pending for the next run", file=sys.stderr, flush=True)
            still[sn["name"]] = {**sn, "pending_reason": reason,
                                 "pending_runs": pending.get(sn["name"], {}).get("pending_runs", 0) + 1}
    tmp = pending_path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(v) + "\n" for v in still.values()))
    tmp.rename(pending_path)
    print(f"sample: {len(have)} saved, {len(still)} pending (retried first next run)"
          + (": " + ", ".join(sorted(still)) if still else ""), flush=True)
    rows = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
    unresolved = [f"{r['name']} ({r['hostname']})" for r in rows if r.get("hostname") and "host_ra" not in r]
    print(f"host name given but not resolved (not in the known-host set): {len(unresolved)}"
          + (": " + "; ".join(unresolved) if unresolved else ""), flush=True)
    return 1 if still else 0


# --- scoring ----------------------------------------------------------------------------

def fetch_tile_retrying(i: int, j: int) -> pd.DataFrame:
    for wait in (*TILE_RETRY_WAITS, None):
        try:
            return sc.fetch_tile(i, j)
        except sc.CatalogError as e:
            if wait is None:
                raise
            print(f"{WARN_PREFIX}{e}; retrying in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)


@functools.lru_cache(maxsize=4096)
def _tile(i: int, j: int) -> pd.DataFrame:
    return fetch_tile_retrying(i, j)


def _retrying(fn, *args):
    for wait in (*TILE_RETRY_WAITS, None):
        try:
            return fn(*args)
        except sc.CatalogError as e:
            if wait is None:
                raise
            print(f"{WARN_PREFIX}{e}; retrying in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)


@functools.lru_cache(maxsize=None)
def _bricks(footprint: str) -> pd.DataFrame:
    return _retrying(sc.footprint_bricks, FOOTPRINTS[footprint][0])


def _maskbits(brick: str):
    return _retrying(sc.fetch_maskbits, brick)


def brick_names(footprint: str, positions: list[tuple[float, float]]) -> set[str]:
    """Every brick a position's gap-check circle overlaps (catalogue_gap reads them all)."""
    b = _bricks(footprint)
    r = sc.GAP_RADIUS_ARCSEC / 3600
    out = set()
    for ra, dec in positions:
        dra = r / math.cos(math.radians(dec))
        hit = b[(b["ra1"] < ra + dra) & (b["ra2"] > ra - dra) & (b["dec1"] < dec + r) & (b["dec2"] > dec - r)]
        out |= set(hit["brickname"])
    return out


def prefetch_maskbits(bricks: set[str]) -> None:
    todo = sorted(b for b in bricks if not (sc.MASKBITS_CACHE_DIR / f"{b}.fits.fz").exists())
    print(f"  {len(bricks)} maskbits bricks needed, {len(todo)} to fetch", flush=True)

    def one(b):
        try:
            _maskbits(b)
            return None
        except sc.CatalogError as e:
            return str(e)

    with ThreadPoolExecutor(TILE_WORKERS) as ex:
        failed = [e for e in ex.map(one, todo) if e]
    if failed:
        print(f"{WARN_PREFIX}{len(failed)} maskbits bricks could not be read; their positions score as fetch errors",
              file=sys.stderr, flush=True)


@functools.lru_cache(maxsize=None)
def _atlas(footprint: str) -> pd.DataFrame:
    for wait in (*TILE_RETRY_WAITS, None):
        try:
            return sc.footprint_atlas(FOOTPRINTS[footprint][0])
        except sc.CatalogError as e:
            if wait is None:
                raise
            print(f"{WARN_PREFIX}{e}; retrying in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)


def prefetch(needs: list[tuple[float, float, float]]) -> None:
    """Fetch every DR10 tile the (ra, dec, radius) needs cover, a few at a time (each is cached on disk)."""
    tiles = sorted({t for ra, dec, r in needs for t in sc.tiles_around(ra, dec, r)})
    todo = [t for t in tiles if not sc.tile_is_cached(*t)]
    print(f"  {len(tiles)} DR10 tiles needed, {len(todo)} to fetch", flush=True)
    def one(t):
        try:
            fetch_tile_retrying(*t)
            return None
        except sc.CatalogError as e:
            return str(e)

    failed = []
    with ThreadPoolExecutor(TILE_WORKERS) as ex:
        for k, err in enumerate(ex.map(one, todo), 1):
            if err:
                failed.append(err)
            if k % 100 == 0:
                print(f"  fetched {k}/{len(todo)}", flush=True)
    if failed:
        print(f"{WARN_PREFIX}{len(failed)} tiles could not be read; positions needing them are scored as errors",
              file=sys.stderr, flush=True)


def key(row) -> str:
    """One galaxy, whichever step found it: atlas galaxies by SGA ID, the rest by position."""
    if row.get("ref_cat") == sc.ATLAS_REF_CAT and row.get("ref_id", 0) > 0:
        return f"sga{int(row['ref_id'])}"
    return f"{row['ra']:.6f},{row['dec']:.6f}"


def candidates(rows30: pd.DataFrame, atlas_rows: pd.DataFrame, two_step: bool, atlas_radius: str) -> pd.DataFrame:
    """
    Every candidate with its d_DLR. Step 2's reach test (sep < t x a) is left out on
    purpose: d <= t implies it, so the nearest candidate with d <= t is production's
    pick at every threshold t at once (checked against best_host in score_position).
    """
    c = sc.host_candidates(rows30)
    if two_step and len(atlas_rows):
        seen = set(c.loc[c["ref_cat"] == sc.ATLAS_REF_CAT, "ref_id"])
        extra = atlas_rows[~atlas_rows["ref_id"].isin(seen)]
        c = pd.concat([c, extra], ignore_index=True) if len(c) else extra
    if c.empty:
        return c.assign(d=[], key=[])
    d = [r["sep_arcsec"] / sc.ellipse_radius(*sc.host_ellipse(r, atlas_radius), r["pa_deg"]) for _, r in c.iterrows()]
    return c.assign(d=d, key=[key(r) for _, r in c.iterrows()])


def true_host(rows: pd.DataFrame, host_ra: float, host_dec: float, tol: float) -> tuple[str | None, str]:
    """(key of the reported host in DR10, status)."""
    if rows.empty:
        return None, "no_dr10"
    near = sc.with_separation(rows.drop(columns=["sep_arcsec", "pa_deg"]), host_ra, host_dec)
    # a named galaxy's centre: DR10 can fit its nucleus as a separate point source (NGC 873,
    # for SN 2022xjk: a PSF 0.18" from the Sesame position, the atlas galaxy 1.15"), so an
    # atlas galaxy within the tolerance is the host ahead of anything nearer
    atlas_here = near[(near["ref_cat"] == sc.ATLAS_REF_CAT) & (near["ref_id"] > 0) & (near["sep_arcsec"] <= tol)]
    if len(atlas_here):
        return key(rows.loc[atlas_here["sep_arcsec"].idxmin()]), "ok"
    k = near["sep_arcsec"].idxmin()
    if near.at[k, "sep_arcsec"] > tol:
        return None, "host_not_in_legacy"
    r = rows.loc[k]
    if sc.pieces(rows.loc[[k]]).iloc[0]:  # a piece of an atlas galaxy (maskbit 12): the galaxy is the host
        centrals = near[sc.atlas_central(near)]
        if centrals.empty:
            return None, "atlas_piece_without_galaxy"
        return key(rows.loc[centrals["sep_arcsec"].idxmin()]), "ok_atlas_piece"
    if r["type"] not in sc.GALAXY_TYPES:
        return key(r), "host_is_point_source"
    return key(r), "ok"


def score_position(footprint: str, ra: float, dec: float, host: tuple[float, float] | None = None,
                   tol: float = 1.0) -> dict:
    need = sc.SEARCH_RADIUS_ARCSEC if host is None else max(sc.SEARCH_RADIUS_ARCSEC, sep_arcsec(ra, dec, *host) + TRUTH_PAD_ARCSEC)
    rows = sc.neighbours(ra, dec, need, fetch=_tile)
    rows30 = rows[rows["sep_arcsec"] <= sc.SEARCH_RADIUS_ARCSEC]
    atlas_rows = sc.atlas_near(_atlas(footprint), ra, dec)
    gap = sc.catalogue_gap(ra, dec, _bricks(footprint), _maskbits)
    out = {"tile_rows": int(rows.attrs.get("tile_rows", 0)), "catalogue_gap": gap,
           "empty_15": bool((rows["sep_arcsec"] <= EMPTY_RADIUS_ARCSEC).sum() == 0),
           "n30": int((rows["sep_arcsec"] <= sc.SEARCH_RADIUS_ARCSEC).sum())}
    # the point-like rules, through sky_catalog's own functions (pieces excluded, as in production)
    out["stellar"] = sc.stellar_counterpart(rows30) is not None
    point = sc.point_counterpart(rows30)
    out["point_key"] = key(point) if point is not None else None
    compact = nearest_star_free_psf(rows30)  # for the dropped compact rule, any r_c
    out["compact_sep"] = round(float(compact["sep_arcsec"]), 2) if compact is not None else None
    out["compact_key"] = key(compact) if compact is not None else None
    true_key = None
    if host is not None:
        true_key, out["truth"] = true_host(rows, *host, tol)
        out["true_key"] = true_key
        if true_key is not None:
            hit = rows[[key(r) == true_key for _, r in rows.iterrows()]]
            if len(hit):
                out["true_sep"] = round(float(hit.iloc[0]["sep_arcsec"]), 2)
                out["true_type"] = str(hit.iloc[0]["type"])
    for name, (two_step, atlas_radius) in VARIANTS.items():
        c = candidates(rows30, atlas_rows, two_step, atlas_radius)
        v = {"best_d": None, "best_is_true": False, "true_d": None}
        if len(c):
            b = c["d"].idxmin()
            v.update(best_d=round(float(c.at[b, "d"]), 3), best_is_true=bool(c.at[b, "key"] == true_key),
                     best_sep=round(float(c.at[b, "sep_arcsec"]), 2), best_atlas=c.at[b, "key"].startswith("sga"))
            if true_key is not None and (c["key"] == true_key).any():
                v["true_d"] = round(float(c.loc[c["key"] == true_key, "d"].min()), 3)
        out[name] = v
    # self-check: the shortcut above must pick what production picks at its own settings
    prod = sc.best_host(rows30, atlas_rows=atlas_rows)
    mine = out[PRODUCTION_VARIANT]
    mine_key = None
    if mine["best_d"] is not None and mine["best_d"] <= sc.HOST_DLR_MAX:
        c = candidates(rows30, atlas_rows, True, sc.ATLAS_RADIUS)
        mine_key = c.at[c["d"].idxmin(), "key"]
    out["selfcheck_ok"] = (key(prod[1]) if prod else None) == mine_key
    return out


def known_pairs() -> list[dict]:
    pairs = []
    path = OUT_DIR / "sne.jsonl"
    for line in path.read_text().splitlines() if path.exists() else []:
        sn = json.loads(line)
        if "host_ra" in sn:
            pairs.append({"source": "TNS", "footprint": sn["footprint"], "name": sn["name"], "type": sn["type"],
                          "ra": sn["ra"], "dec": sn["dec"], "host_ra": sn["host_ra"], "host_dec": sn["host_dec"],
                          "host_name": sn["hostname"]})
    from astropy.table import Table
    t = Table.read(DES_HEAD).to_pandas()
    t = t[t["SNTYPE"].astype(int).isin(DES_SN_TYPES) & (t["HOSTGAL_RA"] > -900)]
    for fp, ((ra0, dec0, radius), _) in FOOTPRINTS.items():
        inside = [sc_sep(r.RA, r.DEC, ra0, dec0) <= radius * 3600 for r in t.itertuples()]
        for r in t[inside].itertuples():
            snid = r.SNID.decode() if isinstance(r.SNID, bytes) else str(r.SNID)
            pairs.append({"source": "DES", "footprint": fp, "name": snid.strip(),
                          "type": DES_SN_TYPES[int(r.SNTYPE)], "ra": float(r.RA), "dec": float(r.DEC),
                          "host_ra": float(r.HOSTGAL_RA), "host_dec": float(r.HOSTGAL_DEC),
                          "des_ddlr": float(r.HOSTGAL_DDLR), "host_name": str(r.HOSTGAL_OBJID)})
    return pairs


def sc_sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    return sep_arcsec(ra1, dec1, ra2, dec2)


def score_or_error(*args, **kw) -> dict:
    try:
        return score_position(*args, **kw)
    except sc.CatalogError as e:
        return {"error": str(e), "tile_rows": 0}


def fingerprint() -> dict[str, str]:
    return {name: (hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else "missing")
            for name, path in FINGERPRINTED.items()}


def write_meta(stage: str) -> None:
    meta = {"stage": stage, "scored_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "catalogue": sc.CATALOG_VERSION, "fingerprint": fingerprint()}
    (OUT_DIR / f"eval_{stage}.meta.json").write_text(json.dumps(meta, indent=1) + "\n")


def version_header() -> list[str]:
    """The report's header: what produced the scores, and a loud warning if it was not one version."""
    metas = {}
    for stage in ("known", "randoms"):
        path = OUT_DIR / f"eval_{stage}.meta.json"
        metas[stage] = json.loads(path.read_text()) if path.exists() else None
    now = fingerprint()
    lines = [f"# Host-matching validation - tools/host_match_check.py report, "
             f"{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}",
             f"# Catalogues: {sc.CATALOG_VERSION}. Settings: HOST_DLR_MAX {sc.HOST_DLR_MAX:g}, ATLAS_RADIUS {sc.ATLAS_RADIUS}, "
             f"search {sc.SEARCH_RADIUS_ARCSEC:g}\", atlas margin {sc.ATLAS_MARGIN_DEG:g} deg, gap radius {sc.GAP_RADIUS_ARCSEC:g}\""]
    for stage, meta in metas.items():
        lines.append(f"# Scores '{stage}': " + (f"{meta['scored_utc']}" if meta else "no sidecar (unknown version)"))
    lines.append("# Fingerprint (sha256, 12 hex) of what decides the scores:")
    for name, h in now.items():
        lines.append(f"#   {name:36s} {h}")
    problems = [stage for stage, meta in metas.items() if meta is None or meta["fingerprint"] != now]
    if problems:
        lines.append(f"# MIXED VERSIONS: scores '{', '.join(problems)}' came from other code or inputs than the files "
                     f"above - rerun evaluate and randoms before using this report")
    return lines


def run_evaluate() -> int:
    pairs = known_pairs()
    print(f"{len(pairs)} known-host pairs: " + ", ".join(
        f"{src} {sum(p['source'] == src for p in pairs)}" for src in ("TNS", "DES")), flush=True)
    prefetch([(p["ra"], p["dec"], max(sc.SEARCH_RADIUS_ARCSEC, sep_arcsec(p["ra"], p["dec"], p["host_ra"], p["host_dec"])
                                      + TRUTH_PAD_ARCSEC)) for p in pairs])
    prefetch_maskbits(set().union(*(brick_names(fp, [(p["ra"], p["dec"]) for p in pairs if p["footprint"] == fp])
                                    for fp in {p["footprint"] for p in pairs})))
    with open(OUT_DIR / "eval_known.jsonl", "w") as f:
        for p in pairs:
            f.write(json.dumps({**p, **score_or_error(p["footprint"], p["ra"], p["dec"], (p["host_ra"], p["host_dec"]),
                                                      HOST_MATCH_ARCSEC[p["source"]])}) + "\n")
    write_meta("known")
    print("done", flush=True)
    return 0


def random_positions(footprint: str, rng: random.Random) -> list[tuple[float, float]]:
    ra0, dec0, radius = FOOTPRINTS[footprint][0]
    out = []
    while len(out) < RANDOM_TILES * RANDOM_PER_TILE:
        # a tile at a uniform point of the cone, then positions uniform inside the tile
        r, phi = radius * math.sqrt(rng.random()), rng.uniform(0, 2 * math.pi)
        ra = ra0 + r * math.sin(phi) / math.cos(math.radians(dec0))
        dec = dec0 + r * math.cos(phi)
        i, j = sc.tile_of(ra, dec)
        for _ in range(RANDOM_PER_TILE):
            pra = (i + rng.random()) * sc.TILE_DEG
            pdec = (j + rng.random()) * sc.TILE_DEG - 90
            if sep_arcsec(pra, pdec, ra0, dec0) <= radius * 3600:
                out.append((pra, pdec))
    return out[: RANDOM_TILES * RANDOM_PER_TILE]


def run_randoms(footprints: list[str]) -> int:
    rng = random.Random(RANDOM_SEED)
    with open(OUT_DIR / "eval_randoms.jsonl", "w") as f:
        for fp in footprints:
            pos = random_positions(fp, rng)
            print(f"{fp}: {len(pos)} random positions", flush=True)
            prefetch([(ra, dec, sc.SEARCH_RADIUS_ARCSEC) for ra, dec in pos])
            prefetch_maskbits(brick_names(fp, pos))
            for ra, dec in pos:
                f.write(json.dumps({"footprint": fp, "ra": ra, "dec": dec, **score_or_error(fp, ra, dec)}) + "\n")
    write_meta("randoms")
    print("done", flush=True)
    return 0


def nearest_star_free_psf(rows: pd.DataFrame) -> pd.Series | None:
    """The dropped rule's candidate: nearest PSF beyond 1" with no Gaia star signature, pieces excluded."""
    ring = rows[(rows["type"] == "PSF") & (rows["sep_arcsec"] > sc.COUNTERPART_RADIUS_ARCSEC) & ~sc.pieces(rows)]
    for _, row in ring.sort_values("sep_arcsec").iterrows():
        if not sc._gaia_star(row):
            return row
    return None


DR10_MASKBITS = {0: "NPRIMARY", 1: "BRIGHT", 2: "SATUR_G", 3: "SATUR_R", 4: "SATUR_Z", 5: "ALLMASK_G",
                 6: "ALLMASK_R", 7: "ALLMASK_Z", 8: "WISEM1", 9: "WISEM2", 10: "BAILOUT", 11: "MEDIUM",
                 12: "GALAXY", 13: "CLUSTER", 14: "SATUR_I", 15: "ALLMASK_I", 16: "SUB_BLOB"}


def maskbits_named(footprint: str, ra: float, dec: float) -> str:
    """The DR10 maskbits at a position, by name (from the brick's maskbits image)."""
    b = _bricks(footprint)
    hit = b[(b["ra1"] <= ra) & (b["ra2"] > ra) & (b["dec1"] <= dec) & (b["dec2"] > dec)]
    if hit.empty:
        return "no brick"
    got = _maskbits(hit.iloc[0]["brickname"])
    if got is None:
        return "no brick"
    image, wcs = got
    x, y = (int(round(float(v))) for v in wcs.all_world2pix([[ra, dec]], 0)[0])
    value = int(image[y, x])
    return ", ".join(name for bit, name in DR10_MASKBITS.items() if value >> bit & 1) or "none set"


def outcome(v: dict, t: float) -> str:
    if v["best_d"] is None or v["best_d"] > t:
        return "orphan"
    return "correct" if v["best_is_true"] else "wrong"


def outcome_with_compact(rec: dict, name: str, t: float, rc: float) -> str:
    """sky_catalog's order: stellar, point_source within 1", the DLR host, then a compact host within rc."""
    if rec.get("stellar"):
        return "stellar"
    if rec.get("point_key"):
        return "point_source"
    first = outcome(rec[name], t)
    if first != "orphan":
        return first
    if rec.get("compact_sep") is not None and rec["compact_sep"] <= rc:
        return "compact_correct" if rec.get("compact_key") == rec.get("true_key") else "compact_wrong"
    return "orphan"


def run_report() -> int:
    print("\n".join(version_header()) + "\n")
    known = [json.loads(l) for l in (OUT_DIR / "eval_known.jsonl").read_text().splitlines()]
    rnd_path = OUT_DIR / "eval_randoms.jsonl"
    rnd = [json.loads(l) for l in rnd_path.read_text().splitlines()] if rnd_path.exists() else []
    errors = [k for k in known if "error" in k] + [r for r in rnd if "error" in r]
    known = [k for k in known if "error" not in k]
    rnd = [r for r in rnd if "error" not in r and r["tile_rows"] > 0]
    gaps_k = [k for k in known if k.get("catalogue_gap")]
    gaps_r = [r for r in rnd if r.get("catalogue_gap")]
    all_fps = sorted({r["footprint"] for r in rnd} | {k["footprint"] for k in known})
    print(f"Catalogue gap (DR10 BAILOUT within the {sc.GAP_RADIUS_ARCSEC:g}\" step-1 area, or no brick), excluded from every score:")
    for src in ("DES", "TNS"):
        print(f"  {src} pairs: " + ", ".join(f"{fp} {sum(k['footprint'] == fp for k in gaps_k if k['source'] == src)}"
                                             f"/{sum(k['footprint'] == fp for k in known if k['source'] == src)}" for fp in all_fps))
    print("  random:    " + ", ".join(f"{fp} {sum(r['footprint'] == fp for r in gaps_r)}/{sum(r['footprint'] == fp for r in rnd)}"
                                      for fp in sorted({r['footprint'] for r in rnd})))
    unexplained = [x for x in known + rnd if x.get("empty_15") and not x.get("catalogue_gap")]
    # chance voids: with the density each position sees within 30", an empty 15" circle has
    # P = exp(-n30 * (15/30)^2); summed over every position that is the count expected by chance
    pool = [x for x in known + rnd if not x.get("catalogue_gap") and "n30" in x]
    frac = (EMPTY_RADIUS_ARCSEC / sc.SEARCH_RADIUS_ARCSEC) ** 2
    expected = sum(math.exp(-x["n30"] * frac) for x in pool)
    print(f"  no DR10 source within {EMPTY_RADIUS_ARCSEC:g}\" yet no BAILOUT: {len(unexplained)}, against "
          f"{expected:.1f} expected by chance from each position's own density (chance voids, not catalogue faults)")
    for x in unexplained:
        print(f"    {x['footprint']} ({x['ra']:.5f}, {x['dec']:+.5f}): {x.get('n30', '?')} sources within 30\", "
              f"maskbits {maskbits_named(x['footprint'], x['ra'], x['dec'])}")
    print()
    known = [k for k in known if not k.get("catalogue_gap")]
    rnd = [r for r in rnd if not r.get("catalogue_gap")]
    if errors:
        print(f"Fetch error, excluded from every score: {len(errors)} positions "
              f"({sum('source' in e for e in errors)} known-host, {sum('source' not in e for e in errors)} random; "
              f"rerun to fill them)\n")
    suspect = [k for k in known if k["name"] in SUSPECT_RECORDS]
    for k in suspect:
        print(f"Suspect record, excluded from every score: {k['source']} {k['name']} - {SUSPECT_RECORDS[k['name']]}")
    known = [k for k in known if k["name"] not in SUSPECT_RECORDS]
    scored_ok = ("ok", "ok_atlas_piece")
    print("Known-host pairs by status (only ok* are scored; host_not_in_legacy for DES = below Legacy depth, "
          "catalogue_hole_at_host = DR10 hole, not depth):")
    for src in ("DES", "TNS"):
        st = pd.Series([k["truth"] for k in known if k["source"] == src]).value_counts()
        print(f"  {src}: " + ", ".join(f"{s_} {n}" for s_, n in st.items()))
    bad = sum(not k["selfcheck_ok"] for k in known) + sum(not r["selfcheck_ok"] for r in rnd)
    print(f"Self-check against sky_catalog.best_host: {bad} disagreements over {len(known) + len(rnd)} positions\n")
    des = [k for k in known if k["source"] == "DES" and k["truth"] in scored_ok]
    tns = [k for k in known if k["source"] == "TNS" and k["truth"] in scored_ok]
    fps = sorted({r["footprint"] for r in rnd})
    for name in VARIANTS:
        print(name)
        print(f"  {'d_DLR <=':>8s}   {'DES: agree':>10s} {'other':>6s} {'none':>5s}   {'TNS: ok':>7s} {'wrong':>5s} {'orph':>4s}"
              f"   random gets a host: {'  '.join(f'{fp:>6s}' for fp in fps)}  {'all':>6s}")
        for t in THRESHOLDS:
            o = pd.Series([outcome(k[name], t) for k in des])
            ot = pd.Series([outcome(k[name], t) for k in tns])
            ch = [np.mean([r[name]["best_d"] is not None and r[name]["best_d"] <= t for r in rnd if r["footprint"] == fp]) for fp in fps]
            ch_all = np.mean([r[name]["best_d"] is not None and r[name]["best_d"] <= t for r in rnd]) if rnd else np.nan
            print(f"  {t:8.1f}   {(o == 'correct').mean():10.0%} {(o == 'wrong').mean():6.0%} {(o == 'orphan').mean():5.0%}"
                  f"   {(ot == 'correct').sum():7d} {(ot == 'wrong').sum():5d} {(ot == 'orphan').sum():4d}"
                  f"   {'':19s}{'  '.join(f'{c:6.1%}' for c in ch)}  {ch_all:6.1%}")
        print()
    base = 'step 1 only, 30" (before the fix)'
    t = sc.HOST_DLR_MAX
    print(f"What step 2 changes, at d_DLR <= {t:g} (baseline: {base}; the baseline sets nothing):")
    print(f"  {'footprint':9s} {'rescued':>8s} {'broken':>7s} {'other':>6s}   {'random newly hosted (cost)':>27s}")
    changes = []
    for fp in all_fps:
        moved = [(k, outcome(k[base], t), outcome(k[PRODUCTION_VARIANT], t)) for k in des + tns if k["footprint"] == fp]
        moved = [m for m in moved if m[1] != m[2]]
        rescued = [m for m in moved if m[2] == "correct"]
        broken = [m for m in moved if m[1] == "correct"]
        rr = [r for r in rnd if r["footprint"] == fp]
        cost = sum(outcome(r[base], t) == "orphan" and outcome(r[PRODUCTION_VARIANT], t) != "orphan" for r in rr)
        print(f"  {fp:9s} {len(rescued):8d} {len(broken):7d} {len(moved) - len(rescued) - len(broken):6d}   "
              f"{cost:>11d} of {len(rr):5d} ({cost / max(len(rr), 1):.1%})")
        changes += [(kind, m) for kind, ms in (("rescued", rescued), ("broken", broken)) for m in ms]
        changes += [("other", m) for m in moved if m not in rescued and m not in broken]
    for kind, (k, before, after) in changes:
        v = k[PRODUCTION_VARIANT]
        print(f"    {kind:8s} {k['source']} {k['footprint']:6s} {k['name']:10s} {before:7s} -> {after:7s}  pick sep "
              f"{v.get('best_sep', '-')}\" d_DLR {v['best_d']}{' (atlas galaxy)' if v.get('best_atlas') else ''}")
    print()

    print(f"point_source before host, at d_DLR <= {t:g}: known-host pairs whose label changes (a star-free PSF within 1\")")
    flips = [k for k in des + tns if k.get("point_key") and outcome(k[PRODUCTION_VARIANT], t) != "orphan"]
    print(f"  {len(flips)} of {len(des) + len(tns)} scored pairs"
          + "".join(f"\n    {k['source']} {k['footprint']} {k['name']}: was {outcome(k[PRODUCTION_VARIANT], t)}"
                    f"{' (the PSF is the reported host)' if k['point_key'] == k.get('true_key') else ''}" for k in flips))
    print("  random positions with a star-free PSF as the nearest source within 1\": "
          + ", ".join(f"{fp} {np.mean([bool(r.get('point_key')) for r in rnd if r['footprint'] == fp]):.2%}" for fp in fps))
    print()
    pts = [k for k in known if k.get("truth") == "host_is_point_source" and not k.get("catalogue_gap")]
    inner = [k for k in pts if k.get("true_sep") is not None and k["true_sep"] <= sc.COUNTERPART_RADIUS_ARCSEC]
    taken = [k for k in inner if outcome(k[PRODUCTION_VARIANT], t) != "orphan"]
    print(f"If host ran before point_source: of the {len(inner)} PSF-typed hosts within 1\", {len(taken)} would get an "
          f"extended galaxy as DLR host (a wrong host); point_source first keeps them, and relabels the {len(flips)} above")
    for k in taken:
        v = k[PRODUCTION_VARIANT]
        print(f"    {k['source']} {k['footprint']} {k['name']}: PSF host at {k['true_sep']}\", DLR pick at {v.get('best_sep')}\" d_DLR {v['best_d']}")
    print()
    seps = sorted(k["true_sep"] for k in pts if k.get("true_sep") is not None)
    print(f"Compact-host rule (measured, dropped from production), at d_DLR <= {t:g}:")
    print(f"  hit side: {len(seps)} reported hosts typed PSF in DR10, separations: " + ", ".join(f'{x:.1f}"' for x in seps))
    print(f"  {'r_c':>5s}  {'PSF hosts within':>16s}  {'picked as compact host':>22s}  {'random: compact host':>20s}"
          + "  ".join(f"{fp:>7s}" for fp in fps))
    for rc in COMPACT_RADII:
        within = sum(x <= rc for x in seps)
        picked = sum(outcome_with_compact(k, PRODUCTION_VARIANT, t, rc) == "compact_correct" for k in pts)
        per_fp = [np.mean([outcome_with_compact(r, PRODUCTION_VARIANT, t, rc).startswith("compact") for r in rnd
                           if r["footprint"] == fp]) for fp in fps]
        all_ = np.mean([outcome_with_compact(r, PRODUCTION_VARIANT, t, rc).startswith("compact") for r in rnd]) if rnd else np.nan
        print(f"  {rc:4.1f}\"  {within:9d}/{len(seps):<6d}  {picked:14d}/{len(pts):<7d}  {all_:20.1%}" + "  ".join(f"{c:7.1%}" for c in per_fp))
    rc = 3.0
    print(f"  all known-host pairs with the (dropped) compact rule at r_c = {rc:g}\" (scored pairs plus PSF-typed hosts):")
    for src in ("DES", "TNS"):
        o = pd.Series([outcome_with_compact(k, PRODUCTION_VARIANT, t, rc) for k in (des if src == "DES" else tns) + [p for p in pts if p["source"] == src]])
        print(f"    {src}: " + ", ".join(f"{name} {n}" for name, n in o.value_counts().items()))
    print()

    print("DES hosts: our d_DLR (production variant) against DES's HOSTGAL_DDLR")
    pairs = [(k[PRODUCTION_VARIANT]["true_d"], k["des_ddlr"]) for k in des if k[PRODUCTION_VARIANT]["true_d"] is not None and k["des_ddlr"] > 0]
    if pairs:
        r = np.array([a / b for a, b in pairs])
        print(f"  n={len(pairs)}  ratio ours/DES median {np.median(r):.2f} (16-84%: {np.quantile(r, .16):.2f}-{np.quantile(r, .84):.2f})")
    print("\nTNS hosts, production variant:")
    for k in sorted(known, key=lambda k: (k["source"] != "TNS", k["footprint"], k["name"])):
        if k["source"] != "TNS":
            continue
        v = k[PRODUCTION_VARIANT]
        flag = f"  [suspect: {SUSPECT_RECORDS[k['name']]}]" if k["name"] in SUSPECT_RECORDS else ""
        print(f"  {k['footprint']:6s} {k['name']:10s} {k['host_name'][:28]:28s} {k['truth']:22s} "
              f"true d_DLR {v['true_d'] if v['true_d'] is not None else '-':>6}  pick d {v['best_d'] if v['best_d'] is not None else '-':>6} "
              f"{'= host' if v['best_is_true'] else '!= host'}{flag}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="build the SN sample with reported hosts (network, resumable)")
    s.add_argument("--footprints", nargs="+", choices=sorted(FOOTPRINTS), default=sorted(FOOTPRINTS))
    sub.add_parser("evaluate", help="score the TNS and DES known-host pairs")
    r = sub.add_parser("randoms", help="score random positions in the footprints")
    r.add_argument("--footprints", nargs="+", choices=sorted(FOOTPRINTS), default=sorted(FOOTPRINTS))
    sub.add_parser("report", help="tables from the scored files, no network")
    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.cmd == "sample":
        return run_sample(args.footprints)
    if args.cmd == "evaluate":
        return run_evaluate()
    if args.cmd == "randoms":
        return run_randoms(args.footprints)
    return run_report()


if __name__ == "__main__":
    sys.exit(main())
