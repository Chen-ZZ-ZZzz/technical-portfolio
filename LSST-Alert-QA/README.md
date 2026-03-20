# LSST/ZTF Alert Data Quality Pipeline

A data quality pipeline for ZTF (and eventually LSST) transient alert data via the [ALeRCE](https://alerce.science/) broker. Fetches objects, validates completeness, and produces a QA report with weighted classifier consensus.

LSST support is included with graceful degradation — the pipeline runs end-to-end but full QA is not yet possible pending survey ramp-up.

## Context

Built as a QA engineering showcase using real astronomical alert data from the Vera C. Rubin Observatory (LSST, launched February 2026) and the Zwicky Transient Facility. Development assisted by Claude Code. The validation patterns — completeness checks, classifier consensus, threshold tuning, structured reporting — transfer directly to sensor data validation in HIL/SIL test environments.

---

## Built With

- Python 3, pandas, pytest
- [ALeRCE](https://alerce.science/) broker API and Python client
- Claude Code (AI-assisted development)

---

## What It Does

- **Completeness validation** — checks for missing detections, null magnitudes, absent real/bogus scores, sparse observations, and API fetch failures
- **Weighted classifier consensus** — aggregates votes from up to 24 independent classifiers, weighting by method relevance (light curve vs stamp), confidence, and model recency
- **Survey-aware checks** — adapts validation rules for ZTF (mature, data-rich) vs LSST (early-stage, sparse), with graceful degradation for unimplemented API endpoints
- **Structured QA reporting** — each object gets a tiered status (PASS / REVIEW_MINOR / REVIEW_MAJOR / FLAG) with detailed flags explaining why

---

## Install

Requires Python 3.13. Uses `uv` for package management.

```bash
git clone <repo>
cd <repo>
python -m venv .venv
source .venv/bin/activate
uv pip install -e .
```

Dev dependencies (pytest):

```bash
uv pip install pytest pytest-mock
```

---

## Usage

### CLI

```bash
# ZTF — fetch 100 objects (default)
python pipeline.py

# ZTF — fetch N objects
python pipeline.py ztf 25

# ZTF — specific OIDs
python pipeline.py ztf ZTF17aaaaahl ZTF18abc

# LSST — fetch 100 objects
python pipeline.py lsst

# Via installed script
alerce-qa ztf 20
```

### Python API

```python
from alerce_qa import run_pipeline

# Full run
df = run_pipeline(page_size=50, survey="ztf")

# Explicit OIDs
df = run_pipeline(survey="ztf", oids=["ZTF17aaaaahl", "ZTF18abc"])

print(df[["oid", "top_class", "consensus", "status"]])
```

### Diagnostic profiler (ZTF only)

```python
from alerce_qa.profiler import object_profile

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
| `top_class` | Plurality class from weighted consensus |
| `class_prob` | Best classifier probability for `top_class` |
| `consensus` | Weighted consensus score [0, 1] |
| `n_classifiers` | Number of classifiers that voted |
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

Weighted consensus across all classifiers. Each vote is weighted by:

- **Method** — `lc_classifier` outweighs `stamp_classifier`; the gap widens as `ndet` grows (lc data becomes more informative)
- **Confidence** — the classifier's own probability for its top class
- **Recency** — small tiebreaker from classifier version string

Rules applied in order:

| Condition | Verdict | Status |
|---|---|---|
| < 2 classifiers voted | — | `FLAG` (insufficient_classifiers) |
| consensus ≥ 0.90 | pass | `PASS` |
| consensus ≥ 0.65, all dissenters < prob 0.30 | review_minor | `REVIEW_MINOR` |
| otherwise | review_major | `REVIEW_MAJOR` |

---

## Survey Support

| Feature | ZTF | LSST |
|---|---|---|
| Detections | ✓ | ✓ |
| Magstats | ✓ | — (falls back to raw detections) |
| Classifiers | ✓ | — (not yet in API) |
| `rb`/`drb` scores | ✓ (`rb` + `drb`) | `rb_absent` checked via `reliability`; no `drb` equivalent |
| Profiler | ✓ | — (`query_lightcurve` ZTF only) |

---

## Project Structure

```
src/alerce_qa/
    config.py      — constants and thresholds
    client.py      — ALeRCE API wrapper with retry
    validators.py  — completeness checks
    classifier.py  — weighted consensus logic
    reporting.py   — QA row assembly and pipeline orchestration
    profiler.py    — single-object diagnostic tool
    __main__.py    — CLI entry point
pipeline.py        — backwards-compatible shim
tests/             — 64 pytest tests, mock data only
pyproject.toml
```

---

## Tests

```bash
python -m pytest tests/ -v
```

All tests use mock data — no live ALeRCE API calls.

---

## Known API Quirks

- `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- API returns duplicate oids — deduplicated in `fetch_candidates`
- LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
- LSST oids come back as integers from the API — normalized to `str` in `fetch_candidates`
