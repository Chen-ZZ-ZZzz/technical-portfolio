"""sky_catalog.py — Legacy Surveys DR10 tiles and local matching. No live TAP calls."""

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import requests

from tests.fake_tap import q3c_tap
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
    columns = [*sc.COLUMNS, *sc.ATLAS_COLUMNS]
    df = pd.DataFrame(list(rows), columns=columns)
    return lambda i, j: df if (i, j) == sc.tile_of(RA, DEC) else pd.DataFrame(columns=columns)


FILLER = row(dra_arcsec=25, type_="PSF", shape_r=0.0)  # something in the tile, far away


def atlas(*rows):
    """A footprint atlas for classify(atlas=...): the atlas galaxies given."""
    return pd.DataFrame(list(rows), columns=[*sc.COLUMNS, *sc.ATLAS_COLUMNS])


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
    assert q.startswith("SELECT t.ra, t.dec, t.type,")
    assert "FROM ls_dr10.tractor AS t LEFT JOIN sga2020.ellipse AS s ON t.ref_id = s.sga_id AND t.ref_cat = 'L3'" in q
    assert "s.sma_moment AS atlas_sma" in q
    assert "WHERE t.ra >= 150.000000 AND t.ra < 150.100000" in q
    assert "t.dec >= 2.000000 AND t.dec < 2.100000 AND t.type != 'DUP'" in q


TILE_HEADER = ",".join([*sc.COLUMNS, *sc.ATLAS_COLUMNS])
TILE_ROW = "150.01,2.01,PSF" + "," * (len(sc.COLUMNS) + len(sc.ATLAS_COLUMNS) - 3)


def test_fetch_tile_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    calls = []

    def get(url, params, timeout):
        calls.append(params["QUERY"])
        return SimpleNamespace(status_code=200, text=f"{TILE_HEADER}\n{TILE_ROW}\n")

    first = sc.fetch_tile(1500, 920, get=get)
    again = sc.fetch_tile(1500, 920, get=lambda *a, **k: pytest.fail("cache miss"))
    assert len(calls) == 1 and first.equals(again) and len(again) == 1
    assert (tmp_path / "1500_920.csv.gz").exists()


def test_fetch_tile_refetches_a_cache_from_before_the_atlas_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    pd.DataFrame({"ra": [150.01], "dec": [2.01], "type": ["PSF"]}).to_csv(tmp_path / "1500_920.csv.gz", index=False)
    fresh = sc.fetch_tile(1500, 920, get=lambda *a, **k: SimpleNamespace(status_code=200, text=f"{TILE_HEADER}\n{TILE_ROW}\n"))
    assert "atlas_sma" in fresh.columns
    assert "atlas_sma" in pd.read_csv(tmp_path / "1500_920.csv.gz").columns


def test_a_cache_file_missing_a_column_is_not_current(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    assert not sc.tile_is_cached(1, 1)
    pd.DataFrame({"ra": [1.0], "dec": [1.0]}).to_csv(tmp_path / "1_1.csv.gz", index=False)
    assert not sc.tile_is_cached(1, 1)
    pd.DataFrame(columns=[*sc.COLUMNS, *sc.ATLAS_COLUMNS]).to_csv(tmp_path / "1_1.csv.gz", index=False)
    assert sc.tile_is_cached(1, 1)


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
    assert not list(tmp_path.iterdir())


def test_a_response_cut_short_raises_and_is_not_cached(tmp_path, monkeypatch):
    """requests raises on a body cut off mid-transfer; that must reach the caller, never a partial tile."""
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)

    def cut(*a, **k):
        raise requests.exceptions.ChunkedEncodingError("connection broken mid-body")

    with pytest.raises(sc.CatalogError, match="ChunkedEncodingError"):
        sc.fetch_tile(1, 1, get=cut)
    assert not list(tmp_path.iterdir())


def test_an_empty_answer_is_returned_but_never_cached(tmp_path, monkeypatch):
    """A tile genuinely empty (inside a bright-star mask) costs a refetch; a glitch cannot become a permanent hole."""
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    calls = []

    def empty(url, params, timeout):
        calls.append(1)
        return SimpleNamespace(status_code=200, text=f"{TILE_HEADER}\n")

    assert sc.fetch_tile(1, 1, get=empty).empty and sc.fetch_tile(1, 1, get=empty).empty
    assert len(calls) == 2 and not list(tmp_path.iterdir())


