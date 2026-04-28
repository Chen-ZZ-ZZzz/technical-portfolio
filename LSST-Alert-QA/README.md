# LSST/ZTF/ANTARES Alert Data Quality Pipeline

A data quality pipeline for ZTF (and eventually LSST) alert data via the [ALeRCE][alerce link] and [ANTARES][antares link] brokers. Fetches objects, validates completeness, and produces a QA report with weighted classifier consensus.

---

## The Alert Ecosystem

The [Vera C. Rubin Observatory](https://rubinobservatory.org/) is an astronomical observatory in Chile. Its main task is to conduct an astronomical survey of the southern sky every few nights, creating a ten-year time-lapse record, termed the **Legacy Survey of Space and Time (LSST)**.

The telescope would generate up to 10 millions alerts per night, about objects that have changed brightness or position relative to archived images. The alerts are immediately available to the public, via alert streams from external "_event brokers_".

The Zwicky Transient Facility (ZTF) serves as a prototype of the system, generating 1 million alerts per night.

**ALeRCE** (Automatic Learning for the Rapid Classification of Events) is a Chilean-led Community Broker. Its LSST support is currently only included with graceful degradation.

**ANTARES** (Arizona-NOIRLab Temporal Analysis and Response to Events System) is an NSF NOIRLab broker that processes ZTF alerts and is approved for the full LSST stream. Unlike ALeRCE's probability-based classifiers, ANTARES uses discrete _tags_ produced by Python filters. Each tag is a science signal (e.g. `nuclear_transient`, `dimmers`) or a pipeline annotation. The search API is open; real-time Kafka streaming requires credentials. ANTARES _started ingesting LSST alerts_ Feb 24, 2026.

---

## Context

Built as a QA engineering showcase using real astronomical alert data from the Vera C. Rubin Observatory (LSST, launched February 2026) and the Zwicky Transient Facility (ZTF). Development assisted by Claude Code.

The validation patterns include completeness checks, classifier consensus, threshold tuning, and structured reporting. This mimics sensor data validation in HIL/SIL test environments.

### Bright Solar System Objects (SSO) Monitor

`antares_sso_monitor.py` is a stand-alone script which scans ANTARES daily for SSO loci that have suddenly brightened. Proof of concept / exploration. Self built _without assists from Claude Code_.

First run defaults to 7-day look-back with empty magnitudes. Daily deployment automated by systemd user timer. Magnitude states of SSO loci from daily scan are stored in `bright_sso_state.json`. Stores daily service log to `logs/sso_monitor.log`.

**Known limitations:**

1. `sso_candidates` tag is ZTF-based, LSST pipeline not yet in ANTARES tag system. After analysing a few days' worth of date, observed high false positive rate due to stellar variable contamination in the tag.

2. Magnitude thresholds are currently arbitrary starting points.

---

## Built With

- Python 3, pandas, pytest
- [ALeRCE broker][alerce link] API and Python client
- [ANTARES broker][antares link] and `antares-client`
- Claude Code (AI-assisted development)

---

## What It Does

- **Completeness validation** — checks for missing detections, null magnitudes, absent real/bogus scores, sparse observations, and API fetch failures

- **Weighted classifier consensus** — aggregates votes from up to 24 independent classifiers, weighting by method relevance (light curve vs stamp), confidence, and model recency

- **ANTARES tag classification** — maps discrete science tags to the same verdict schema, filtering out pipeline/infrastructure tags before scoring

- **Survey-aware checks** — adapts validation rules for ZTF (mature, data-rich) vs LSST (early-stage, sparse), with graceful degradation for unimplemented API endpoints

- **Structured QA reporting** — each object gets a tiered status (PASS / REVIEW_MINOR / REVIEW_MAJOR / FLAG) with detailed flags explaining why

---

## Install

Requires Python 3.13. Uses `uv` for package management.

```bash
git clone <repo>
cd <repo>
uv sync
```

Dev dependencies (pytest) are included, uv sync handles everything

---

## Usage

### CLI

```bash
# ZTF — fetch 100 objects (default)
uv run pipeline.py

# ZTF — fetch N objects
uv run pipeline.py ztf 25

# ZTF — specific OIDs
uv run pipeline.py ztf ZTF17aaaaahl ZTF18abc

# LSST — fetch 100 objects
uv run pipeline.py lsst

# ANTARES — fetch 20 random loci
uv run pipeline.py antares 20

# ANTARES — specific locus IDs or ZTF object IDs
uv run pipeline.py antares ANT2020j7wo4 ZTF20aafqubg

# Via installed script
uv run rubin-qa ztf 20
uv run rubin-qa antares 10
```

### Python API

```python
from rubin_qa.reporting import run_pipeline, run_antares_pipeline

# ALeRCE full run
df = run_pipeline(page_size=50, survey="ztf")

# ALeRCE explicit OIDs
df = run_pipeline(survey="ztf", oids=["ZTF17aaaaahl", "ZTF18abc"])

# ANTARES random loci
df = run_antares_pipeline(page_size=20)

# ANTARES explicit locus IDs or ZTF object IDs
df = run_antares_pipeline(locus_ids=["ANT2020j7wo4", "ZTF20aafqubg"])

print(df[["oid", "top_class", "consensus", "status"]])
```

### Diagnostic profiler (ZTF only)

```python
from rubin_qa.profiler import object_profile

object_profile("ZTF17aaaaahl")
```

Prints classification verdict, per-filter magstats, and light curve summary for one object.

---

## QA Report

Reports are saved to `reports/qa_{survey}_{timestamp}_n{count}.csv` after each run. The CLI also prints a summary table (`oid`, `ndet`, `top_class`, `consensus`, `n_classifiers`, `status`) and a flagged-object count.

One row per object. Columns:

| Column | Description |
|---|---|
| `oid` | Object identifier |
| `ndet` | Total detection count |
| `mag_range` | Brightness amplitude: magmax − magmin |
| `timespan_days` | Last − first detection epoch |
| `top_class` | ALeRCE: plurality class from weighted consensus; ANTARES: all science tags, sorted and joined |
| `class_prob` | Best classifier probability for `top_class` |
| `consensus` | Weighted consensus score [0, 1] |
| `n_classifiers` | ALeRCE: classifiers that voted; ANTARES: science tags present |
| `n_agree` / `n_disagree` | Classifiers voting for/against plurality class |
| `confirmed` | `True` if ndet > 1 |
| `has_issues` | `True` if any completeness issues |
| `completeness_issues` | List of issue tokens (see below) |
| `flag` | Combined flag string, or `None` |
| `status` | `PASS` / `REVIEW_MINOR` / `REVIEW_MAJOR` / `FLAG` |

**Status tiers (evaluated in order):**

| Status | Condition |
|---|---|
| `FLAG` | Any completeness issues, or fewer than 2 classifiers voted (`insufficient_classifiers`) |
| `PASS` | No issues, consensus ≥ 0.90 across ≥ 2 classifiers |
| `REVIEW_MINOR` | No issues, consensus ≥ 0.65, all dissenters below prob 0.30 |
| `REVIEW_MAJOR` | No issues, genuine classifier split — needs human inspection |

Note: `REVIEW_MINOR` is currently dormant for ZTF because `lc_classifier` returns no data for most objects, leaving only one classifier voting. It will activate once `lc_classifier` data flows.

**Completeness issue tokens** (appear in `completeness_issues` and `flag`): `no_detections`, `no_magstats`, `ndet_lt_2`, `coordinates_missing`, `mag_null`, `rb_absent`, `drb_absent` (ZTF only), `no_classification`, `fetch_error_<field>`

**Classification flag tokens** (appear in `flag` only): `insufficient_classifiers`, `minor disagreement: ...`, `genuine split: ...`, `no_classification`, `no_ranking1_rows`, `zero_total_weight`

---

## Classification

### ALeRCE

Weighted consensus across all classifiers. Each vote is weighted by:

- **Method** — `lc_classifier` outweighs `stamp_classifier`; the gap widens as `ndet` grows (lc data becomes more informative)
- **Confidence** — the classifier's own probability for its top class
- **Recency** — small tiebreaker from classifier version string

| Condition | Status |
|---|---|
| < 2 classifiers voted | `FLAG` (insufficient_classifiers) |
| consensus ≥ 0.90 | `PASS` |
| consensus ≥ 0.65, all dissenters < prob 0.30 | `REVIEW_MINOR` |
| otherwise | `REVIEW_MAJOR` |

### ANTARES

Tags are filtered into science vs pipeline sets before scoring. The 19 science tags (e.g. `dimmers`, `nuclear_transient`, `extragalactic`) count toward consensus; the 19 pipeline/infrastructure tags (e.g. `lc_feature_extractor`, `high_snr`, `in_LSSTDDF`) are stripped. Unknown tags are flagged explicitly rather than silently ignored.

`top_class` reports all science tags present, sorted (e.g. `"dimmers, extragalactic"`). Consensus = 1/n_science_tags. Single science tag → PASS; multiple → REVIEW_MINOR/MAJOR by count; none → FLAG.

All ANTARES tags are filter outputs, not confirmed classifications — treat every tag as a candidate until followed up.

---

## Broker / Survey Support

| Feature | ZTF (ALeRCE) | LSST (ALeRCE) | ANTARES |
|---|---|---|---|
| Detections | ✓ | ✓ | ✓ (alerts, upper limits filtered out) |
| Magstats | ✓ | — (falls back to raw detections) | ✓ (locus properties) |
| Classifiers | ✓ | — (not yet in API) | ✓ (tag-based, science tags only) |
| `rb`/`drb` scores | ✓ | `reliability` only | — (pre-filtered upstream, rb ≥ 0.55) |
| Catalog cross-matches | — | — | ✓ (Gaia, Sloan, WISE, Chandra) |
| Real-time stream | — | — | ✓ (Kafka, requires credentials) |
| Profiler | ✓ | — | — |

---

## Project Structure

```
src/rubin_qa/
    config.py          — constants and thresholds
    client.py          — ALeRCE API wrapper with retry
    antares_client.py  — ANTARES API wrapper
    validators.py      — validate_completeness, validate_antares
    classifier.py      — classify_object (weighted consensus), classify_antares (tags)
    reporting.py       — QA row assembly and pipeline orchestration (ALeRCE + ANTARES)
    profiler.py        — single-object diagnostic tool (ZTF only)
    __main__.py        — CLI entry point
pipeline.py            — backwards-compatible shim
tests/                 — pytest, mock data only
pyproject.toml
```

---

## Tests

```bash
uv run pytest tests/ -v
```

All tests use mock data — no live API calls.

---

## Known API Quirks

**ALeRCE:**
- `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- API returns duplicate oids — deduplicated in `fetch_candidates`
- LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
- LSST oids come back as integers from the API — normalized to `str` in `fetch_candidates`

**ANTARES:**
- Kafka streaming requires credentials (request from ANTARES team); search/fetch API is open
- `get_random_locus_ids` returns duplicates — deduplicated in `fetch_antares_candidates`
- `locus.alerts` bundles real detections (`ztf_candidate`, have `ant_mag`) and non-detections (`ztf_upper_limit`, no `ant_mag`) — pipeline filters to `ant_mag.notna()` before building the lightcurve
- ANTARES pre-filters alerts to rb ≥ 0.55, fwhm ≤ 5.0 px, elong ≤ 1.2 — objects in ANTARES already pass these; ALeRCE objects may not
- `antares-client` import is deferred — ALeRCE-only installs are unaffected if the package is absent

[alerce link]: https://alerce.science/
[antares link]: https://antares.noirlab.edu/
