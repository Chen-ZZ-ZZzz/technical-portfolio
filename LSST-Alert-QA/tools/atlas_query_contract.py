"""
Contract check: does tests/fake_tap.py read q3c_radial_query the way Data Lab does?

The unit test of the footprint atlas (test_the_margin_fetches_an_atlas_galaxy_just_outside
_the_footprint) runs sky_catalog's real query against a fake TAP service that applies
the query's q3c cone itself. If the fake's reading of q3c (argument order, degrees, how
the radius applies) were wrong, fake and code would agree while the real server returned
something else. This check sends the same footprint query, margin included, to the real
server, runs the fake on a superset fetched without q3c (a plain RA/Dec box, same
filters, comfortably larger than the cone), and compares the atlas IDs. Equal sets
validate the fake. On demand only, outside the test suite; rerun it when the footprint
query changes. Exit code 0 = match, 1 = mismatch.

Second check: step 2 takes atlas galaxies through DR10's L3 central rows. Where DR10's
fitting gave up (BAILOUT) a row could in principle be missing although the galaxy is in
the atlas, so the atlas table queried directly is compared with the L3 join for the
same cone (2026-10-01: C 768 = 768, D 1173 = 1173, ecdfs 1002 = 1002; brick 0532m280,
49% BAILOUT, still has its one atlas galaxy). Run it for any new footprint.

Usage:
    uv run tools/atlas_query_contract.py              # footprint D
    uv run tools/atlas_query_contract.py --footprint ecdfs
"""

from __future__ import annotations

import argparse
import io
import math
import pathlib
import sys

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]  # imports only (src/, tests.fake_tap); no data paths here
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from rubin_qa import sky_catalog as sc  # noqa: E402
from rubin_qa.transient_monitor import FOOTPRINTS  # noqa: E402
from tests.fake_tap import great_circle_deg, q3c_tap  # noqa: E402

BOX_PAD_DEG = 0.3  # the superset box reaches this far beyond the cone + margin
EDGE_SHOW = 5      # galaxies nearest the cone edge, listed on each side


def tap_csv(query: str, first_column: str = "ra") -> pd.DataFrame:
    r = requests.get(sc.TAP_URL, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                         "MAXREC": sc.TAP_MAXREC, "QUERY": query}, timeout=sc.ATLAS_TAP_TIMEOUT)
    if r.status_code != 200 or not r.text.startswith(f"{first_column},"):
        raise sc.CatalogError(f"HTTP {r.status_code} {r.text[:120]!r}")
    return pd.read_csv(io.StringIO(r.text))


def superset_query(cone: tuple[float, float, float]) -> str:
    """
    The same rows as the footprint query, by an RA/Dec box instead of q3c. Deliberately a
    box, not a cone: an oracle has to be independent of the thing it checks.
    """
    ra, dec, radius = cone
    r = radius + sc.ATLAS_MARGIN_DEG + BOX_PAD_DEG
    dra = r / math.cos(math.radians(min(abs(dec) + r, 89.0)))
    return sc._select(f"t.ra >= {ra - dra:.6f} AND t.ra < {ra + dra:.6f} AND t.dec >= {dec - r:.6f} "
                      f"AND t.dec < {dec + r:.6f} AND t.ref_cat = '{sc.ATLAS_REF_CAT}' AND t.ref_id > 0")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--footprint", choices=sorted(FOOTPRINTS), default="D")
    args = p.parse_args()
    cone = FOOTPRINTS[args.footprint][0]
    query = sc.footprint_atlas_query(cone)
    reach = cone[2] + sc.ATLAS_MARGIN_DEG
    print(f"footprint {args.footprint}: cone {cone}, margin {sc.ATLAS_MARGIN_DEG:g} deg -> radius {reach:g} deg", flush=True)

    real = tap_csv(query)
    sky = tap_csv(superset_query(cone))
    fake = pd.read_csv(io.StringIO(q3c_tap(sky)(sc.TAP_URL, {"QUERY": query}, None).text))
    ids_real, ids_fake = set(real["ref_id"]), set(fake["ref_id"])
    print(f"real server: {len(ids_real)} atlas galaxies; superset box: {len(sky)}; fake on the superset: {len(ids_fake)}")

    sky["dist_deg"] = great_circle_deg(sky["ra"], sky["dec"], cone[0], cone[1])
    sky["in_real"] = sky["ref_id"].isin(ids_real)
    inside = sky[sky["dist_deg"] <= reach].nlargest(EDGE_SHOW, "dist_deg")
    outside = sky[sky["dist_deg"] > reach].nsmallest(EDGE_SHOW, "dist_deg")
    print(f"nearest the edge ({reach:g} deg), as the real server answered:")
    for _, g in pd.concat([inside, outside]).sort_values("dist_deg").iterrows():
        print(f"  SGA {int(g.ref_id):8d}  {g.dist_deg:.5f} deg  {'returned' if g.in_real else 'not returned'}")
    if len(ids_real) != len(real) or not ids_real <= set(sky["ref_id"]):
        print("superset does not contain every real row: widen BOX_PAD_DEG")
        return 1
    ok = ids_real == ids_fake
    print("MATCH: the fake reads q3c as the server does" if ok else
          f"MISMATCH: only real {sorted(ids_real - ids_fake)}  only fake {sorted(ids_fake - ids_real)}")

    atlas = tap_csv(f"SELECT sga_id, galaxy FROM {sc.ATLAS_TABLE} WHERE "
                    f"'t' = q3c_radial_query(ra, dec, {cone[0]:.6f}, {cone[1]:.6f}, {reach:.6f})", "sga_id")
    missing = set(atlas["sga_id"]) - ids_real
    print(f"atlas table in the same cone: {len(atlas)}; missing from the L3 join: {len(missing)}"
          + (f" {sorted(missing)[:20]} - source step 2 from the atlas table" if missing else ""))
    return 0 if ok and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