def test_an_answer_at_maxrec_is_treated_as_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(sc, "TAP_MAXREC", 2)
    sent = {}

    def full(url, params, timeout):
        sent.update(params)
        return SimpleNamespace(status_code=200, text=f"{TILE_HEADER}\n{TILE_ROW}\n{TILE_ROW}\n")

    with pytest.raises(sc.CatalogError, match="at MAXREC: possibly truncated"):
        sc.fetch_tile(1, 1, get=full)
    assert sent["MAXREC"] == 2 and not list(tmp_path.iterdir())


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


def test_wise_agn_colour_on_the_point_source_itself_is_nuclear():
    agn = row(0.2, type_="PSF", shape_r=0, w1=10.0, w2=12.0, ivar_w1=10.0, ivar_w2=10.0)  # W1-W2 = 0.20 AB
    xm = sc.classify(RA, DEC, catalog(agn))
    assert (xm.verdict, xm.flags) == ("point_source", ["agn_nuclear"])
    assert xm.evidence[-1] == "AGN colours (W1-W2=0.20 AB) at the transient"


def test_wise_agn_colour_is_read_from_the_assigned_host_not_a_neighbour():
    """The old step read W1-W2 from whatever sat within 1", before any host was known."""
    agn_host = row(ddec_arcsec=-8.0, shape_r=3.0, e1=0.5, w1=10.0, w2=12.0, ivar_w1=10.0, ivar_w2=10.0)
    xm = sc.classify(RA, DEC, catalog(agn_host))
    assert (xm.verdict, xm.flags) == ("host", ["agn_host"]) and xm.evidence[-1] == "AGN colours (W1-W2=0.20 AB) of the host"
    # the old step: an AGN-coloured source 0.9" away that is not the host (d_DLR 4.5) decided "agn"
    agn_neighbour = row(0.9, type_="DEV", shape_r=0.2, w1=10.0, w2=12.0, ivar_w1=10.0, ivar_w2=10.0)
    host = row(ddec_arcsec=-8.0, shape_r=3.0, e1=0.5)
    xm = sc.classify(RA, DEC, catalog(agn_neighbour, host))
    assert (xm.verdict, xm.flags) == ("host", []) and "sep=8.0" in xm.evidence[0]


def test_wise_colour_needs_signal():
    """A red colour on noisy fluxes (S/N 0.3-0.6) is no measurement, never an AGN flag and never 'not AGN'."""
    faint = row(0.2, type_="PSF", shape_r=0, w1=0.1, w2=0.2, ivar_w1=10.0, ivar_w2=10.0)
    xm = sc.classify(RA, DEC, catalog(faint))
    assert (xm.verdict, xm.flags, xm.evidence[-1]) == ("point_source", [], "WISE colour: no measurement (S/N < 5 in W1 or W2)")


def test_wise_gate_needs_both_bands():
    one_band = row(0.2, type_="PSF", shape_r=0, w1=10.0, w2=12.0, ivar_w1=10.0, ivar_w2=0.01)  # W2 S/N 1.2
    assert sc.wise_colour(sc.with_separation(pd.DataFrame([one_band]), RA, DEC).iloc[0])[0] == "no_measurement"


@pytest.mark.parametrize("w2,expected", [(10.0 * 10 ** (0.15 / 2.5), "not_agn"), (10.0 * 10 ** (0.17 / 2.5), "agn")])
def test_agn_cut_is_0_8_vega_which_is_0_16_ab(w2, expected):
    """Vega->AB: W1 +2.699, W2 +3.339, so (W1-W2)_AB = (W1-W2)_Vega - 0.64; the cut sits at 0.16 AB, not 0.8."""
    assert sc.AGN_W1W2_AB_MIN == pytest.approx(0.8 + 2.699 - 3.339)
    src = row(0.2, type_="PSF", shape_r=0, w1=10.0, w2=w2, ivar_w1=10.0, ivar_w2=10.0)
    assert sc.wise_colour(src)[0] == expected


def test_blue_wise_colour_is_not_agn():
    star_like = row(0.2, type_="EXP", shape_r=1.0, w1=12.0, w2=8.0, ivar_w1=10.0, ivar_w2=10.0)
    xm = sc.classify(RA, DEC, catalog(star_like))
    assert xm.verdict == "host" and not {"agn_nuclear", "agn_host"} & set(xm.flags)


def test_a_point_source_within_1_arcsec_outranks_a_dlr_host():
    """A faint variable star beyond Gaia's reach, on a galaxy's outskirts, must not pass as SN-like."""
    xm = sc.classify(RA, DEC, catalog(row(0.6, type_="PSF", shape_r=0.0), row(ddec_arcsec=-8.0, shape_r=3.0, e1=0.5)))
    assert xm.verdict == "point_source"


