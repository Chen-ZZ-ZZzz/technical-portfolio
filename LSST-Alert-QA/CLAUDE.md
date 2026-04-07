# LSST/ZTF/ANTARES Alert Data Quality Pipeline

A data quality pipeline for ZTF (and eventually LSST) alert data via the ALeRCE and ANTARES brokers.

## Environment

- Python 3.13, venv at `.venv`
- Activate: `source .venv/bin/activate`
- Package managed with `uv`; install: `uv pip install -e .`
- Dev deps (pytest, pytest-mock): `uv pip install pytest pytest-mock`
- Key deps: `alerce`, `antares-client`, `pandas`

## Structure

```
src/rubin_qa/
    config.py          — constants and thresholds
    client.py          — ALeRCE API wrapper (_api_call, fetch_candidates, fetch_object_data)
    antares_client.py  — ANTARES API wrapper (fetch_antares_candidates, fetch_antares_locus)
    validators.py      — validate_completeness, validate_antares
    classifier.py      — _version_score, _method_weight, classify_object, classify_antares
    reporting.py       — build_qa_row, run_pipeline, build_antares_qa_row, run_antares_pipeline
    profiler.py        — object_profile (diagnostic deep-dive, ZTF only, uses query_lightcurve)
    __main__.py        — CLI entry point
pipeline.py            — thin shim: python pipeline.py [survey] [page_size | oid ...]
tests/                 — pytest, mock data only (no live API calls)
pyproject.toml         — build: hatchling, script: rubin-qa
```

## Pipeline stages

**ALeRCE (ztf/lsst):** `fetch_candidates()` → `fetch_object_data()` → `validate_completeness()` → `classify_object()` → `build_qa_row()` → `run_pipeline()`

**ANTARES:** `fetch_antares_candidates()` → `fetch_antares_locus()` → `validate_antares()` → `classify_antares()` → `build_antares_qa_row()` → `run_antares_pipeline()`

## Key design decisions

- `fetch_candidates`: deduplicates oids (ALeRCE API returns duplicates); default page size 100
- `fetch_object_data`: 3 API calls per object (detections, magstats, probabilities), each with retry (2 attempts, 2s base delay)
- `validate_completeness`: confirmed = ndet > 1; no magnitude threshold filtering
- `classify_object`: weighted consensus — lc_classifier outweights stamp_classifier, gap widens with ndet; consensus ≥ 0.90 → clean, ≥ 0.65 + dissenters < 0.30 → minor flag, else genuine split
- `build_qa_row` status tiers (in priority order): FLAG if completeness issues or n_classifiers < 2 ("insufficient_classifiers"); PASS if consensus ≥ 0.90; REVIEW_MINOR if consensus ≥ 0.65 + outlier dissenters; REVIEW_MAJOR otherwise
- REVIEW_MINOR is currently dormant for ZTF: `lc_classifier` returns empty for most objects, leaving only stamp_classifier voting → n_classifiers=1 → FLAG (insufficient_classifiers). Will activate when lc_classifier data flows.

### ANTARES-specific design

- `fetch_antares_candidates`: deduplicates locus IDs (ANTARES API returns duplicates, same issue as ALeRCE)
- `fetch_antares_locus`: accepts ANTARES locus IDs (`ANT...`) or ZTF object IDs (`ZTF...`) — routes to `get_by_id` or `get_by_ztf_object_id` accordingly. Builds `ms` from locus.properties (num_mag_values, brightest/faintest_alert_magnitude). Filters `locus.alerts` to `ant_mag.notna()` rows only — upper limits (`ztf_upper_limit`) have no `ant_mag` and are excluded from the lightcurve.
- `ndet` uses `num_mag_values` from locus.properties (ANTARES quality-filtered count, conservative). `len(dets)` after filter may be slightly higher — ANTARES applies additional cuts not reflected in the raw alert stream.
- `classify_antares`: filters raw tags to `SCIENCE_TAGS` before computing consensus; pipeline/filter tags are stripped. `top_class` = all science tags sorted and joined (e.g. `"dimmers, extragalactic"`). consensus = 1/n_science_tags; single science tag → PASS; multi → REVIEW_MINOR/MAJOR; no science tags → FLAG with `no_science_tags` flag listing which pipeline/unknown tags were present.
- `build_antares_qa_row`: same output schema as `build_qa_row`; no `n_classifiers < 2` threshold (ANTARES uses tags, not multi-classifier votes). In ANTARES context, `n_classifiers` = number of *science* tags.
- `antares-client` must be installed; import is deferred (lazy) so ALeRCE-only users are not affected

