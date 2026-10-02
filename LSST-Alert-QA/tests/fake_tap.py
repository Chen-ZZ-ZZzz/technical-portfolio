"""
A fake TAP service that applies the q3c cone of the query it is sent, for testing
sky_catalog's footprint query end to end without the network.

It encodes one reading of q3c_radial_query(ra_col, dec_col, ra0, dec0, radius):
argument order, degrees, and great-circle distance <= radius. That reading is checked
against the real server by tools/atlas_query_contract.py (rerun it if the query changes).
"""

import math
import re
from types import SimpleNamespace

import pandas as pd

Q3C = re.compile(r"q3c_radial_query\(t\.ra, t\.dec, ([-\d.]+), ([-\d.]+), ([-\d.]+)\)")


def great_circle_deg(ra1, dec1, ra2, dec2):
    r = math.radians
    return [math.degrees(2 * math.asin(math.sqrt(
        math.sin(r(d - dec2) / 2) ** 2 + math.cos(r(d)) * math.cos(r(dec2)) * math.sin(r(a - ra2) / 2) ** 2)))
        for a, d in zip(ra1, dec1)]


def q3c_tap(sky: pd.DataFrame):
    """A get() for sky_catalog._fetch_cached: the rows of sky inside the query's q3c cone, as CSV."""
    def get(url, params, timeout):
        ra0, dec0, radius = map(float, Q3C.search(params["QUERY"]).groups())
        inside = sky[[d <= radius for d in great_circle_deg(sky["ra"], sky["dec"], ra0, dec0)]]
        return SimpleNamespace(status_code=200, text=inside.to_csv(index=False))
    return get