def test_host_by_directional_light_radius():
    """8 arcsec off a galaxy with a 3 arcsec half-light radius along the major axis: d_DLR 2.7 -> host."""
    gal = row(ddec_arcsec=-8.0, shape_r=3.0, e1=0.5, flux_r=10.0)  # major axis N-S, target due north of it
    xm = sc.classify(RA, DEC, catalog(gal))
    assert xm.verdict == "host"
    assert xm.evidence == ['EXP r=20.0 sep=8.0" d_DLR=2.7', "WISE colour: no measurement (S/N < 5 in W1 or W2)"]


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
    assert (xm.verdict, xm.evidence) == ("point_source", ['PSF r=24.0 at 0.4", no Gaia astrometry', "WISE colour: no measurement (S/N < 5 in W1 or W2)"])


def test_no_compact_host_rule_a_point_source_beyond_1_arcsec_is_not_a_host():
    """Dropped 2026-10-01: 0 of 15 PSF-typed hosts gained, 10.4% of random positions given a host at 3"."""
    assert sc.classify(RA, DEC, catalog(row(2.0, type_="PSF", shape_r=0.0, flux_r=0.4), FILLER)).verdict == "none"


def test_within_1_arcsec_a_point_source_is_point_source():
    """Right on a point-like source: a variable star or a nuclear event; the Gaia check sorts those, not this label."""
    xm = sc.classify(RA, DEC, catalog(row(0.5, type_="PSF", shape_r=0.0), row(2.0, type_="PSF", shape_r=0.0)))
    assert xm.verdict == "point_source"


# --- ellipse conventions, anchored to the image ---------------------------------------
# Expected major-axis PAs come from the pixels, never from a catalogue (2026-10-01):
# measured with tools/pa_from_pixels.py (r-band FITS cutout, second moments, orientation
# from the WCS), reproduce with `python tools/pa_from_pixels.py --radius R RA DEC`, R =
# shape_r (Tractor rows) or sma_moment (atlas rows). Each was also confirmed by eye on the
# Legacy viewer cutout against neutral 45/135 deg guides. Convention errors are gross (a
# 90 deg swap or an east-west mirror both move a diagonal by ~90 deg), so the tolerance is
# wide; all fixtures are strongly elongated and on a diagonal.
# If these fail after a change that follows the DR10 docs (PA = 180 - phi), the change is
# wrong, not the fixtures: the documented formula is the mirror image of the sky.
EYE_TOLERANCE_DEG = 20

TRACTOR_FIXTURES = [  # ra, dec, shape_r, shape_e1, shape_e2, major-axis PA from the pixels
    (150.21580, +3.22794, 3.7561, +0.09881, -0.63301, 141.8),
    (51.16068, -25.97190, 3.9609, +0.22416, +0.61862, 34.9),
    (50.67062, -29.28958, 4.2439, -0.02598, -0.52705, 132.2),
    (151.45504, +2.44826, 3.3988, +0.15313, -0.50570, 142.9),
    (52.89671, -27.22079, 3.3473, +0.23447, +0.59090, 26.5),
]
ATLAS_FIXTURES = [  # ra, dec, sga_id, shape_r, e1, e2, atlas pa, ba, sma_moment, PA from the pixels
    (55.12245, -26.86222, 263760, 16.0829, -0.10924, -0.44459, 128.81, 0.4454, 62.320, 130.0),
    (35.44683, -10.02366, 1430535, 8.3399, +0.01934, +0.46189, 45.60, 0.4479, 42.768, 45.6),
    (52.82959, -26.56198, 144583, 14.2914, +0.03302, +0.51009, 42.39, 0.3220, 46.954, 55.8),
]


def axis_diff(a, b):
    return abs((a - b + 90) % 180 - 90)


def atlas_row(dra_arcsec=0.0, ddec_arcsec=0.0, sga_id=1430535, shape_r=8.3399, e1=0.01934, e2=0.46189,
              pa=45.60, ba=0.4479, sma=42.768, **kw):
    return {**row(dra_arcsec, ddec_arcsec, type_="SER", shape_r=shape_r, e1=e1, e2=e2, **kw),
            "ref_cat": "L3", "ref_id": sga_id, "atlas_pa": pa, "atlas_ba": ba, "atlas_sma": sma}


