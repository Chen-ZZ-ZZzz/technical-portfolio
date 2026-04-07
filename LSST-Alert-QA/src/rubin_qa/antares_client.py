"""ANTARES alert broker client — fetch loci and per-locus data."""

import warnings

import pandas as pd

from .config import DEFAULT_PAGE_SIZE


def _search():
    try:
        from antares_client import search
        return search
    except ImportError:
        raise ImportError("antares-client not installed. Run: pip install antares-client")


def fetch_antares_candidates(page_size: int = DEFAULT_PAGE_SIZE) -> list:
    """
    Fetch random locus IDs from ANTARES.
    Returns a list of locus_id strings. Empty list on error.
    """
    try:
        ids = _search().get_random_locus_ids(page_size)
        seen = set()
        deduped = []
        for id_ in ids:
            if id_ not in seen:
                seen.add(id_)
                deduped.append(id_)
        return deduped
    except Exception as e:
        warnings.warn(f"fetch_antares_candidates: {e}")
        return []


def fetch_antares_locus(locus_id: str) -> dict:
    """
    Fetch data for one ANTARES locus.
    Returns dict with keys: dets, ms, tags, fetch_errors.

      dets: DataFrame of alerts — columns include mjd, ra, dec plus alert properties.
      ms:   1-row DataFrame with ndet, magmin, magmax from locus.properties.
            magmin = brightest_alert_magnitude (lowest magnitude number = brightest).
            magmax = faintest_alert_magnitude.
            Compatible with build_qa_row's ms expectations (.sum() / .min() / .max()).
      tags: list of tag name strings (ANTARES discrete classification labels).
      fetch_errors: list of error tokens.
    """
    fetch_errors = []
    empty = pd.DataFrame()

    try:
        s = _search()
        if locus_id.startswith("ZTF"):
            locus = s.get_by_ztf_object_id(locus_id)
        else:
            locus = s.get_by_id(locus_id)
    except Exception as e:
        return {"dets": empty, "ms": empty, "tags": [], "fetch_errors": [f"locus:{e}"]}

    # Alerts → dets DataFrame
    dets = empty
    try:
        alerts = locus.alerts or []
        if alerts:
            records = []
            for a in alerts:
                rec = {"mjd": a.mjd, "ra": locus.ra, "dec": locus.dec}
                if a.properties:
                    rec.update(a.properties)
                records.append(rec)
            dets = pd.DataFrame(records)
            if "ant_mag" in dets.columns:
                dets = dets[dets["ant_mag"].notna()].reset_index(drop=True)
    except Exception as e:
        fetch_errors.append(f"alerts:{e}")

    # Locus properties → ms-like summary row
    ms = empty
    try:
        props = locus.properties or {}
        # num_mag_values is ANTARES's quality-filtered count (conservative).
        # len(dets) after ant_mag.notna() filter may be slightly higher (e.g. 59 vs 56)
        # because ANTARES applies additional cuts not reflected in the raw alert stream.
        # Prefer num_mag_values as the authoritative ndet for validators.
        n_mag = props.get("num_mag_values") or (len(dets) if not dets.empty else 0)
        ms = pd.DataFrame([{
            "ndet":   n_mag,
            "magmin": props.get("brightest_alert_magnitude"),
            "magmax": props.get("faintest_alert_magnitude"),
        }])
    except Exception as e:
        fetch_errors.append(f"properties:{e}")

    # Tags
    tags = []
    try:
        tags = list(locus.tags) if locus.tags else []
    except Exception as e:
        fetch_errors.append(f"tags:{e}")

    return {"dets": dets, "ms": ms, "tags": tags, "fetch_errors": fetch_errors}