### ANTARES tag taxonomy (classifier.py, updated 2026-04)

`SCIENCE_TAGS` (19) — astrophysical classifications, count toward consensus:
`dimmers`, `extragalactic`, `young_extragalactic_candidate`, `nuclear_transient`, `high_amplitude_variable_star_candidate`, `high_amplitude_transient_candidate`, `blue_transient`, `ECL_blue_transient_candidate`, `dwarf_nova_outburst`, `high_flux_ratio_wrt_nn`, `recent_reddening`, `sso_candidates`, `sso_confirmed`, `imhb_candidate`, `SN_candies`, `soraisam_sublum`, `young_rubin_transients`, `nova_test`, `in_m31`

`PIPELINE_TAGS` (19) — processing/filter/footprint/infrastructure, excluded from classification:
`lc_feature_extractor`, `refitt_newsources_snrcut`, `siena_mag_coord_cut`, `iso_forest_anomaly_detection`, `LAISS_RFC_AD_filter`, `desoto_classified`, `superphot_plus_classified`, `matheson_extreme_vpdf`, `in_LSSTDDF`, `in_shadow_virgo`, `NUTTelA_TAO`, `lsst_scimma_quality_transient`, `desi_target`, `high_snr`, `anomaly_transient_dmdt_visited_v1`, `random_tagger_filter`, `test_dev_all`, `test_dev_lsst`, `test_dev_ztf`

Notes:
- Current tag set reflects ZTF filter implementations only — LSST-equivalent science tags not yet deployed.
- All tags are filter outputs, not confirmed classifications. Treat every tag as a candidate until followed up.
- `high_snr`: excluded from classification; useful in validators — a high-SNR object still failing other checks is more suspicious since measurement quality cannot be blamed.
- `desi_target`: catalog crossmatch, weak extragalactic hint; excluded from classification.
- Unknown tags (not in either set): flagged explicitly in `no_science_tags` output so new ANTARES tags don't silently affect classification.
- Full per-tag annotations (official descriptions, filter logic) are in `classifier.py`.

## QA report columns

`oid, ndet, mag_range, timespan_days, top_class, class_prob, consensus, n_classifiers, n_agree, n_disagree, confirmed, has_issues, completeness_issues, flag, status`

- `mag_range`: magmax − magmin (brightness amplitude)
- `timespan_days`: last − first detection epoch (from `mjd` or `jd`)
- `top_class`: ALeRCE: plurality class from weighted consensus; ANTARES: all science tags sorted and joined
- `n_agree` / `n_disagree`: classifiers voting for/against the plurality class
- `n_classifiers`: ALeRCE: number of classifiers that returned results; ANTARES: number of science tags
- `status`: PASS / REVIEW_MINOR / REVIEW_MAJOR / FLAG — see tier rules below

## CLI summary (lean) output columns

`oid, ndet, top_class, consensus, n_classifiers, status`

The terminal printout (`SUMMARY_COLUMNS` in `__main__.py`) shows these six columns. Full details including mag stats and flag strings are in the CSV.

## Survey / broker support

- `survey="ztf"` (default) — ALeRCE, full support: magstats, classifiers, detections
- `survey="lsst"` — ALeRCE, partial/degraded: `query_magstats` raises `NotImplementedError`; ndet/mag stats fall back to raw detections. `query_classifiers`/`query_classes` also not implemented yet. LSST oids are integers from the API, normalized to str in `fetch_candidates`.
- `survey="antares"` — ANTARES broker: tag-based classification, locus model; requires `antares-client`
- Run ANTARES: `python pipeline.py antares [page_size | locus_id | ZTF_oid ...]`

## Known API quirks

- ALeRCE: `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- ALeRCE: API returns duplicate oids — handled in `fetch_candidates`
- ALeRCE: LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
- ANTARES: credentials required for Kafka streaming; search/fetch API is open (confirmed). `get_by_ztf_object_id`, `get_by_id`, `get_random_locus_ids` all work without auth.
- ANTARES: `get_random_locus_ids` returns duplicates — deduplicated in `fetch_antares_candidates`.
- ANTARES: `locus.alerts` includes both `ztf_candidate` (real detections, have `ant_mag`) and `ztf_upper_limit` (non-detections, no `ant_mag`). Pipeline filters to `ant_mag.notna()` before building the lightcurve.
- ANTARES: `locus.tags` returns plain strings (not objects); `locus.properties` contains num_mag_values which is the quality-filtered detection count.