def offset(pa_deg, sep_arcsec):
    """(dra, ddec) arcsec of a point sep_arcsec from the origin at position angle pa_deg."""
    return sep_arcsec * math.sin(math.radians(pa_deg)), sep_arcsec * math.cos(math.radians(pa_deg))


@pytest.mark.parametrize("ra,dec,shape_r,e1,e2,eye", TRACTOR_FIXTURES)
def test_tractor_major_axis_matches_the_image(ra, dec, shape_r, e1, e2, eye):
    _, _, pa = sc.tractor_ellipse(shape_r, e1, e2)
    assert axis_diff(pa, eye) <= EYE_TOLERANCE_DEG
    # the light radius is longest along the axis the image shows
    assert sc.directional_light_radius(shape_r, e1, e2, eye) > 2 * sc.directional_light_radius(shape_r, e1, e2, eye + 90)


@pytest.mark.parametrize("ra,dec,sga_id,shape_r,e1,e2,pa,ba,sma,eye", ATLAS_FIXTURES)
def test_atlas_major_axis_matches_the_image(ra, dec, sga_id, shape_r, e1, e2, pa, ba, sma, eye):
    a, q, major = sc.host_ellipse(pd.Series(atlas_row(0, 0, sga_id, shape_r, e1, e2, pa, ba, sma)))
    assert axis_diff(major, eye) <= EYE_TOLERANCE_DEG
    assert (a, q) == (sma, ba)  # the atlas ellipse, not the Tractor shape


def test_atlas_copied_pa_falls_back_to_the_tractor_shape():
    """sma_moment -1: the atlas could not measure the ellipse and filled pa from HyperLeda."""
    copied = pd.Series(atlas_row(pa=0.0, ba=0.9, sma=-1.0))
    assert sc.host_ellipse(copied) == pytest.approx(sc.tractor_ellipse(8.3399, 0.01934, 0.46189))


def test_atlas_chain_piece_excluded_atlas_ellipse_used_atlas_wins():
    """
    SN 28" off atlas galaxy 1430535 across its major axis (fixture geometry), with a DR10
    piece of the galaxy 0.5" from the SN. Unhandled, the piece wins (d_DLR 0.5); on the
    Tractor shape the galaxy is ~9 radii away. Handled: the galaxy, d_DLR ~1.5.
    """
    gal_dra, gal_ddec = offset(45 + 90, -28.0)  # galaxy sits 28" from the SN at PA 315
    galaxy = atlas_row(gal_dra, gal_ddec)
    piece = {**row(0.5, 0.0, type_="REX", shape_r=1.0), "ref_cat": "L3", "ref_id": -1}
    xm = sc.classify(RA, DEC, catalog(galaxy, piece))
    assert xm.verdict == "host"
    assert "SGA 1430535" in xm.evidence[0] and "d_DLR=1.5" in xm.evidence[0]


# --- two-step host candidates ------------------------------------------------------------
NGC1398_LIKE = dict(sga_id=7, shape_r=60.0, e1=0.0, e2=0.0, pa=98.6, ba=0.79, sma=216.0)


def test_step_2_finds_an_atlas_galaxy_far_beyond_the_search_radius():
    """SN 2025zi's geometry: 123" from the centre of an NGC 1398-sized galaxy, outside step 1's 30"."""
    galaxy = atlas_row(*offset(98.6, -123.0), **NGC1398_LIKE)  # the SN sits on the major axis
    xm = sc.classify(RA, DEC, catalog(FILLER), atlas=atlas(galaxy))
    assert xm.verdict == "host"
    assert "SGA 7" in xm.evidence[0] and "sep=123.0" in xm.evidence[0] and "d_DLR=0.6" in xm.evidence[0]


def test_step_2_skips_an_atlas_galaxy_whose_scaled_ellipse_falls_short():
    small = atlas_row(*offset(0, -150.0), sga_id=8, sma=30.0, ba=0.5, pa=0.0)  # 4 x 30" = 120" < 150"
    assert sc.classify(RA, DEC, catalog(FILLER), atlas=atlas(small)).verdict == "none"
    assert sc.reached_atlas(sc.with_separation(pd.DataFrame([small]), RA, DEC)).empty


def test_an_atlas_galaxy_found_by_both_steps_counts_once():
    gal_dra, gal_ddec = offset(45 + 90, -28.0)
    galaxy = atlas_row(gal_dra, gal_ddec)
    xm = sc.classify(RA, DEC, catalog(galaxy), atlas=atlas(galaxy))
    assert xm.verdict == "host" and "SGA 1430535" in xm.evidence[0] and "d_DLR=1.5" in xm.evidence[0]


