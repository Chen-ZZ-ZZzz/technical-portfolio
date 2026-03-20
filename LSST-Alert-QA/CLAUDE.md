# LSST/ZTF Alert Data Quality Pipeline

A data quality pipeline for ZTF (and eventually LSST) alert data via the ALeRCE broker.

## Environment

- Python 3.13, venv at `.venv`
- Activate: `source .venv/bin/activate`
- Package managed with `uv`; install: `uv pip install -e .`
- Dev deps (pytest, pytest-mock): `uv pip install pytest pytest-mock`
- Key deps: `alerce`, `pandas`

## Structure

```
src/alerce_qa/
    config.py      — constants and thresholds
    client.py      — ALeRCE API wrapper (_api_call, fetch_candidates, fetch_object_data)
    validators.py  — validate_completeness
    classifier.py  — _version_score, _method_weight, classify_object
    reporting.py   — _psfflux_to_mag, build_qa_row, run_pipeline
    profiler.py    — object_profile (diagnostic deep-dive, ZTF only, uses query_lightcurve)
    __main__.py    — CLI entry point
pipeline.py        — thin shim: python pipeline.py [survey] [page_size | oid ...]
tests/             — pytest, mock data only (no live API calls)
pyproject.toml     — build: hatchling, script: alerce-qa
```

## Pipeline stages

`fetch_candidates()` → `fetch_object_data()` → `validate_completeness()` → `classify_object()` → `build_qa_row()` → `run_pipeline()`

## Key design decisions

- `fetch_candidates`: deduplicates oids (ALeRCE API returns duplicates); default page size 100
- `fetch_object_data`: 3 API calls per object (detections, magstats, probabilities), each with retry (2 attempts, 2s base delay)
- `validate_completeness`: confirmed = ndet > 1; no magnitude threshold filtering
- `classify_object`: weighted consensus — lc_classifier outweights stamp_classifier, gap widens with ndet; consensus ≥ 0.90 → clean, ≥ 0.65 + dissenters < 0.30 → minor flag, else genuine split

## QA report columns

`oid, ndet, mag_range, timespan_days, top_class, class_prob, consensus, n_classifiers, n_agree, n_disagree, confirmed, has_issues, completeness_issues, flag, status`

- `mag_range`: magmax − magmin (brightness amplitude)
- `timespan_days`: last − first detection epoch (from `mjd` or `jd`)
- `n_agree` / `n_disagree`: classifiers voting for/against the plurality class
- `status`: PASS (no flag) / REVIEW (genuine split) / FLAG (everything else)

## Survey support

LSST support is included with graceful degradation — the pipeline runs end-to-end but full QA is not yet possible. Full validation is pending survey ramp-up.

- `survey="ztf"` (default) — full support: magstats, classifiers, detections
- `survey="lsst"` — partial/degraded: `query_magstats` raises `NotImplementedError`; ndet/mag stats fall back to raw detections. `query_classifiers`/`query_classes` also not implemented yet. LSST oids are integers from the API, normalized to str in `fetch_candidates`.
- Run LSST: `python pipeline.py lsst`

## Known ALeRCE API quirks

- `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- API returns duplicate oids — handled in `fetch_candidates`
- LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
