"""
The transient monitor's live path works with ANTARES impossible to import (user, 2026-10-03).

ANTARES left the live path on 2026-10-03: listing, light curves and TNS all come from
ALeRCE. This guard keeps it out. It runs the monitor's daily command (the CLI, --dry-run,
footprint ecdfs) in a fresh interpreter in which antares_client and rubin_qa.antares_api
cannot be imported, and records every attempt, so an import that a try/except swallows
still fails the test. Only the network is faked, at its edges: ALeRCE's client methods,
requests.get (Data Lab TAP, the maskbits portal) and requests.post (ALeRCE's TNS service),
and any other connection raises. Everything between them is the real code, so an ANTARES
call added anywhere on the path - listing, photometry, DR10, TNS, the point-source watch -
is caught, not only where a fake would have stood in.

The fake day carries one new ZTF transient, bracketed, on a DR10 galaxy and in TNS, so
the run goes through every stage; LSST is quiet, as it is in ecdfs. The retired SSO
monitor and the host tool's validation sample may keep using ANTARES: they are not the
live path. A fresh interpreter, because pytest's own process has imported ANTARES
modules for other tests already, and an imported module never asks the import system again.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

BLOCKED = ("antares_client", "rubin_qa.antares_api")
ATTEMPTS_FILE = "antares_import_attempts.json"
FOOTPRINT = "ecdfs"
TRANSIENT = (53.2, -27.7)  # inside the ecdfs cone, b = -54 deg
OID = "ZTF26aaguard"
BRICK = "0532m277"


def test_the_live_scan_runs_with_antares_impossible_to_import(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / f"transient_monitor_{FOOTPRINT}.json").write_text(
        json.dumps({"last_mjd": None, "reported": {}, "parked": {}}))  # an installation, as --init leaves it
    (state_dir / "point_source_watch.json").write_text(json.dumps({"objects": {}}))
    env = {**os.environ, "RUBIN_QA_ROOT": str(tmp_path)}
    proc = subprocess.run([sys.executable, __file__], env=env, capture_output=True, text=True, timeout=300)
    out = proc.stdout

    attempts_file = tmp_path / ATTEMPTS_FILE
    assert attempts_file.exists(), f"the run stopped before the scan:\n{proc.stderr}"
    attempts = json.loads(attempts_file.read_text())
    assert attempts == [], f"the live path tried to import ANTARES: {attempts}\n{proc.stderr}"
    assert proc.returncode == 0, proc.stderr
    # the run went through every stage, so the guard covered them all
    assert "ZTF listing (ALeRCE): 1 objects" in out
    assert "LSST listing (ALeRCE): none active since" in out and "upstream quiet" in out
    assert f"ztf:{OID}  host · bracketed  new: bracketed" in out
    assert '  DR10: host - EXP r=20.0 sep=1.5" d_DLR=0.7' in out
    assert "  TNS: SN 2026grd, SN Ia, discovered" in out
    assert "Point-source watch:" in out
    assert "dry run: state not saved" in out


# ------------------------------------------------- run in the fresh interpreter


class _AntaresBlocker:
    """A meta-path finder that refuses the BLOCKED modules and records every attempt."""

    def __init__(self):
        self.attempts: list[str] = []

    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in BLOCKED):
            self.attempts.append(name)
            raise ImportError(f"{name} is blocked: the transient monitor's live path must not need ANTARES")
        return None


def _csv(rows: list[dict], columns) -> str:
    import pandas as pd

    return pd.DataFrame(rows, columns=list(columns)).to_csv(index=False)


def _fake_network(now: float) -> None:
    """ALeRCE, Data Lab, the maskbits portal and the TNS service, as they answer on a day with one transient."""
    import io
    import re
    import socket
    import time
    from types import SimpleNamespace

    import numpy as np
    import requests
    from astropy.io import fits
    from astropy.wcs import WCS

    from rubin_qa import client, sky_catalog, tns

    def no_network(*args, **kwargs):
        raise RuntimeError("a connection the fakes do not cover")

    socket.socket.connect = no_network
    time.sleep = lambda s: None  # TNS pacing, retries
    ra, dec = TRANSIENT

    # ALeRCE: ZTF lists one object, LSST nothing (and its newest object is months old)
    ztf_item = {"oid": OID, "meanra": ra, "meandec": dec, "firstmjd": now - 5, "lastmjd": now - 3, "ndet": 3,
                "class": "SN", "classifier": "stamp_classifier", "probability": 0.8}
    lsst_newest = {"oid": 1700, "meanra": 53.0, "meandec": -27.9, "firstmjd": now - 300, "lastmjd": now - 200}

    def query_objects(format="json", survey="ztf", **kw):
        if survey == "lsst":
            return [lsst_newest] if "order_by" in kw else []
        return {"items": [ztf_item]}

    def query_lightcurve(oid, format="json", survey="ztf"):
        assert (survey, oid) == ("ztf", OID), f"unexpected light curve {survey}:{oid}"
        dets = [{"mjd": now - d, "fid": 2, "magpsf": m, "sigmapsf": 0.05, "isdiffpos": "t"}
                for d, m in ((5, 19.0), (4, 18.9), (3, 18.8))]
        return {"detections": dets, "non_detections": [{"mjd": now - 6, "fid": 2, "diffmaglim": 21.0}]}

    client._client.query_objects = query_objects
    client._client.query_lightcurve = query_lightcurve

    # Data Lab: one exponential galaxy 1.5" from the transient, one brick, no atlas galaxy
    galaxy = {"ra": ra, "dec": dec + 1.5 / 3600, "type": "EXP", "flux_g": 8.0, "flux_r": 10.0, "flux_z": 12.0,
              "flux_w1": 0.0, "flux_w2": 0.0, "flux_ivar_w1": 0.0, "flux_ivar_w2": 0.0, "shape_r": 2.0,
              "shape_e1": 0.0, "shape_e2": 0.0, "parallax": 0.0, "parallax_ivar": 0.0, "pmra": 0.0,
              "pmra_ivar": 0.0, "pmdec": 0.0, "pmdec_ivar": 0.0, "ref_cat": "", "ref_id": 0, "maskbits": 0}
    tile_cols = (*sky_catalog.COLUMNS, *sky_catalog.ATLAS_COLUMNS)
    half = 0.125
    brick = {"brickname": BRICK, "ra1": ra - half / math.cos(math.radians(dec)),
             "ra2": ra + half / math.cos(math.radians(dec)), "dec1": dec - half, "dec2": dec + half}
    box = re.compile(r"t\.ra >= ([-\d.]+) AND t\.ra < ([-\d.]+) AND t\.dec >= ([-\d.]+) AND t\.dec < ([-\d.]+)")

    w = WCS(naxis=2)  # a maskbits image around the transient, no BAILOUT anywhere
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [150.5, 150.5]
    w.wcs.cd = [[-0.262 / 3600, 0], [0, 0.262 / 3600]]
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(np.zeros((300, 300), dtype=np.int16), header=w.to_header(),
                                                        name="MASKBITS")]).writeto(buf)
    maskbits = buf.getvalue()

    def get(url, params=None, timeout=None):
        if url == sky_catalog.TAP_URL:
            query = params["QUERY"]
            if sky_catalog.BRICKS_TABLE in query:
                return SimpleNamespace(status_code=200, text=_csv([brick], sky_catalog.BRICK_COLUMNS))
            m = box.search(query)
            if m:  # a tile
                ra0, ra1, dec0, dec1 = map(float, m.groups())
                inside = ra0 <= galaxy["ra"] < ra1 and dec0 <= galaxy["dec"] < dec1
                return SimpleNamespace(status_code=200, text=_csv([galaxy] if inside else [], tile_cols))
            return SimpleNamespace(status_code=200, text=_csv([], tile_cols))  # the footprint atlas: none
        if url == sky_catalog.MASKBITS_URL.format(group=BRICK[:3], brick=BRICK):
            return SimpleNamespace(status_code=200, content=maskbits)
        raise RuntimeError(f"unexpected GET {url}")

    # ALeRCE's TNS service: the transient is SN 2026grd; the canary answers as always
    def post(url, json=None, timeout=None):
        assert url == tns.ALERCE_TNS_URL, f"unexpected POST {url}"
        records = {(ra, dec): {"objname": "2026grd", "name_prefix": "SN", "object_type": {"id": 3, "name": "SN Ia"},
                               "radeg": ra, "decdeg": dec, "discoverydate": "2026-09-30 05:00:00"},
                   (tns.TNS_CANARY["ra"], tns.TNS_CANARY["dec"]): {"objname": tns.TNS_CANARY["name"]}}
        rec = next((r for (r_ra, r_dec), r in records.items()
                    if abs(r_ra - json["ra"]) < 1e-4 and abs(r_dec - json["dec"]) < 1e-4), {})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"object_data": rec})

    requests.get = get
    requests.post = post


def _run_daily_scan() -> int:
    """The daily command, `python -m rubin_qa.transient_monitor --dry-run`, with ANTARES blocked."""
    import runpy

    blocker = _AntaresBlocker()
    sys.meta_path.insert(0, blocker)
    loaded = [m for m in sys.modules if any(m == b or m.startswith(b + ".") for b in BLOCKED)]
    assert not loaded, f"already imported, the block cannot apply: {loaded}"
    try:
        import antares_client  # noqa: F401
    except ImportError:
        pass
    else:
        raise AssertionError("the block does not hold")
    blocker.attempts.clear()

    from rubin_qa.config import PROJECT_ROOT, now_mjd

    _fake_network(now_mjd())
    sys.argv = ["transient_monitor", "--dry-run"]
    code = 1
    try:
        runpy.run_module("rubin_qa.transient_monitor", run_name="__main__", alter_sys=True)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        (PROJECT_ROOT / ATTEMPTS_FILE).write_text(json.dumps(blocker.attempts))
    return code


if __name__ == "__main__":
    sys.exit(_run_daily_scan())