def test_without_an_atlas_only_step_1_runs():
    far = atlas_row(*offset(98.6, -123.0), **NGC1398_LIKE)
    assert sc.classify(RA, DEC, catalog(FILLER, far)).verdict == "none"  # 123" is beyond step 1
    xm = sc.classify(RA, DEC, catalog(row(ddec_arcsec=-8.0, shape_r=3.0, e1=0.5)))
    assert xm.verdict == "host" and xm.evidence == ['EXP r=22.5 sep=8.0" d_DLR=2.7', "WISE colour: no measurement (S/N < 5 in W1 or W2)"]


def test_atlas_near_keeps_the_margin():
    near = atlas_row(*offset(0, -0.9 * 3600), sga_id=1)
    beyond = atlas_row(*offset(0, -1.1 * 3600), sga_id=2)
    assert list(sc.atlas_near(atlas(near, beyond), RA, DEC)["ref_id"]) == [1]


def test_footprint_atlas_query_is_one_cone_with_the_margin_and_only_atlas_galaxies():
    q = sc.footprint_atlas_query((150.1, 2.5, 2.82))
    assert "'t' = q3c_radial_query(t.ra, t.dec, 150.100000, 2.500000, 3.820000)" in q
    assert "t.ref_cat = 'L3' AND t.ref_id > 0" in q and "LEFT JOIN sga2020.ellipse" in q


def test_caches_are_keyed_on_the_catalogue_versions(tmp_path, monkeypatch):
    assert sc.CATALOG_VERSION == "ls_dr10+sga2020"
    assert sc.CACHE_DIR.parent.name == sc.ATLAS_CACHE_DIR.parent.name == "ls_dr10+sga2020"
    monkeypatch.setattr(sc, "ATLAS_CACHE_DIR", tmp_path)
    calls = []

    def get(url, params, timeout):
        calls.append(timeout)
        return SimpleNamespace(status_code=200, text=f"{TILE_HEADER}\n{TILE_ROW}\n")

    sc.footprint_atlas((150.1, 2.5, 2.82), get=get)
    sc.footprint_atlas((150.1, 2.5, 2.82), get=lambda *a, **k: pytest.fail("cache miss"))
    assert calls == [sc.ATLAS_TAP_TIMEOUT]
    assert (tmp_path / "cone_150.1_2.5_2.82_margin_1.csv.gz").exists()


# The two TNS supernovae whose reported host lies beyond step 1's 30" (2026-10-01).
# Host rows as DR10 (ls_dr10.tractor, REF_CAT L3) and the atlas (sga2020.ellipse) give them.
TNS_BIG_HOSTS = [  # name, SN ra, dec, host, separation arcsec
    ("2025zi", 54.727480, -26.370617, dict(name="NGC 1398", ra=54.716993, dec=-26.337636, sga_id=315734, shape_r=62.1727,
                                          e1=-0.01116, e2=+0.00851, pa=98.56, ba=0.7866, sma=215.864), 123.5),
    ("2023cr", 55.554490, -27.871239, dict(name="ESO 419-003", ra=55.546473, dec=-27.864341, sga_id=1255822, shape_r=16.9856,
                                          e1=-0.03133, e2=-0.16787, pa=128.10, ba=0.6926, sma=47.760), 35.6),
]


def at(ra, dec, **fields):
    """A catalogue row at an absolute position."""
    return {**row(), "ra": ra, "dec": dec, **fields}


def atlas_galaxy_at(g):
    return at(g["ra"], g["dec"], type="SER", shape_r=g["shape_r"], shape_e1=g["e1"], shape_e2=g["e2"],
              ref_cat="L3", ref_id=g["sga_id"], atlas_pa=g["pa"], atlas_ba=g["ba"], atlas_sma=g["sma"])


def sky_around(ra, dec, *rows):
    """A tile fetch holding rows in the tile of (ra, dec), plus a far PSF so the tile is not empty."""
    filler = at(ra + 25 / 3600 / math.cos(math.radians(dec)), dec, type="PSF", shape_r=0.0)
    columns = [*sc.COLUMNS, *sc.ATLAS_COLUMNS]
    df = pd.DataFrame([filler, *rows], columns=columns)
    return lambda i, j: df if (i, j) == sc.tile_of(ra, dec) else pd.DataFrame(columns=columns)


