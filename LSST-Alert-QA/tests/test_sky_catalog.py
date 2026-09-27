"""sky_catalog.py — Legacy Surveys DR10 tiles and local matching. No live TAP calls."""

import math
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from rubin_qa import sky_catalog as sc

RA, DEC = 150.0, 2.0
ARCSEC = 1 / 3600


def row(dra_arcsec=0.0, ddec_arcsec=0.0, type_="EXP", flux_r=1.0, shape_r=1.0, e1=0.0, e2=0.0,
        parallax=0.0, parallax_ivar=0.0, pmra=0.0, pmra_ivar=0.0, pmdec=0.0, pmdec_ivar=0.0,
        w1=0.0, w2=0.0, ivar_w1=0.0, ivar_w2=0.0):
    return {"ra": RA + dra_arcsec * ARCSEC / math.cos(math.radians(DEC)), "dec": DEC + ddec_arcsec * ARCSEC,
            "type": type_, "flux_g": flux_r, "flux_r": flux_r, "flux_z": flux_r, "flux_w1": w1, "flux_w2": w2,
            "flux_ivar_w1": ivar_w1, "flux_ivar_w2": ivar_w2, "shape_r": shape_r, "shape_e1": e1, "shape_e2": e2,
            "parallax": parallax, "parallax_ivar": parallax_ivar, "pmra": pmra, "pmra_ivar": pmra_ivar,
            "pmdec": pmdec, "pmdec_ivar": pmdec_ivar}


def catalog(*rows):
    df = pd.DataFrame(list(rows), columns=list(sc.COLUMNS))
    return lambda i, j: df if (i, j) == sc.tile_of(RA, DEC) else pd.DataFrame(columns=list(sc.COLUMNS))


FILLER = row(dra_arcsec=25, type_="PSF", shape_r=0.0)  # something in the tile, far away


# --- tiles -----------------------------------------------------------------------


def test_tile_of():
    assert sc.tile_of(150.0, 2.0) == (1500, 920)
    assert sc.tile_of(-0.05, -90.0) == (3599, 0)


def test_tiles_around_wraps_through_ra_zero():
    tiles = sc.tiles_around(0.001, 0.05, 30)
    assert {i for i, _ in tiles} == {3599, 0}


def test_tiles_around_widen_in_ra_away_from_equator():
    assert len({i for i, _ in sc.tiles_around(150.05, 80.0, 180)}) > len({i for i, _ in sc.tiles_around(150.05, 0.0, 180)})


def test_tile_query():
    q = sc.tile_query(1500, 920)
    assert q.startswith("SELECT ra, dec, type,")
    assert "FROM ls_dr10.tractor WHERE ra >= 150.000000 AND ra < 150.100000" in q
    assert "dec >= 2.000000 AND dec < 2.100000 AND type != 'DUP'" in q


def test_fetch_tile_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    calls = []

    def get(url, params, timeout):
        calls.append(params["QUERY"])
        return SimpleNamespace(status_code=200, text="ra,dec,type\n150.01,2.01,PSF\n")

    first = sc.fetch_tile(1500, 920, get=get)
    again = sc.fetch_tile(1500, 920, get=lambda *a, **k: pytest.fail("cache miss"))
    assert len(calls) == 1 and first.equals(again) and len(again) == 1
    assert (tmp_path / "1500_920.csv.gz").exists()


@pytest.mark.parametrize("response", [
    SimpleNamespace(status_code=500, text="oops"),
    SimpleNamespace(status_code=200, text="<?xml version='1.0'?><VOTABLE>ERROR</VOTABLE>"),
])
def test_fetch_tile_rejects_error_answers(tmp_path, monkeypatch, response):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    with pytest.raises(sc.CatalogError):
        sc.fetch_tile(1, 1, get=lambda *a, **k: response)
    assert not list(tmp_path.iterdir())


def test_fetch_tile_network_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)

    def down(*a, **k):
        raise requests.ConnectionError("down")

    with pytest.raises(sc.CatalogError, match="tile 1,1: ConnectionError"):
        sc.fetch_tile(1, 1, get=down)


def test_neighbours_separation_and_position_angle():
    near = sc.neighbours(RA, DEC, 30, catalog(row(ddec_arcsec=-2.0), row(dra_arcsec=-3.0), row(dra_arcsec=40)))
    assert list(near["sep_arcsec"].round(3)) == [2.0, 3.0]
    # the target lies north of the first row and east of the second
    assert list(near["pa_deg"].round(1)) == [0.0, 90.0]


# --- directional light radius -------------------------------------------------------


def test_dlr_is_the_half_light_radius_on_the_major_axis_and_b_on_the_minor():
    e = 0.5  # b/a = 1/3
    assert sc.directional_light_radius(3.0, e, 0.0, 0.0) == pytest.approx(3.0)
    assert sc.directional_light_radius(3.0, e, 0.0, 90.0) == pytest.approx(1.0)
    assert sc.directional_light_radius(3.0, 0.0, 0.0, 37.0) == pytest.approx(3.0)  # round


