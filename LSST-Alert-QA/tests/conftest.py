"""Shared mock data fixtures."""

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def fresh_retry_budget():
    """
    The retry budget is module-global and shared by both clients, so a test whose
    retries drain it would silently change how later tests behave — and the
    existing client tests do exhaust retries. Reset around every test to keep the
    suite order-independent.
    """
    from rubin_qa import retry_budget

    retry_budget.reset()
    yield
    retry_budget.reset()


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


# Live voters of the classifier registry (classifier.VOTERS), one per family.
BHRF = ("lc_classifier_BHRF_forced_phot", "2.1.0")
ATAT = ("LC_classifier_ATAT_forced_phot(beta)", "1.0.0")
ZTF_STAMP = ("stamp_classifier", "stamp_classifier_1.0.4")
ZTF_STAMP_2025 = ("stamp_classifier_2025_beta", "2.1.1_beta")
LSST_STAMP = ("stamp_classifier_rubin_beta_20260421", "202")
CLASSES = ("SN", "AGN", "VS", "asteroid", "bogus", "satellite")


def class_rows(classifier: tuple[str, str], top: str, p: float, classes=CLASSES) -> list[dict]:
    """One classifier version's rows as ALeRCE returns them: one row per class it has, ranked,
    `top` at probability p and the rest below it. The class list is what decides whether two
    families can be compared (classifier.classify_object)."""
    name, version = classifier
    rest = [c for c in classes if c != top]
    share = (1 - p) / len(rest) if rest else 0.0
    ranked = [(top, p)] + [(c, share * (1 - 0.01 * i)) for i, c in enumerate(rest)]
    return [{"classifier_name": name, "classifier_version": version, "class_name": c,
             "probability": q, "ranking": rank} for rank, (c, q) in enumerate(ranked, 1)]


def probs_frame(*classifiers: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([row for rows in classifiers for row in rows])


# The consensus fixtures give every voter one class list so that they can be compared; ALeRCE's
# real live lists never match (README, "Independent Evidence in ALeRCE's Classifiers").
@pytest.fixture
def probs_clean():
    """Two comparable families agree on 'SN' with high confidence → no flag."""
    return probs_frame(class_rows(BHRF, "SN", 0.95), class_rows(ATAT, "SN", 0.90))


@pytest.fixture
def probs_majority():
    """Three comparable families: two agree on SN, one outlier below OUTLIER_PROB_THRESHOLD
    → minor flag (weighted consensus 0.82: above MAJORITY, below HIGH_CONFIDENCE)."""
    return probs_frame(class_rows(BHRF, "SN", 0.75), class_rows(ZTF_STAMP_2025, "SN", 0.75),
                       class_rows(ATAT, "AGN", 0.25))


@pytest.fixture
def probs_split():
    """Two comparable families disagree with substantial probability → genuine split."""
    return probs_frame(class_rows(BHRF, "SN", 0.60), class_rows(ATAT, "AGN", 0.55))


@pytest.fixture
def data_complete(ztf_dets, ztf_ms, probs_clean):
    return {"dets": ztf_dets, "ms": ztf_ms, "probs": probs_clean, "fetch_errors": []}


@pytest.fixture
def data_empty():
    import pandas as pd
    empty = pd.DataFrame()
    return {"dets": empty, "ms": empty, "probs": empty, "fetch_errors": []}


@pytest.fixture
def antares_dets():
    """Locus alerts after the ant_mag.notna() filter — upper limits already dropped."""
    return pd.DataFrame({
        "ra":       [153.5, 153.5, 153.5],
        "dec":      [-3.42, -3.42, -3.42],
        "ant_mag":  [18.9,  18.4,  18.1],
        "mjd":      [60000.0, 60005.0, 60012.0],
    })


@pytest.fixture
def antares_ms():
    """One-row magstats-shaped summary built from locus.properties."""
    return pd.DataFrame([{"ndet": 3, "magmin": 18.1, "magmax": 18.9}])


@pytest.fixture
def antares_data(antares_dets, antares_ms):
    """A complete locus with a single science tag → PASS."""
    return {
        "dets": antares_dets,
        "ms": antares_ms,
        "tags": ["nuclear_transient", "lc_feature_extractor"],
        "fetch_errors": [],
    }


@pytest.fixture
def antares_data_empty():
    empty = pd.DataFrame()
    return {"dets": empty, "ms": empty, "tags": [], "fetch_errors": []}