@pytest.mark.parametrize("name,ra,dec,host,sep", TNS_BIG_HOSTS)
def test_tns_supernovae_beyond_step_1_find_their_host_in_step_2(name, ra, dec, host, sep):
    before = sc.classify(ra, dec, sky_around(ra, dec))  # step 1 alone: the old false orphan
    assert before.verdict == "none"
    xm = sc.classify(ra, dec, sky_around(ra, dec), atlas=atlas(atlas_galaxy_at(host)))
    assert xm.verdict == "host"
    assert f"SGA {host['sga_id']}" in xm.evidence[0] and f'sep={sep:.1f}"' in xm.evidence[0]


def test_the_margin_fetches_an_atlas_galaxy_just_outside_the_footprint(tmp_path, monkeypatch):
    """
    The case the margin exists for: an SN 0.02 deg inside the footprint edge whose host's
    centre lies 0.04 deg outside it. The fake TAP service applies the query's own q3c
    cone, so this runs footprint_atlas's real query, its margin and classify end to end.
    """
    cone = (150.1, 2.5, 2.82)
    sn_ra, sn_dec = 150.1, 2.5 + 2.80
    host = dict(ra=150.1, dec=2.5 + 2.86, sga_id=9, shape_r=30.0, e1=0.0, e2=0.0, pa=0.0, ba=0.8, sma=100.0)
    sky = pd.DataFrame([atlas_galaxy_at(host)], columns=[*sc.COLUMNS, *sc.ATLAS_COLUMNS])
    tap = q3c_tap(sky)  # its reading of q3c is checked against Data Lab by tools/atlas_query_contract.py

    monkeypatch.setattr(sc, "ATLAS_CACHE_DIR", tmp_path / "margin")
    footprint = sc.footprint_atlas(cone, get=tap)
    assert list(footprint["ref_id"]) == [9]  # fetched although its centre is outside the cone
    xm = sc.classify(sn_ra, sn_dec, sky_around(sn_ra, sn_dec), atlas=footprint)
    assert xm.verdict == "host" and "SGA 9" in xm.evidence[0] and 'sep=216.0"' in xm.evidence[0]

    monkeypatch.setattr(sc, "ATLAS_MARGIN_DEG", 0.0)  # without the margin it is never fetched
    monkeypatch.setattr(sc, "ATLAS_CACHE_DIR", tmp_path / "no_margin")
    assert sc.footprint_atlas(cone, get=tap).empty
    assert sc.classify(sn_ra, sn_dec, sky_around(sn_ra, sn_dec), atlas=sc.footprint_atlas(cone, get=tap)).verdict == "none"


# NGC 873 as DR10 has it (brick 0342m112): the atlas galaxy, and its nucleus fitted as a
# separate point source 1.3" from the model centre - REF_CAT empty, only maskbit 12 says it
# lies inside the atlas ellipse. Values from ls_dr10.tractor and sga2020.ellipse, 2026-10-01.
NGC873 = dict(ra=34.134775, dec=-11.348796, sga_id=1049583, shape_r=16.556896, e1=0.026569, e2=-0.090132,
              pa=144.95497, ba=0.769505, sma=53.137524)
NGC873_NUCLEUS = (34.13489477, -11.34845317)
INSIDE_ATLAS = 1 << sc.GALAXY_MASKBIT


def ngc873_sky(sn_ra, sn_dec, *extra):
    galaxy = {**atlas_galaxy_at(NGC873), "flux_r": 13213.9, "maskbits": INSIDE_ATLAS}
    nucleus = at(*NGC873_NUCLEUS, type="PSF", shape_r=0.0, flux_r=269.4, ref_cat="", ref_id=0, maskbits=INSIDE_ATLAS)
    return sky_around(sn_ra, sn_dec, galaxy, nucleus, *extra), atlas(galaxy)


def test_an_sn_on_ngc873s_nucleus_is_host_ngc873_nuclear_not_a_point_source():
    """Pieces belong to their atlas galaxy in every rule, the point-source rules included."""
    sn_ra, sn_dec = NGC873_NUCLEUS[0] + 0.18 / 3600 / math.cos(math.radians(NGC873_NUCLEUS[1])), NGC873_NUCLEUS[1]
    fetch, atl = ngc873_sky(sn_ra, sn_dec)
    xm = sc.classify(sn_ra, sn_dec, fetch, atlas=atl)
    assert xm.verdict == "host" and "SGA 1049583" in xm.evidence[0]
    assert "nuclear" in xm.flags  # 1.3" from the model centre, inside the atlas nuclear radius


