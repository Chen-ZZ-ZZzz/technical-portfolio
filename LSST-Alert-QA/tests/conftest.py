"""Shared mock data fixtures."""

import pandas as pd
import pytest


@pytest.fixture
def ztf_dets():
    return pd.DataFrame({
        "ra":     [150.0, 150.1, 150.2],
        "dec":    [30.0,  30.1,  30.2],
        "magpsf": [18.5,  18.7,  19.0],
        "rb":     [0.90,  0.95,  0.92],
        "drb":    [0.85,  0.90,  0.88],
        "mjd":    [59000.0, 59010.0, 59020.0],
    })


@pytest.fixture
def ztf_ms():
    return pd.DataFrame({
        "fid":      [1, 2],
        "ndet":     [5, 3],
        "magmin":   [17.5, 17.8],
        "magmax":   [19.0, 19.2],
        "magmean":  [18.2, 18.4],
        "magsigma": [0.3,  0.2],
        "firstmjd": [59000.0, 59001.0],
        "lastmjd":  [59050.0, 59055.0],
    })


@pytest.fixture
def lsst_dets():
    return pd.DataFrame({
        "ra":          [150.0, 150.1],
        "dec":         [30.0,  30.1],
        "psfFlux":     [1000.0, 1200.0],   # nanojanskies
        "reliability": [0.90,   0.92],
        "mjd":         [60000.0, 60010.0],
    })


@pytest.fixture
def probs_clean():
    """Both classifiers agree on 'SN' with high confidence → no flag."""
    return pd.DataFrame({
        "classifier_name":    ["lc_classifier", "stamp_classifier"],
        "classifier_version": ["1.0.0",          "1.0.0"],
        "class_name":         ["SN",              "SN"],
        "probability":        [0.95,              0.90],
        "ranking":            [1,                 1],
    })


@pytest.fixture
def probs_majority():
    """Three stamp classifiers: two agree on SN, one outlier below threshold → minor flag.
    Stamp-only so weights are equal and lc doesn't push consensus above HIGH_CONFIDENCE_THRESHOLD."""
    return pd.DataFrame({
        "classifier_name":    ["stamp_classifier", "stamp_classifier_02", "stamp_classifier_03"],
        "classifier_version": ["1.0.0",             "1.0.0",               "1.0.0"],
        "class_name":         ["SN",                "SN",                  "AGN"],
        "probability":        [0.75,                0.70,                  0.20],
        "ranking":            [1,                   1,                     1],
    })


@pytest.fixture
def probs_split():
    """lc and stamp disagree with substantial probability → genuine split."""
    return pd.DataFrame({
        "classifier_name":    ["lc_classifier", "stamp_classifier"],
        "classifier_version": ["1.0.0",          "1.0.0"],
        "class_name":         ["SN",              "AGN"],
        "probability":        [0.60,              0.55],
        "ranking":            [1,                 1],
    })


@pytest.fixture
def data_complete(ztf_dets, ztf_ms, probs_clean):
    return {"dets": ztf_dets, "ms": ztf_ms, "probs": probs_clean, "fetch_errors": []}


@pytest.fixture
def data_empty():
    import pandas as pd
    empty = pd.DataFrame()
    return {"dets": empty, "ms": empty, "probs": empty, "fetch_errors": []}
