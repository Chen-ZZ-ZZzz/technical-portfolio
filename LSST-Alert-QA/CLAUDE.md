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
    retry_budget.py    — run-wide retry sleep budget, shared by both clients
    client.py          — ALeRCE API wrapper (_api_call, fetch_candidates, fetch_object_data)
    antares_client.py  — ANTARES API wrapper (fetch_antares_candidates, fetch_antares_locus)
    validators.py      — validate_completeness, validate_antares
    classifier.py      — _version_score, _method_weight, classify_object, classify_antares
    reporting.py       — build_qa_row, run_pipeline, build_antares_qa_row, run_antares_pipeline
    profiler.py        — object_profile (diagnostic deep-dive, ZTF only, uses query_lightcurve)
    __main__.py        — CLI entry point
pipeline.py            — thin shim: python pipeline.py [survey] [page_size | oid ...]
tests/                 — pytest, mock data only (no live API calls)
    test_antares.py    — ANTARES path end to end; antares_client.py at 100% stmt coverage
pyproject.toml         — build: hatchling, script: rubin-qa
```

## Pipeline stages

**ALeRCE (ztf/lsst):** `fetch_candidates()` → `fetch_object_data()` → `validate_completeness()` → `classify_object()` → `build_qa_row()` → `run_pipeline()`

**ANTARES:** `fetch_antares_candidates()` → `fetch_antares_locus()` → `validate_antares()` → `classify_antares()` → `build_antares_qa_row()` → `run_antares_pipeline()`

## Key design decisions

- `fetch_candidates`: deduplicates oids (ALeRCE API returns duplicates); default page size 100
- `fetch_object_data`: 3 API calls per object (detections, magstats, probabilities), each with retry (4 attempts, 9s base delay, exponential → 9/18/36s). Sized for ALeRCE 504 episodes, where the endpoint stays slow for tens of seconds.
- Failure messaging: operator-facing messages go to stderr with one of two prefixes from config.py — `WARN: ` (`WARN_PREFIX`) for degraded-but-recoverable conditions where the run continues (retry attempts, an empty candidate page), and `ERROR: ` (`ERROR_PREFIX`) for failures whose caller cannot proceed (failed candidate fetch, pipeline exception, no rows produced). Every ERROR path returns no data or exits 1. stdout carries only normal output (progress lines, QA table). A run that produces no rows writes no CSV and exits 1.
- `validate_completeness`: confirmed = ndet > 1; no magnitude threshold filtering
- `classify_object`: weighted consensus — lc_classifier outweights stamp_classifier, gap widens with ndet; consensus ≥ 0.90 → clean, ≥ 0.65 + dissenters < 0.30 → minor flag, else genuine split
- `build_qa_row` status tiers (in priority order): FLAG if completeness issues or n_classifiers < 2 ("insufficient_classifiers"); PASS if consensus ≥ 0.90; REVIEW_MINOR if consensus ≥ 0.65 + outlier dissenters; REVIEW_MAJOR otherwise
- REVIEW_MINOR is currently dormant for ZTF: `lc_classifier` returns empty for most objects, leaving only stamp_classifier voting → n_classifiers=1 → FLAG (insufficient_classifiers). Will activate when lc_classifier data flows.

### ANTARES-specific design

- `fetch_antares_candidates`: deduplicates locus IDs (ANTARES API returns duplicates, same issue as ALeRCE)
- `fetch_antares_locus`: accepts ANTARES locus IDs (`ANT...`) or ZTF object IDs (`ZTF...`) — routes to `get_by_id` or `get_by_ztf_object_id` accordingly. Builds `ms` from locus.properties (num_mag_values, brightest/faintest_alert_magnitude). Filters `locus.alerts` to `ant_mag.notna()` rows only — upper limits (`ztf_upper_limit`) have no `ant_mag` and are excluded from the lightcurve.
- `ndet` uses `num_mag_values` from locus.properties (ANTARES quality-filtered count, conservative). `len(dets)` after filter may be slightly higher — ANTARES applies additional cuts not reflected in the raw alert stream.
- `classify_antares`: filters raw tags to `SCIENCE_TAGS` before computing consensus; pipeline/filter tags are stripped. `top_class` = all science tags sorted and joined (e.g. `"dimmers, extragalactic"`). consensus = 1/n_science_tags; single science tag → PASS; two or more → REVIEW_MAJOR; only pipeline/unknown tags → REVIEW_MAJOR with `no_science_tags` flag listing which pipeline/unknown tags were present; zero tags → FLAG (`no_classification` from `validate_antares`). REVIEW_MINOR is unreachable here: 1/n caps two tags at 0.50, under the 0.65 majority threshold.
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
- ANTARES: `get_random_locus_ids` returns duplicates — deduplicated in `fetch_antares_candidates`. Expect **10-15% fewer IDs than requested** (measured 2026-08-06: 256 raw → 217/225/228 unique). Cause is upstream: the ES query uses `random_score` with no seed, and `_list_all_resources` issues a fresh request per page, so ordering reshuffles mid-walk and loci recur across pages. `run_antares_pipeline` emits one row per deduplicated ID unconditionally, so `len(df)` == unique count — a short report is this, not a row-drop.
- ANTARES: an ANT locus ID that does not exist answers **500, not 404**, so it is indistinguishable from a transient server fault and costs the full retry backoff. `get_by_ztf_object_id` correctly returns None → `locus:not_found`.
- ANTARES: `fetch_antares_candidates` / `fetch_antares_locus` retry via `antares_client._api_call` (same 4 attempts / 9s exponential as ALeRCE), on `requests` exceptions and `AntaresException`. ANTARES imposes its own 60s read timeout; before the retry existed, one slow morning meant the daily run produced no CSV at all.
- Retry is per-object in **both** brokers, so an outage multiplies across the page — ALeRCE worst, at 3 retried calls per object. Three ceilings bound it, applied to `run_pipeline` and `run_antares_pipeline` alike. All degrade rather than abort:
  - `REQUEST_TIMEOUT` (60s) — forced onto the ALeRCE client by `client._force_session_timeout()`, which wraps `Session.request` on all **five** sessions the Alerce object holds (top-level + legacy/multisurvey × search/stamps). The alerce package passes no timeout at all, so requests would block forever; the per-run ceilings are checked *between* calls and cannot interrupt a blocked socket. antares-client applies its own 60s timeout, so it needs no equivalent.
  - `RETRY_BUDGET_SECONDS` (300s) — total retry *sleep* per run, held in `retry_budget.py` and shared by both clients (a run stalls once, regardless of broker). Each pipeline calls `retry_budget.reset()` before its loop; `_api_call` draws on it via `consume()`, which clamps to what is left so the cap is exact. Once spent, attempts continue with no backoff.
  - **Run deadline — derived per job, not a constant.** `deadline_for(n, survey)` = `DEADLINE_SLACK` (6×) × `estimate_runtime()`, floored at `DEADLINE_FLOOR` (300s), computed from the *resolved* object count after dedup. `max_run_seconds=None` (default) means derive; `0` disables. A 1000-object ZTF scan is entitled to its ~1h; what trips is a run dragging far past its own estimate, i.e. a stalled broker or link. The slack is 6× rather than 3× because it absorbs drift in `SECONDS_PER_OBJECT` between measurement days, not just per-run variance — see below.
- `SECONDS_PER_OBJECT` (config.py) holds measured per-object cost — ztf 3.7s (3 ALeRCE calls), lsst 2.4s (magstats short-circuits client-side), antares 1.0s (measured 2026-08-11, 20 samples/survey). Feeds both the estimate and the deadline; update if broker latency shifts. Affects only pacing, never correctness.
  - These are volatile: on 2026-08-06 ztf measured 17.0s, 4.6× the 2026-08-11 figure, with no incident in between. Which sample is representative is **still open** — more data points wanted. `DEADLINE_SLACK` (6×) is sized so that a return to 17.0s/object degrades the estimate without truncating a healthy run (3.7 × 6 = 22.2s/object of headroom).
  - `DEFAULT_SECONDS_PER_OBJECT` (17.0s) does *not* track the table — it stays deliberately pessimistic for an unmeasured survey, since a new broker is likelier to be slow than fast.
  - Re-measuring: `tools/bench_latency.py` times each client call directly (no retry wrapper), 10 objects per survey, two rounds. What matters is per-object cost *excluding* the one-off candidate fetch, which `SECONDS_PER_OBJECT` does not model.
  - **Open sampling campaign (started 2026-08-11):** `tools/sample_latency.py` appends one record per run to `logs/latency_samples.jsonl` (~2.5 min; `--quick` skips the candidate fetch, ~1.5 min). Collected by an hourly systemd timer with `RandomizedDelaySec=3600` (`systemd/lsst-latency-sample.*.example`), which lands one sample inside each hour and fills all 24 hour-buckets per day; manual runs at odd hours work too and mix in fine. Each record keeps every individual object timing, and `--report` pools them across runs, so sample size does not track how often anyone remembers to run it. Records also store the `SECONDS_PER_OBJECT` in force at sampling time, so changing the constants mid-campaign does not corrupt the history. Broker failures are recorded as samples rather than aborting the run — they carry no timing, so `--report` lists them separately (first one caught 2026-08-11 22:50: ZTF `ReadTimeout` at the full 60s `REQUEST_TIMEOUT`, recovering to normal by 23:05 — the broker really does swing). The timer runs `Persistent=true`, so records carry `uptime_s`/`source` and `--report --exclude-boot` drops boot-adjacent catch-up runs at analysis time. Until it is reviewed, treat the 2026-08-11 numbers as provisional. Note `logs/` is gitignored — samples are local-only.
- The candidate fetch is unmodeled by `SECONDS_PER_OBJECT` and is the slowest single call in the system: ZTF `query_objects` measured 24-40s (2026-08-11), against a 60s `REQUEST_TIMEOUT`. It is also *slower* at page_size=10 than at 100 — server-side variance dominates, page size barely matters. This call, not the per-object ones, is what keeps `REQUEST_TIMEOUT` at 60s.
- CLI confirms before long runs: `_confirm_long_run` prompts y/N when the estimate exceeds `CONFIRM_THRESHOLD_SECONDS` (900s). **Only on a TTY** — under systemd there is no stdin, so it logs the estimate to stderr and proceeds rather than hanging the unit. `-y/--yes` skips it.
- ANTARES: `locus.alerts` includes both `ztf_candidate` (real detections, have `ant_mag`) and `ztf_upper_limit` (non-detections, no `ant_mag`). Pipeline filters to `ant_mag.notna()` before building the lightcurve.
- ANTARES: `locus.tags` returns plain strings (not objects); `locus.properties` contains num_mag_values which is the quality-filtered detection count.