def test_an_sn_on_a_disk_knot_of_ngc873_is_host_ngc873_not_nuclear():
    knot = (NGC873["ra"] + 20 / 3600 / math.cos(math.radians(NGC873["dec"])), NGC873["dec"])
    fetch, atl = ngc873_sky(*knot, at(*knot, type="PSF", shape_r=0.0, flux_r=5.0, ref_cat="", ref_id=0,
                                      maskbits=INSIDE_ATLAS))
    xm = sc.classify(*knot, fetch, atlas=atl)
    assert xm.verdict == "host" and "SGA 1049583" in xm.evidence[0] and "nuclear" not in xm.flags


def test_maskbit_12_marks_a_piece_but_never_the_atlas_galaxy_itself():
    rows = pd.DataFrame([{**atlas_galaxy_at(NGC873), "maskbits": INSIDE_ATLAS},
                         at(*NGC873_NUCLEUS, type="PSF", ref_cat="", ref_id=0, maskbits=INSIDE_ATLAS),
                         at(34.13, -11.35, type="EXP", ref_cat="L3", ref_id=-1, maskbits=0),
                         at(34.20, -11.30, type="PSF", ref_cat="", ref_id=0, maskbits=0)])
    assert list(sc.pieces(rows)) == [False, True, True, False]


def test_atlas_radius_choice_is_explicit():
    gal_dra, gal_ddec = offset(45 + 90, -28.0)
    rows = sc.with_separation(pd.DataFrame([atlas_row(gal_dra, gal_ddec)]), RA, DEC)
    assert sc.best_host(rows, atlas_radius="sma_moment") is not None
    assert sc.best_host(rows, atlas_radius="shape_r") is None  # b = 0.45 * 8.3" = 3.7": 7.5 radii


# --- catalogue gaps (DR10 BAILOUT) -------------------------------------------------------


def maskbits_world(bailout_box_px=(90, 110, 90, 110)):
    """A 200x200 maskbits image at 0.262"/px centred on (RA, DEC), BAILOUT set in a pixel box; and its bricks."""
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [RA, DEC]
    w.wcs.crpix = [100.5, 100.5]  # FITS 1-based: the image centre
    w.wcs.cd = [[-0.262 / 3600, 0], [0, 0.262 / 3600]]
    img = np.zeros((200, 200), dtype=np.int16)
    x0, x1, y0, y1 = bailout_box_px
    img[y0:y1, x0:x1] = 1 << sc.BAILOUT_BIT
    half = 100 * 0.262 / 3600
    bricks = pd.DataFrame({"brickname": ["test"], "ra1": [RA - half / math.cos(math.radians(DEC))],
                           "ra2": [RA + half / math.cos(math.radians(DEC))], "dec1": [DEC - half], "dec2": [DEC + half]})
    return bricks, (lambda brick: (img, w))


def test_bailout_at_the_position_is_a_catalogue_gap():
    bricks, fetch = maskbits_world()
    assert "BAILOUT" in sc.catalogue_gap(RA, DEC, bricks, fetch)


def test_bailout_anywhere_in_the_step_1_search_area_counts_but_farther_does_not():
    """A host whose centre sits in a gap is lost to step 1 even when the SN is outside it."""
    bricks, fetch = maskbits_world((10, 23, 95, 105))  # ~20" east of the target: inside 30"
    assert sc.catalogue_gap(RA, DEC, bricks, fetch) is not None
    bricks, fetch = maskbits_world((0, 3, 95, 105))  # ~25.5" away: still inside
    assert sc.catalogue_gap(RA, DEC, bricks, fetch) is not None
    bricks, fetch = maskbits_world((95, 105, 0, 3))  # ~25" south
    assert sc.catalogue_gap(RA, DEC, bricks, fetch) is not None
    bricks, fetch = maskbits_world((95, 105, 0, 1))  # ~26" south: a gap for the 30" area, not for a 20" one
    assert sc.catalogue_gap(RA, DEC, bricks, fetch, radius_arcsec=20.0) is None