def test_dlr_floor_for_tiny_galaxies():
    assert sc.directional_light_radius(0.0, 0.0, 0.0, 0.0) == pytest.approx(sc.MIN_HALF_LIGHT_ARCSEC)


# --- classify --------------------------------------------------------------------


def test_nothing_nearby_is_none():
    assert sc.classify(RA, DEC, catalog(FILLER)).verdict == "none"


def test_no_rows_at_all_is_unavailable_not_none():
    """Outside DR10 coverage an empty sky must not read as 'no host'."""
    assert sc.classify(RA, DEC, catalog()).verdict == "unavailable"


def test_catalog_error_is_unavailable():
    def broken(i, j):
        raise sc.CatalogError("tile 1,1: HTTP 500")

    xm = sc.classify(RA, DEC, broken)
    assert (xm.verdict, xm.evidence) == ("unavailable", ["tile 1,1: HTTP 500"])


def test_gaia_parallax_is_stellar():
    xm = sc.classify(RA, DEC, catalog(row(0.3, type_="PSF", shape_r=0, parallax=2.0, parallax_ivar=25.0)))
    assert (xm.verdict, xm.evidence) == ("stellar", ['Gaia parallax/err=10.0 at 0.3"'])


def test_gaia_proper_motion_is_stellar():
    xm = sc.classify(RA, DEC, catalog(row(0.2, type_="PSF", shape_r=0, pmra=10.0, pmra_ivar=1.0)))
    assert xm.verdict == "stellar" and "proper motion" in xm.evidence[0]


def test_star_beats_host():
    xm = sc.classify(RA, DEC, catalog(row(0.3, type_="PSF", shape_r=0, parallax=2.0, parallax_ivar=25.0),
                                      row(2.0, shape_r=3.0)))
    assert xm.verdict == "stellar"


def test_wise_agn_colour():
    agn = row(0.2, type_="PSF", shape_r=0, w1=10.0, w2=12.0, ivar_w1=10.0, ivar_w2=10.0)  # W1-W2 = 0.20 AB
    xm = sc.classify(RA, DEC, catalog(agn))
    assert xm.verdict == "agn" and "W1-W2=0.20 AB" in xm.evidence[0]


def test_wise_colour_needs_signal():
    faint = row(0.2, type_="PSF", shape_r=0, w1=0.1, w2=0.2, ivar_w1=10.0, ivar_w2=10.0)
    assert sc.classify(RA, DEC, catalog(faint)).verdict == "point_source"


def test_blue_wise_colour_is_not_agn():
    star_like = row(0.2, type_="EXP", shape_r=1.0, w1=12.0, w2=8.0, ivar_w1=10.0, ivar_w2=10.0)
    assert sc.classify(RA, DEC, catalog(star_like)).verdict == "host"


def test_host_by_directional_light_radius():
    """8 arcsec off a galaxy with a 3 arcsec half-light radius along the major axis: d_DLR 2.7 -> host."""
    gal = row(ddec_arcsec=-8.0, shape_r=3.0, e1=0.5, flux_r=10.0)  # major axis N-S, target due north of it
    xm = sc.classify(RA, DEC, catalog(gal))
    assert xm.verdict == "host"
    assert xm.evidence == ['EXP r=20.0 sep=8.0" d_DLR=2.7']


def test_same_offset_across_the_minor_axis_is_too_far():
    gal = row(dra_arcsec=-8.0, shape_r=3.0, e1=0.5)  # target due east: b = 1 arcsec, d_DLR 8
    assert sc.classify(RA, DEC, catalog(gal, FILLER)).verdict == "none"


def test_nearest_in_light_radii_wins_not_nearest_in_arcsec():
    small_close = row(ddec_arcsec=-2.0, shape_r=0.5, flux_r=1.0)  # d_DLR 4.0
    big_far = row(ddec_arcsec=-6.0, shape_r=4.0, flux_r=100.0)  # d_DLR 1.5
    xm = sc.classify(RA, DEC, catalog(small_close, big_far))
    assert "d_DLR=1.5" in xm.evidence[0]


def test_point_like_counterpart_without_gaia():
    xm = sc.classify(RA, DEC, catalog(row(0.4, type_="PSF", shape_r=0.0, flux_r=0.25)))
    assert (xm.verdict, xm.evidence) == ("point_source", ['PSF r=24.0 at 0.4", no Gaia astrometry'])


def test_galaxy_outranks_point_source():
    xm = sc.classify(RA, DEC, catalog(row(0.4, type_="PSF", shape_r=0.0), row(3.0, shape_r=2.0)))
    assert xm.verdict == "host"


def test_nmgy_to_mag():
    assert sc.nmgy_to_mag(1.0) == 22.5
    assert sc.nmgy_to_mag(0.0) is None