def test_the_search_area_is_read_from_every_brick_it_overlaps():
    """Target near a brick edge: the BAILOUT lies in the neighbour brick's image, within 30"."""
    from astropy.wcs import WCS

    def world(centre_ra, box):
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [centre_ra, DEC]
        w.wcs.crpix = [100.5, 100.5]
        w.wcs.cd = [[-0.262 / 3600, 0], [0, 0.262 / 3600]]
        img = np.zeros((200, 200), dtype=np.int16)
        if box:
            x0, x1, y0, y1 = box
            img[y0:y1, x0:x1] = 1 << sc.BAILOUT_BIT
        return img, w

    half = 100 * 0.262 / 3600 / math.cos(math.radians(DEC))  # brick half-width in RA
    edge = RA + half
    bricks = pd.DataFrame({"brickname": ["home", "east"], "ra1": [RA - half, edge], "ra2": [edge, edge + 2 * half],
                           "dec1": [DEC - 0.01] * 2, "dec2": [DEC + 0.01] * 2})
    images = {"home": world(RA, None), "east": world(edge + half, (175, 200, 95, 105))}  # BAILOUT at its west edge
    target = edge - 5 / 3600 / math.cos(math.radians(DEC))  # 5" inside home, BAILOUT ~20" east in "east"
    assert "brick east" in sc.catalogue_gap(target, DEC, bricks, images.get)
    images["east"] = None  # the portal has no such brick: part of the search area is uncovered
    assert "no DR10 brick for part" in sc.catalogue_gap(target, DEC, bricks, images.get)


def test_no_brick_is_no_coverage_not_open_sky():
    bricks, fetch = maskbits_world()
    assert sc.catalogue_gap(RA + 1, DEC, bricks, fetch) == "no DR10 brick here"
    assert sc.catalogue_gap(RA, DEC, bricks, lambda b: None).startswith("no DR10 brick")  # the portal said 404


def test_a_gap_check_that_fails_raises():
    bricks, _ = maskbits_world()

    def down(brick):
        raise sc.CatalogError("maskbits test: ReadTimeout")

    with pytest.raises(sc.CatalogError):
        sc.catalogue_gap(RA, DEC, bricks, down)


def test_classify_turns_a_gap_into_unavailable_never_none():
    xm = sc.classify(RA, DEC, catalog(FILLER), coverage=lambda ra, dec: "DR10 catalogue gap: BAILOUT")
    assert (xm.verdict, xm.evidence) == ("unavailable", ["DR10 catalogue gap: BAILOUT"])

    def down(ra, dec):
        raise sc.CatalogError("maskbits test: HTTP 502")

    assert sc.classify(RA, DEC, catalog(FILLER), coverage=down).verdict == "unavailable"


@pytest.mark.parametrize("status,body", [(500, b"oops"), (200, b"<html>502 Bad Gateway</html>")])
def test_fetch_maskbits_rejects_and_never_caches_a_bad_answer(tmp_path, monkeypatch, status, body):
    monkeypatch.setattr(sc, "MASKBITS_CACHE_DIR", tmp_path)
    with pytest.raises(sc.CatalogError):
        sc.fetch_maskbits("0532m280", get=lambda url, timeout: SimpleNamespace(status_code=status, content=body))
    assert not list(tmp_path.iterdir())


def test_fetch_maskbits_404_is_no_brick_and_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MASKBITS_CACHE_DIR", tmp_path)
    assert sc.fetch_maskbits("9999p999", get=lambda url, timeout: SimpleNamespace(status_code=404, content=b"")) is None
    assert not list(tmp_path.iterdir())


def test_fetch_maskbits_caches_a_fits_file(tmp_path, monkeypatch):
    import io as _io

    from astropy.io import fits

    monkeypatch.setattr(sc, "MASKBITS_CACHE_DIR", tmp_path)
    buf = _io.BytesIO()
    fits.PrimaryHDU(np.full((4, 4), 1 << sc.BAILOUT_BIT, dtype=np.int16)).writeto(buf)
    urls = []
    image, _ = sc.fetch_maskbits("0532m280", get=lambda url, timeout: urls.append(url) or SimpleNamespace(
        status_code=200, content=buf.getvalue()))
    assert image[0, 0] == 1 << sc.BAILOUT_BIT and (tmp_path / "0532m280.fits.fz").exists()
    assert urls == ["https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr10/south/coadd/053/0532m280/"
                    "legacysurvey-0532m280-maskbits.fits.fz"]


def test_footprint_bricks_query_covers_the_cone_and_margin():
    q = sc.footprint_bricks_query((53.1, -27.8, 2.82))
    assert q.startswith("SELECT brickname, ra1, ra2, dec1, dec2 FROM ls_dr10.bricks WHERE dec2 > -31.620000 AND dec1 < -23.980000")


def test_nmgy_to_mag():
    assert sc.nmgy_to_mag(1.0) == 22.5
    assert sc.nmgy_to_mag(0.0) is None
