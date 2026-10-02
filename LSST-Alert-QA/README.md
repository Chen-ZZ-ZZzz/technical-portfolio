# LSST Alert Data Quality Pipeline

A data quality pipeline for LSST (and ZTF) alert data via the [ALeRCE][alerce link] and [ANTARES][antares link] brokers. Fetches objects, validates completeness, and produces a QA report with weighted classifier consensus.

---

## The Alert Ecosystem

The [Vera C. Rubin Observatory](https://rubinobservatory.org/) is an astronomical observatory in Chile. Its main task is to conduct an astronomical survey of the southern sky every few nights, creating a ten-year time-lapse record, termed the **Legacy Survey of Space and Time (LSST)**.

The telescope would generate up to 10 millions alerts per night, about objects that have changed brightness or position relative to archived images. The alerts are immediately available to the public, via alert streams from external "_event brokers_".

The Zwicky Transient Facility (ZTF) serves as a prototype of the system, generating 1 million alerts per night.

**ALeRCE** (Automatic Learning for the Rapid Classification of Events) is a Chilean-led Community Broker. Its current support of LSST is clean and mostly full.

**ANTARES** (Arizona-NOIRLab Temporal Analysis and Response to Events System) is an NSF NOIRLab broker that processes ZTF alerts and is approved for the full LSST stream. Unlike ALeRCE's probability-based classifiers, ANTARES uses discrete _tags_ produced by Python filters. Each tag is a science signal (e.g. `nuclear_transient`, `dimmers`) or a pipeline annotation. The search API is open; real-time Kafka streaming requires credentials. ANTARES _started ingesting LSST alerts_ Feb 24, 2026.

---

## Context

Built as a QA engineering showcase using real astronomical alert data from the Vera C. Rubin Observatory (LSST, launched February 2026) and the Zwicky Transient Facility (ZTF). Development assisted by Claude Code.

The validation patterns include completeness checks, classifier consensus, threshold tuning, and structured reporting. This mimics sensor data validation in HIL/SIL test environments.

It also includes a worked false-positive investigation: the SSO monitor below was run daily for four months, its alerts audited against independent ground truth, and its design assumption falsified rather than its thresholds retuned. Negative results are reported here as findings, not hidden. Both of the investigative jobs in this repo — that monitor and the broker-latency sampler — have since been retired on purpose: each was run until it answered its question, then stopped rather than left ticking.

---

## Bright Solar System Objects (SSO) Monitor

> **Retired 2026-08-18** — ran daily for 115 scans, 2026-04-13 to 2026-08-17 (the last scan logged just after midnight on the 18th); the systemd timer is disabled. The audit below is the reason: the premise was falsified, so there was nothing left to learn from another night of scanning. Kept in the repo as the investigation, not as a running detector.

`antares_sso_monitor.py` is a stand-alone script which scanned ANTARES daily for SSO loci that had suddenly brightened. Proof of concept / exploration. Self built _without assists from Claude Code_.

> **Negative result — and the reason it is in this portfolio.** The premise does not hold: an ANTARES locus is a sky position, not an object, so it cannot track a moving target. Every alert this monitor raised was a stationary variable star or galaxy. The script is kept unfixed because the investigation is the deliverable: a production false-positive rate traced to an invalid design assumption, tested against independent ground truth, with the residual risk quantified instead of tuned away.

First run defaults to 7-day look-back with empty magnitudes. Daily deployment was automated by a systemd user timer (`lsst-sso-monitor`, now disabled). Magnitude states of SSO loci from each scan were stored in `logs/bright_sso_state.json`, and the daily service log in `logs/sso_monitor.log` — the log the audit below had to reconstruct its test population from.

### Audit, 2026-08-17

**1 — Symptom.** Daily runs from 2026-04-13 raised 208 brightening events across 181 loci. Manual spot-checks kept landing on long-period variables and galaxies rather than asteroids, at a rate high enough to suspect the detector rather than the sky.

**2 — Audit design.** Full population, not a sample: all 375 loci the monitor had ever touched. This first required recovering the test population — the 181 loci that actually alerted were **absent from `bright_sso_state.json`** (the state was reset on 2026-06-09; the two sets have zero overlap), so they were reconstructed from `logs/sso_monitor.log` and re-fetched from ANTARES. The oracle was chosen to be independent of the system under test: MPC ephemeris magnitudes (`ztf_ssmagnr`) carried in the ZTF alert packet, plus membership in Gaia DR2/DR3, VSX and ASAS-SN — never the ANTARES `sso_candidates` tag that raised the alert in the first place.

**3 — Result.** Of the 181 alerted loci, **181 are stationary sources, not solar system objects**: 170 variable stars, 2 extragalactic (one an AGN1 with Gaia quasar probability 0.87), 9 stationary but uncatalogued. Against the 194 loci collected since the filter fix, the two populations separate on every independent axis:

| | alerted loci (181) | genuine asteroid detections (194) |
|---|---|---|
| Gaia DR2 + DR3 source | 181 | 4 |
| VSX / ASAS-SN variable catalogues | 114 / 126 | 0 |
| detections per locus (median) | 601 (max 2194) | 2 |
| prior detections at that position (`ndethist`) | 698 (max 2945) | 2 |
| reference-image counterpart (`distnr`) | 0.22″ | 6.7″ |
| detection history span | 2538 d | 0 d |
| \|measured − MPC predicted mag\| | **3.90** | **0.30** |

**4 — Root cause: an ANTARES locus is a sky position, not an object.** Alerts within ~1″ are merged into one locus. A variable star sits there permanently and accumulates an 8-year light curve; an asteroid crosses the same position once and contributes only the `sso_candidates` tag. The monitor then read `newest_alert_magnitude` off that locus as though one object produced all of it, so what it measured as "brightening" was the star varying.

The converse is equally fatal: a moving object can never accumulate a light curve at a locus. Asteroid 31521 appears in **361 distinct loci**, RA spread 360°, Dec spread 34° — one or two alerts per night, each at a new position, never returning. Comparing a locus magnitude between scans is therefore meaningless for a mover. **This is not a threshold-tuning problem and no choice of `MAG_THRESHOLD` / `DELTA_MAG_ALERT` fixes it.**

**5 — Differential test of the existing mitigation.** The `.exclude("terms", catalogs=STELLAR_CATALOGS)` clause added around 2026-06-09 catches **181 of 181** historical contaminants. Confirmed by running the query with and without the clause against a known-bad input, `ANT2020xooq` (1081 detections, VSX eclipsing binary `ZTF J190900.29-132623.4`, P = 0.387 d): returned without the clause, dropped with it. Every alert in the log predates that clause; the 61 scans since have produced zero events. So the loci now being collected really are asteroid detections — magnitudes match MPC predictions to 0.30 mag, no counterpart within ~7″, `ndethist` = 2, and 177 of 194 carry `sso_confirmed` (against 1 of 181 in the alerted set).

**6 — Residual risk after the mitigation.** The filter removes the stationary contaminants, but the locus is still the wrong unit. 167 of those 194 loci contain detections from **two or more different asteroids** that crossed the same position years apart — e.g. `ANT20222ko6002pughw` holds asteroid 22564 (2022-09) and 53857 (2026-05). 68 of them differ by more than `DELTA_MAG_ALERT` between their two asteroids, so each is a false "brightened" alert waiting for its next detection.

**7 — What would work instead**, if this is picked up again: the identity of a mover is per-alert, not per-locus — `ztf_ssnamenr` (MPC number), with `ztf_ssdistnr` and `ztf_ssmagnr`. It is searchable via `properties.ztf_ssnamenr`, though the locus-level property keeps only one name so blends must still be filtered alert by alert. The strongest signal needs no state file at all: the residual `ant_mag − ztf_ssmagnr` against the MPC ephemeris separated real asteroid detections from star blends at 0.30 vs 3.90 mag, and an asteroid genuinely brighter than prediction is the interesting event in the first place. Newly discovered movers carry no `ssnamenr` and need a different signature (`ndethist` = 1, no counterpart, no catalogue match).

Remaining minor limitations: `sso_candidates` is ZTF-based, with no LSST equivalent yet in the ANTARES tag system; the magnitude thresholds were arbitrary starting points and were never reached — only 2 of 194 loci ever got brighter than mag 15.

Full per-locus verdict table, committed as the evidence behind every number above: [`reports/sso_audit_2026-08-17.csv`](reports/sso_audit_2026-08-17.csv) — 375 rows, one per locus, with the raw discriminants (`ndethist`, `distnr`, catalogue membership, magnitude residual against the MPC prediction) and the verdict each one supports.

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

- **Survey-aware checks** — adapts validation rules for ZTF (mature, data-rich) vs LSST (early-stage, sparse), with graceful degradation for unimplemented API endpoints, and without letting a structurally-absent endpoint masquerade as a data defect

- **Single-object profiler** — a diagnostic deep-dive on any of the three brokers, deriving per-band photometry where the broker serves no magstats

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

# quiet mode — one-line summary
uv run pipeline.py -q

# long scans confirm first (est. runtime + deadline), unless -y
uv run pipeline.py ztf 500        # prompts: "≈ 142 min estimated. Continue? [y/N]"
uv run pipeline.py ztf 500 -y     # skip the prompt
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

# Deadline defaults to 6x the job's own estimate; override or disable it
df = run_antares_pipeline(page_size=256, max_run_seconds=900)
df = run_antares_pipeline(page_size=5000, max_run_seconds=0)   # no deadline

# Inspect the sizing without running
from rubin_qa.reporting import estimate_runtime, deadline_for
estimate_runtime(1000, "ztf") / 60   # -> 283 min
deadline_for(1000, "ztf") / 60       # -> 850 min

print(df[["oid", "top_class", "consensus", "status"]])
```

### Diagnostic profiler

Deep-dive on a single object, on any of the three brokers. Where the pipeline says an
object is `FLAG` or `REVIEW_MAJOR`, the profiler says why.

```bash
# ZTF
uv run python -m rubin_qa.profiler ztf ZTF17aaaaahl

# LSST
uv run python -m rubin_qa.profiler lsst 170226393632735260

# ANTARES — locus ID or ZTF object ID
uv run python -m rubin_qa.profiler antares ANT2026fmcqp4h4xvw4

# via the installed script; several objects in one go
uv run rubin-qa-profile ztf ZTF17aaaaahl ZTF18abc
```

```python
from rubin_qa.profiler import object_profile

object_profile("170226393632735260", survey="lsst")
```

Three sections per object: **classification verdict**, **per-band photometry**, and a
**light curve summary**.

Per-band photometry is served magstats on ZTF only — ALeRCE has none for LSST and
ANTARES has none at all, so both are derived from their own detections (grouped by
band; LSST fluxes converted nJy → AB). The output says which it is, so a derived table
is never mistaken for a served one.

| | ZTF | LSST | ANTARES |
|---|---|---|---|
| Classification | probabilities → weighted consensus | same — and 2 classifiers really do vote | science tags → same verdict schema |
| Bands | `fid` → g/r/i | `band_name` (u/g/r/i/z/y) | `ant_passband` |
| Non-detections | upper limits | forced photometry (`non_detections` comes back empty) | `ant_maglim`, in the same frame |
| Also reports | `magpsf_corr` coverage | `reliability`, `snr` ranges | `ant_mag_corrected` coverage, source surveys |

Real output, LSST object `170226393632735260`:

```
================================================================
FULL PROFILE: 170226393632735260   [lsst]
================================================================

-- Classification (all classifiers, ranking=1) --
                     classifier_name class_name  probability
         stamp_classifier_rubin_beta         SN     0.997319
stamp_classifier_rubin_beta_20260421         SN     0.985418

-- Photometry by band --
  (derived from detections — ALeRCE serves no LSST magstats)
band  ndet  magmin  magmax  magmean  magsigma  firstmjd   lastmjd
   g    13  21.022  23.705   21.785     0.896 61135.003 61178.984
   i    17  20.990  22.231   21.320     0.291 61136.156 61172.981
   r    16  20.847  22.699   21.689     0.558 61136.021 61178.002
   z    14  21.414  21.964   21.643     0.150 61135.042 61172.005

  Verdict:     SN  (best prob 0.997, weighted consensus 100%)
  Classifiers: 2/2 vote for 'SN'

-- Light curve --
  Detections:     60 epochs
  Non-detections: 0 upper limits
  Forced phot.:   10 epochs
  MJD range:      61135.0 – 61179.0
  Bands seen:     ['g', 'i', 'r', 'z']
  Mag range:      20.85 – 23.70
  Reliability:    0.500 – 1.000
  SNR:            10.518 – 68.204
```

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

**Completeness issue tokens** (appear in `completeness_issues` and `flag`): `no_detections`, `no_magstats` (ZTF only — see below), `ndet_lt_2`, `coordinates_missing`, `mag_null`, `rb_absent`, `drb_absent` (ZTF only), `no_classification`, `fetch_error_<field>`

`no_magstats` is deliberately **not** emitted for LSST. ALeRCE raises `NotImplementedError` for every LSST object, so the token fired on 100% of rows — and since any completeness issue forces `FLAG`, it made every LSST object FLAG and hid its actual verdict, including the clean two-classifier `PASS` that LSST probabilities do support. A condition that is always true of a survey describes the survey, not the object. It comes back if ALeRCE ships LSST magstats.

**Classification flag tokens** (appear in `flag` only):

- ALeRCE: `insufficient_classifiers`, `minor disagreement: ...`, `genuine split: ...`, `no_classification`, `no_ranking1_rows`, `zero_total_weight`
- ANTARES: `no_science_tags (pipeline: [...]; unknown: [...])`, `minor: N science tags — ...`, `multiple science tags (N): ...`

---

## Classification

### ALeRCE

Weighted consensus across all classifiers. Each vote is weighted by:

- **Method** — `lc_classifier` outweighs `stamp_classifier`; the gap widens as `ndet` grows (lc data becomes more informative)
- **Confidence** — the classifier's own probability for its top class
- **Recency** — small tiebreaker from the classifier version (a string on ZTF, `"1.0.0"`; an integer on LSST, `201`)

| Condition | Status |
|---|---|
| < 2 classifiers voted | `FLAG` (insufficient_classifiers) |
| consensus ≥ 0.90 | `PASS` |
| consensus ≥ 0.65, all dissenters < prob 0.30 | `REVIEW_MINOR` |
| otherwise | `REVIEW_MAJOR` |

### ANTARES

Tags are filtered into science vs pipeline sets before scoring. The 19 science tags (e.g. `dimmers`, `nuclear_transient`, `extragalactic`) count toward consensus; the 19 pipeline/infrastructure tags (e.g. `lc_feature_extractor`, `high_snr`, `in_LSSTDDF`) are stripped. Unknown tags are flagged explicitly rather than silently ignored.

`top_class` reports all science tags present, sorted (e.g. `"dimmers, extragalactic"`). Consensus = 1/n_science_tags.

| Tags present | Consensus | Status |
|---|---|---|
| One science tag | 1.0 | `PASS` |
| Two or more science tags | ≤ 0.50 | `REVIEW_MAJOR` |
| Only pipeline/unknown tags | — | `REVIEW_MAJOR`, flagged `no_science_tags` |
| No tags at all | — | `FLAG` (completeness: `no_classification`) |

Note: `REVIEW_MINOR` is unreachable for ANTARES. Consensus is 1/n, so two tags already score 0.50 — below the 0.65 majority threshold. The tier would only open up if consensus stopped being a flat reciprocal (e.g. weighting tags by reliability).

A locus carrying only pipeline tags is *not* FLAG: `validate_antares` sees a non-empty tag list, so no completeness issue fires and the row lands in `REVIEW_MAJOR` with the `no_science_tags` flag naming the pipeline and unknown tags. Only a locus with zero tags reaches `FLAG`.

All ANTARES tags are filter outputs, not confirmed classifications — treat every tag as a candidate until followed up.

---

## Broker / Survey Support

| Feature | ZTF (ALeRCE) | LSST (ALeRCE) | ANTARES |
|---|---|---|---|
| Detections | ✓ | ✓ | ✓ (alerts, upper limits filtered out) |
| Magstats | ✓ | — (falls back to raw detections) | ✓ (locus properties) |
| Classifiers | ✓ | ✓ (2 stamp classifiers; `query_classifiers`/`query_classes` still absent) | ✓ (tag-based, science tags only) |
| `rb`/`drb` scores | ✓ | `reliability` only | — (pre-filtered upstream, rb ≥ 0.55) |
| Catalog cross-matches | — | — | ✓ (Gaia, Sloan, WISE, Chandra) |
| Real-time stream | — | — | ✓ (Kafka, requires credentials) |
| Profiler | ✓ | ✓ | ✓ |

---

## Project Structure

```
src/rubin_qa/
    config.py          — constants and thresholds
    retry_budget.py    — run-wide retry sleep budget, shared by both clients
    client.py          — ALeRCE API wrapper with retry
    antares_api.py     — ANTARES API wrapper
    validators.py      — validate_completeness, validate_antares
    classifier.py      — classify_object (weighted consensus), classify_antares (tags)
    reporting.py       — QA row assembly and pipeline orchestration (ALeRCE + ANTARES)
    profiler.py        — single-object diagnostic tool (all three surveys, own CLI)
    photometry.py      — broker-neutral photometry records (ANTARES, ALeRCE)
    sky_catalog.py     — Legacy Surveys DR10 + Siena Galaxy Atlas host matching, cached per catalogue version
    transient_monitor.py — new-transient scan of a sky cone (own CLI)
    replay.py          — the monitor replayed day by day over past dates (transient_monitor --replay)
    __main__.py        — CLI entry point
pipeline.py            — backwards-compatible shim
tools/                 — on-demand measurement scripts, outside the monitor's imports:
    host_match_check.py  — host-matching validation (known hosts vs random positions)
    pa_from_pixels.py    — galaxy position angles from cutout pixels (source of the ellipse fixtures)
    atlas_query_contract.py — checks the test fake's q3c reading and the atlas join against Data Lab
    lsst_box_census.py   — every LSST object at ALeRCE per test box, 2026-02-15 → 07-14
    replay_tns_check.py  — a replay's alerts against TNS, per survey (throttle-aware lookups)
tests/                 — pytest, mock data only
pyproject.toml
```

---

## Tests

```bash
uv run pytest tests/ -v
uv run pytest tests/ --cov=rubin_qa --cov-report=term-missing
```

All tests use mock data — no live API calls.

| File | Covers |
|---|---|
| `test_client.py` | ALeRCE client: retry, dedup, per-field fetch errors |
| `test_antares.py` | ANTARES broker path, end to end (see below) |
| `test_validators.py` | completeness tokens, ZTF and LSST |
| `test_classifier.py` | weighted classifier consensus |
| `test_reporting.py` | QA row assembly, status tiers |
| `test_ceilings.py` | request timeout, retry budget, run deadline |
| `test_main.py` | CLI argument routing, survey validation, CSV naming, exit codes |
| `test_profiler.py` | profiler: per-broker branches, derived band tables, its own CLI |
| `test_tools.py` | `tools/sample_latency.py` — pooling, boot filtering, failure reporting |
| `test_photometry.py` | broker-neutral fetch layer: survey objects, ANTARES locus splitting, ALeRCE fallback |
| `test_transient_monitor.py` | monitor paths (bracketed, rising, rapid rise, re-brightening), the decision table (every path × sky context through `evaluate`; an unmapped context raises), re-reporting on the (category, path) pair, state, footprint atlas loading and its outage flags |
| `test_replay.py` | replay: listing in chunks, light-curve cache (failures never cached), day slicing (the future never leaks into a day), the span cut's equivalence on synthetic data, catalogue prefetch, suspend-aware stage timer |
| `test_sky_catalog.py` | DR10 host matching: ellipse conventions pinned to pixel-measured fixtures, atlas pieces (NGC 873), two-step candidates and the margin (with a q3c-reading fake TAP service, `tests/fake_tap.py`), catalogue gaps, cache rules (no failure or empty answer ever cached) |
| `test_host_match_check.py` | `tools/host_match_check.py` — every fetch-failure path fed its failure (empty TNS record, Sesame down, tile and atlas errors) must come back as "fetch error" or pending, never "no host"; the TNS throttle rule against a simulated throttled service at every window phase; truth mapping; mixed-version detection |
| `test_asteroid_probe.py` | `tools/asteroid_probe.py` — the evidence behind the monitor's asteroid rules |

**ANTARES coverage.** The ALeRCE path had unit tests from the start; the ANTARES
path had only the ceiling and CLI tests, which drive the loop but never the locus
model inside it. `test_antares.py` closes that: 53 tests taking
`src/rubin_qa/antares_api.py` from 87% to 100% statement coverage, and covering
`validate_antares`, `classify_antares`, `build_antares_qa_row` and
`run_antares_pipeline` alongside it. The cases are chosen where ANTARES *differs*
from ALeRCE and a wrong answer would be silent rather than loud:

- **ANT vs ZTF id routing** — `get_by_id` and `get_by_ztf_object_id` are not
  interchangeable, and only the ZTF path reports a missing object as `not_found`.
- **Upper-limit filtering** — `ztf_upper_limit` alerts sit in `locus.alerts` with no
  `ant_mag`. Left in, they become detections that never happened.
- **`num_mag_values` over `len(dets)`** — ANTARES applies quality cuts the raw alert
  stream does not reflect, so the row count is the *higher*, wrong number. Nothing
  raises if the wrong one is used; every `ndet` in the report is simply inflated.
- **Science vs pipeline tags** — a locus tagged `nuclear_transient` +
  `lc_feature_extractor` + `high_snr` has one classification, not three. Unknown tags
  are asserted to surface by name rather than vanish into "no classification".
- **Per-field degradation** — `alerts`, `properties` and `tags` are fetched under
  separate exception handlers, so one unusable field must cost a column and not the
  row. Tested with a locus whose attribute access raises, through to the `FLAG` row
  that comes out the far end.
- **REVIEW_MINOR is asserted unreachable.** ANTARES consensus is 1/n, so two tags give
  0.50, under the 0.65 majority threshold — the tier cannot fire. That is a known,
  accepted dead branch; the test pins it so a threshold change surfaces as a failure
  instead of a silent behaviour change.

Each new test was checked by mutation: the upper-limit filter, the `num_mag_values`
preference, the science-tag filter, the pacing delay, the `ImportError` message and
each degradation handler were broken in turn, and the suite was confirmed to fail on
every one. A test that passes against broken code is not coverage.

---

## Deployment

`systemd/` holds example user units — one `.service` + `.timer` pair per job, all
named `lsst-<role>` so `systemctl --user list-timers 'lsst-*'` and
`journalctl -t 'lsst-*'` sweep the whole set:

| Unit | Cadence | Runs | Status |
|---|---|---|---|
| `lsst-pipeline-antares` | daily | `pipeline.py antares 256 -q` | active |
| `lsst-pipeline-alerce` | weekly | `pipeline.py lsst 100` | active |
| `lsst-sso-monitor` | daily | `antares_sso_monitor.py` | **retired 2026-08-18** |
| `lsst-latency-sample` | hourly, randomized | `tools/sample_latency.py --quick --quiet` | **retired 2026-09-01** |

The two investigative units are done and disabled — the monitor because its premise
was falsified, the sampler because it settled the constants it was collected for.
Their `.example` files and setup instructions stay in `systemd/` as the deployment
record; the scripts behind them still run by hand. Nothing in the repo re-enables
them.

Install: copy the pair, drop the `.example` suffix, replace `/path/to/...`, then
`systemctl --user daemon-reload && systemctl --user enable --now <unit>.timer`.

Every service carries the same sandboxing block (`ProtectSystem=strict` +
`ReadWritePaths` on the project directory, syscall filter, no new privileges). Output
goes to the journal; `lsst-sso-monitor` was the one exception, keeping its own
`logs/sso_monitor.log` as the production record for as long as it ran. See
[`systemd/README.md`](systemd/README.md)
for per-unit rationale, the directive-by-directive breakdown, and the gotchas
(`ReadWritePaths` is mandatory under `strict`; `ProtectHome` must stay unset for `uv`).

The long-run confirm prompt only fires on a TTY — under systemd there is no stdin, so
the estimate is logged to stderr and the run proceeds rather than hanging the unit.

---

## Broker Latency Sampling Campaign

> **Concluded 2026-09-01** — 329 samples over 21 days; the hourly timer is retired. The constants it was collected to check turned out to be right, so nothing changed. That is the result, not the absence of one.

`SECONDS_PER_OBJECT` drives the runtime estimate, the run deadline and the long-run
confirm prompt. It was seeded on 2026-08-06 from a single afternoon's measurement,
which put ZTF at 17.0s per object. A constant sized off one sitting is a guess; the
campaign was run to find out whether it was a baseline or an episode.

**Method.** `tools/sample_latency.py` appends one record per run to
`logs/latency_samples.jsonl`, timing each client call directly with no retry wrapper.
Collection was hourly with `RandomizedDelaySec=3600`, so each sample landed somewhere
inside its hour rather than at the same minute of every hour — a fixed offset cannot
separate time-of-day from load. Every individual object timing is kept, so `--report`
pools across runs and sample size does not depend on how often anyone remembers to
run it. Each record also stores the constants in force when it was taken, so changing
them mid-campaign does not corrupt the history. Broker failures are recorded as
samples rather than aborting the run.

**Result — the constants stand.** Per-run medians, including the inter-object delay,
which is what the constant has to cover:

| survey | p50 | p95 | p99 | worst | `SECONDS_PER_OBJECT` |
|---|---|---|---|---|---|
| ztf | 3.38s | 3.76s | 3.84s | 4.31s | **3.7s** |
| lsst | 2.19s | 2.38s | 2.50s | 2.71s | **2.4s** |
| antares | 0.93s | 1.36s | 1.45s | 1.53s | **1.0s** |

Each constant sits at or above its survey's p95, so a typical run finishes inside its
own estimate and the deadline only trips on something genuinely stuck. **No change
was made.**

**The 17.0s figure was an episode.** It never recurred in 21 days of hourly sampling.
It survives only as `DEFAULT_SECONDS_PER_OBJECT`, the fallback for a survey with no
measurement — kept deliberately pessimistic, since an unmeasured broker is likelier
to be slow than fast, and now explicitly a judgement call rather than a measurement.

**Three hypotheses tested and dropped.** Latency is flat by hour (3.19-3.61s across
the 19 hours sampled), flat by weekday, and free of collection bias (timer runs 3.34s
vs. manual 3.41s) — so the hourly cadence, which existed to catch a diurnal pattern,
was measuring something that is not there. Hours 02-06 are empty because the machine
is off overnight and no run is ever issued then; that is a property of the schedule,
not a gap in the result.

**What the campaign did find** is that the broker swings on its own timetable: 12
failed fetches across 329 runs. Eleven are ZTF `ReadTimeout` at the full 60s
`REQUEST_TIMEOUT` — about 3.4% of ZTF runs — clustered 08-12→08-16 and 08-31 rather
than spread evenly; the twelfth is a 2026-08-13 event where ZTF *and* LSST both
returned `APIError: 500`, the only broker-wide blip in the window. At that rate a
failure is a normal Tuesday, which is the argument for ceilings that degrade rather
than abort: a run has to survive one and still write its CSV.

It also priced the call that `SECONDS_PER_OBJECT` does not model at all — the one-off
candidate fetch, the slowest single query in the system. ZTF `query_objects` runs a
median 19.3s against the 60s `REQUEST_TIMEOUT`, with a worst case of 45.4s, and is no
faster at `page_size=10` than at 100: server-side variance dominates and page size
barely matters. That call, not the per-object ones, is what keeps `REQUEST_TIMEOUT`
at 60s.

**Re-measuring.** The unit examples are kept in `systemd/`; re-enable the pair after a
broker change or when a fourth survey is added, and let it run for weeks rather than
days — the useful signal here was the failure clustering, which a short run would have
missed entirely. `uv run tools/sample_latency.py --report` still reads the collected
log; `--by-hour` and `--exclude-boot` break it down. Note `logs/` is gitignored, so
the samples are local-only. For a one-off, `tools/bench_latency.py` times 10 objects
per survey over two rounds without the retry wrapper.

---

## Replay Box Census (transient monitor)

> **2026-10-01** — the LSST alert stream stopped on 2026-07-14, so the monitor's Rubin path can only be exercised by replaying past dates. Before choosing where and when to replay, every LSST object ALeRCE holds for each candidate box was counted, 2026-02-15 → 07-14. Summary per box and month: [`reports/lsst_box_census_20261001.csv`](reports/lsst_box_census_20261001.csv).

**Method.** ALeRCE only searches cones, so each box was fetched in 2.5° cells, each as the cone around it trimmed to the cell. Queries were cut into one-week chunks by first detection, checkpointed, and resumable. Two properties of the listing were checked before trusting the counts:
- It returns one row per object *per classifier*. Filtering to one classifier gives one row each, with 0 objects missing in four test windows.
- Its paging is deterministic. Two walks and an oid-ordered walk of the same query returned identical sets.

Every object is kept, so any other tiling is a re-read, not a refetch (`tools/lsst_box_census.py`).

**Result — box D (COSMOS) stays, replayed in March–May; RA 240–255, Dec −25..−15 is rejected.**

| box | Feb 15–28 | Mar | Apr | May | Jun | Jul 1–14 |
|---|---|---|---|---|---|---|
| D: new objects | 322,900 | 1,992 | 4,926 | 2,722 | 547 | 0 |
| D: ≥ 3 detections over > 1 night | 41,687 | 57 | 175 | 92 | 5 | 0 |
| RA 240–255 (6 tiles): new objects | 0 | 0 | 0 | 119,625 | 150,492 | 187,358 |

- *Box D has a first-look burst.* On the first nights of the COSMOS season (02-16 → 02-25) everything already variable in the field became a "new" object at once: 323k in two weeks, mostly classed bogus, variable star or AGN. A replay date inside that burst would drown in it; March–May is the quiet, usable part. Rubin covered only a ~1.75° disc of the box, the deep-drilling pointing, about 10 of its 25 deg².
- *The 240–255 region looked dense, for the wrong reasons.* Rubin was only there May–July. Five of its six tiles lie partly or mostly inside the monitor's own |b| < 20° cut, and its multi-detection objects are Galactic variables by the thousand per tile-month (50–58% classed variable star in May).

---

## Host Matching Validation (transient monitor)

> **2026-10-01** — the Legacy Surveys DR10 host matcher behind the monitor's `host` / `orphan` split, validated in two directions. The threshold was set from the measurement, not copied from DES. Full report, fingerprinted with the code and inputs that produced it: [`reports/host_match_validation_20261001.txt`](reports/host_match_validation_20261001.txt).

The monitor labels each transient by what DR10 shows at its position: a host galaxy, a point source, a star, or nothing (an orphan). The host decision uses the directional light radius, d_DLR (Sullivan et al. 2006, Gupta et al. 2016): the separation divided by the galaxy's radius in the transient's direction, read from its fitted ellipse.

**1 — Conventions checked against the pixels, not the documentation.** The DR10 catalogue page gives the ellipse position angle as PA = 180 − ϕ, and the Siena Galaxy Atlas describes its own `pa` as "clockwise from North". Both are the mirror image of the sky. The code's convention (PA = ½·atan2(e2, e1), North through East) was confirmed four independent ways: second moments of the FITS cutout pixels with the WCS orientation (60 random galaxies: 92% within 15°, against 2% for the documented formula), cutouts by eye, the Tractor source (`EllipseE.getRaDecBasis`), and 609 HyperLeda position angles. The test fixtures take their expected angles from the pixels (`tools/pa_from_pixels.py`), so a "fix" that follows the docs fails 5 tests.

**2 — Faults the validation exposed in the design, and the fixes.**
- *Big galaxies.* DR10 breaks an atlas galaxy into pieces (~4–7 each, up to ~75 near a big one), and each piece competed as a host. A piece is any row inside the atlas ellipse (maskbit 12), including point-like nuclei with no reference tag: NGC 873's nucleus is a separate PSF 1.3″ from the model centre. Pieces now belong to their galaxy in every rule.
- *Hosts beyond the search radius.* SN 2025zi sits 123″ from NGC 1398, beyond the 30″ search. Candidates are now collected in two steps: Tractor sources within 30″, plus every atlas galaxy whose ellipse, scaled by the threshold, reaches the transient.
- *Catalogue gaps.* Where DR10's fitting gave up (maskbit BAILOUT; brick 0532m280 at the CDFS centre is 49% BAILOUT) no sources exist, so empty sky there says nothing. A position with BAILOUT in its 30″ search area is "unavailable", never an orphan.
- *AGN check.* The WISE colour check used to read whichever source sat nearest, before any host was known. It is now a flag on the source actually assigned, applied only when both WISE bands have S/N ≥ 5, with Stern's 0.8 Vega cut converted to 0.16 AB.

**3 — Test populations and oracles.**
- *Known hosts.* 21 classified TNS supernovae with a reported host (records via ALeRCE's TNS service, host positions from survey J-names or CDS Sesame), plus 172 DES-SN5YR spectroscopic supernovae with the host DES chose by the same method. DES measures **agreement, not truth**: its radii come from deeper stacks, and our d_DLR runs 1.39× theirs (median), so its cut does not carry over.
- *Excluded and counted, never scored as misses.* 4 pairs in catalogue gaps, 8 DES hosts below Legacy depth, 14 hosts that DR10 types as point-like, and 1 TNS record whose host redshift contradicts the SN's.
- *Random positions.* 1000 per footprint, giving the chance-host rate.
- *Self-check.* The tool's fast scoring against the production `best_host`: 0 disagreements over 3188 positions.

**4 — Result: threshold d_DLR ≤ 4** (two-step candidates, atlas radius `sma_moment`):

| d_DLR ≤ | DES agrees | DES: no host | TNS correct (of 20) | random positions given a host |
|---|---|---|---|---|
| 2 | 72% | 17% | 20 | 3.8% |
| 3 | 84% | 5% | 20 | 8.5% |
| **4** | **86%** | **1%** | **20** | **14.8%** |
| 5 | 88% | 0% | 20 | 23.5% |

The two error rates apply to populations of very different size. Hosted supernovae outnumber hostless ones about 20:1 at Legacy depth, so per 100 transients the orphan bucket is about 80% pure at 4 and about 50% at 3. A chance host only relabels the rare hostless case, which is still reported. Treat 4 as the knee plus a margin rather than a precise optimum: 1% of 146 is one or two objects. From 2.5 upward DES picks a different galaxy in a flat 11–12% of pairs, which is disagreement about *which* galaxy, not a threshold effect.

**5 — What the two-step search buys.** At threshold 4 it rescues 2 known hosts (SN 2025zi on NGC 1398, at 123″; SN 2023cr on ESO 419-003, at 36″), breaks 0, and costs 0.7–2.8% of random positions a new host.

**6 — Negative results, kept with their evidence.**
- *A compact-host rule (a point-like source 1–3″ away taken as host) was measured and dropped.* It gained 0 of 14 point-like hosts, 12 of which sit within 1″ where the point-source label already keeps them out of the orphan bucket, and it gave 10.4% of random positions a host at 3″.
- *Point source before host costs more than estimated.* About 2% of random positions (1.4–2.6%) have a star-free point source within 1″, not 0.1%. It relabels 4 correct hosts; the reverse order would instead give 4 of the 12 point-like hosts a wrong galaxy. On that tie the point source stays first: a wrong host asserts something false, while the point-source label only withholds a host, the transient is reported either way, and a faint variable star beyond Gaia's reach cannot pass as SN-like. (The 4 relabelled DES supernovae may be the SNe themselves, since DR10's stacks include DES epochs; that cannot happen for new Rubin transients.)
- *Data defects found by auditing the inputs.* ALeRCE's TNS service answers with an empty record once its quota of 10 requests a minute is spent (see Replay Validation below; at one call a second the sampler spent it itself, which first looked like intermittent faults). Before the fix, 14 of 44 supernovae were saved as "no host"; refetching recovered 8 real hosts. One host name came back URL-encoded (`2MASXJ09565234%2B0328119`) and silently failed to resolve. Every fetch-failure path now has an injected-failure test, and failed supernovae go on a pending list that the next run retries first.
- *A bug in the validator itself.* Its truth mapping first took NGC 873's point-like nucleus, 0.18″ from the Sesame position, as the host of SN 2022xjk, while the matcher had correctly chosen the galaxy (d_DLR 0.08). A named host now resolves to the atlas galaxy at that position, ahead of any nucleus or piece.
- *One position with no DR10 source within 15″ and no mask bit set:* 1 observed against 3.1 expected by chance from each position's own source density. A chance void, not a catalogue fault.

**Provenance.** Each scoring stage writes a sidecar with the SHA-256 of the code and inputs it ran on. The report prints those fingerprints in its header and opens with a MIXED VERSIONS banner if its stages disagree with each other or with the current files. That guard exists because one run did mix versions: the code was edited between its known-host and random-position stages. The published report was then rescored from the cache in a single version.

Tools: `tools/host_match_check.py` (sample, evaluate, randoms, report), `tools/pa_from_pixels.py`, `tools/atlas_query_contract.py` (the test fake's q3c reading agrees with Data Lab's: 1173 = 1173 atlas IDs, edge cut exact; the atlas join misses no atlas galaxy in any footprint).

---

## Replay Validation (transient monitor)

> **2026-10-01** — the monitor's first end-to-end run on real data. Box D (COSMOS), 2026-03-12 → 05-15, judged day by day as the live monitor would have judged it, then checked against TNS.

**Method.** A replay may filter only on what cannot change afterwards:
- *Candidates* are every LSST and ZTF object first detected in the window (plus the 14-day lookback), listed once at ALeRCE in week-long chunks.
- *The span cut.* Only objects whose detections span more than one night are kept. The cut is lossless because every monitor path now requires detections on two nights. That rule was made explicit for all paths when this design exposed `rising` and `rapid_rise` passing on a single night. An `n_det ≥ 3` cut would not have been lossless.
- *Fetching.* Each candidate's light curve is fetched once and cached. Every DR10 tile and brick the crossmatch can need is fetched before judging.
- *Judging.* Each simulated day is judged by the live code (`judge_day`) on light curves sliced to that day, with no API calls.
- *Estimate first.* The time is estimated before running, from the census counts × the measured 1.13 s per light-curve call.

A first attempt was discarded. It judged from a cold catalogue cache, fetching tiles inside the day loop, while Data Lab was slow and then down and the machine was suspended for at least an hour. Each failed lookup became "unavailable" for the rest of the run. The redo prefetches the catalogue and times every stage against a clock that excludes suspend.

**Run.** 477 candidates (381 LSST, 96 ZTF).
- Fetching was estimated at 17.7 min and took 14.3 min, plus 2.2 min of catalogue fetches.
- 0 fetch errors, 0 crossmatches unavailable, 0 s suspended.
- A rerun from the cache judges all 65 days in about a minute.

**Equivalence test** (`--compare-cuts`, 04-05 → 04-06). The same window was replayed with and without the span cut: 48 candidates against 2,200, and identical alerts. The evidence is narrow: one alert, and 2,152 objects removed by the cut, none of which would have alerted. The uncut side took 46 min against an estimated 52.

**Result — 46 alerts in 65 days** (the first day, 03-12, includes warm-up: the state starts empty). Each cell gives the TNS matches over the alerts checked; the 3 stars marked * appeared after the check.

| category · path | LSST | ZTF |
|---|---|---|
| host · bracketed (± rising) | — | 6 / 6 |
| host · rising | 18 / 22 | 0 / 1 |
| agn · rising | 2 / 6 | — |
| orphan · rising / rapid_rise | 1 / 2 | 0 / 2 (incl. 1 bracketed) |
| point_source · rising | 1 / 1 | — |
| stellar · any path | 0 / 2, plus 3* | 0 / 1 |

- *ZTF matched as expected.* All 6 ZTF objects bracketed on a host are in TNS. Five list our own ZTF ID among their internal names.
- *LSST matched far more than expected (22 of 32), and the reason is the reporting, not the selection.* TNS records every one of the 22 as discovered from Rubin data. They were reported by SGLF (15), Lasair (5) and ALeRCE (1), and 21 carry our diaObjectId as `LSST-AP-DO-<id>`. Brokers were reporting COSMOS Rubin transients systematically that spring, so the rate measures that practice.
- *TNS mostly came first.* TNS discovery preceded the alert by a median of 4.5 days, because the monitor waits for a second night and a measurable rise. Two alerts came first: SN 2026kfw by 5 days, AT 2026khr by 1.
- *The categories separate.* 18 of 22 host risers are in TNS, against 2 of 6 AGN-coloured risers and none of the stars.

**The oracle had a fault of its own, kept with its evidence.** ALeRCE's TNS service answers 10 requests per 60 s window. Once those are spent it returns empty records until the window turns, which looks exactly like "not in TNS".
- *Measured.* At one call a second, answers came back full for about 20 s and empty for about 40 s, every minute; paced at 7 calls a minute, every answer was full. The windows turned at :23 past the minute.
- *The rule.* The check never relies on that phase, since a restart can move it. It accepts an empty answer only when two (empty answer → full known-object canary) pairs fall within 60 s. At most one window turn fits in that span, so one pair shares a window, and within a window answers only go from full to empty.
- *The test.* A simulated throttled service swept over 120 window phases × 21 moments at which other users spend the quota never fools the rule. A one-pair rule and a wall-clock-minute rule both fail the sweep. So does the real rule against a window shorter than its assumed 60 s (27 of 2,520 cases at 25 s), which makes its one assumption visible.
- *The limit.* A free TNS account removes the ambiguity, through its own API key or the daily public-objects CSV used as a local snapshot. It is worth it only if TNS becomes more than display context.

**What the run changed in the monitor: the decision table.** The run surfaced a combination nobody had written down. Rising objects on Gaia stars, on AGN and on point sources were all reported as `rising`, mixed with supernova candidates.
- *Two axes.* The sky context alone now decides the category: `host`, `agn`, `orphan`, `point_source`, `stellar` or `unchecked`. The photometric path is the confidence label: `bracketed`, `rising`, `rapid_rise` or `re_brightening`. A report reads `host · rising`.
- *Visible names.* The old names `new_candidate`, `agn_flare` and `stellar_flare` claimed a path as well. The "flaring" stars rose over up to two weeks, which is stellar variability.
- *Nothing dropped.* The table has no "dropped" cell. The old rule dropped Gaia stars that did not rise, and in this window it had silently hidden 3 objects.
- *Tested.* A test runs all 5 path cases × 8 sky contexts through the monitor. An unmapped context raises an error.
- *Re-reporting.* A report is repeated when either axis changes, so an `unchecked` object is reported again once the catalogue gives a verdict.

Tools: `python -m rubin_qa.transient_monitor --footprint D --replay 2026-03-12 2026-05-15 [--estimate | --compare-cuts]`, `tools/replay_tns_check.py`. Results are kept locally in `logs/` (replay JSON and log, TNS rows with reporting groups).

---

## Known API Quirks

**ALeRCE:**
- `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- API returns duplicate oids — deduplicated in `fetch_candidates`
- LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
- LSST oids come back as integers from the API — normalized to `str` in `fetch_candidates`
- LSST `classifier_version` is an integer (201, 202) where ZTF sends a string (`"1.0.0"`); the version tiebreaker parses both
- LSST `query_lightcurve` returns **three** keys — `detections`, `non_detections`, `forced_photometry` — against ZTF's two, and `non_detections` is empty even for well-sampled objects: Rubin publishes forced photometry where ZTF publishes upper limits. Detections carry fluxes (`psfFlux`, nJy) and `band`/`band_name`, never `magpsf`/`fid`
- LSST `query_probabilities` works and returns two ranking-1 classifiers, so the consensus path actually engages — but they are the same model at two dates, so their agreement is not independent evidence the way lc-vs-stamp would be

**ANTARES:**
- Kafka streaming requires credentials (request from ANTARES team); search/fetch API is open
- `get_random_locus_ids` returns duplicates *within a single call* — deduplicated in `fetch_antares_candidates`, so a run returns 10-15% fewer loci than the requested page size (measured: 256 requested → 217-228 unique). The ES query is unseeded and the client pages with a fresh request per page, so results reshuffle mid-walk. Not a row-drop in the pipeline — one row is emitted per deduplicated ID.
- A nonexistent ANT locus ID returns HTTP 500, not 404, so it cannot be told apart from a transient fault and consumes the full retry backoff. ZTF IDs return `not_found` immediately.
- Both ANTARES fetches retry on timeout / server error (4 attempts, 9s exponential), matching the ALeRCE client. ANTARES applies its own 60s read timeout.
- Because retry is per-object, a stalled broker is bounded by three ceilings, applied to both pipelines. All degrade rather than abort — a short CSV still gets written:
  - **Per request (60s)** — forced onto the ALeRCE client, which passes no timeout of its own and would otherwise block forever. ANTARES already applies its own.
  - **Retry sleep (300s per run)** — shared budget; once spent, calls stop waiting between attempts.
  - **Run deadline — sized to the job**, at 6× the run's own estimated duration (floor 5 min). A large scan is entitled to take a long time; what trips the deadline is a run dragging far past what its size predicts. Pass `max_run_seconds` to override, or `0` to disable. The estimate comes from `SECONDS_PER_OBJECT` (ztf 3.7s, lsst 2.4s, antares 1.0s), each confirmed at or above its survey's p95 by the 2026-08/09 sampling campaign above. The 6× slack is not there for drift — latency proved stable — but to absorb one pathological object: the retry budget caps retry *sleep*, not socket time, so a worst-case ZTF object can block 3 calls × 4 attempts × 60s = 720s with only this deadline to stop it.
- The alerce package sets no request timeout anywhere, so `client._force_session_timeout()` wraps all five `requests.Session` objects the Alerce client holds.
- `locus.alerts` bundles real detections (`ztf_candidate`, have `ant_mag`) and non-detections (`ztf_upper_limit`, no `ant_mag`) — pipeline filters to `ant_mag.notna()` before building the lightcurve
- `locus.lightcurve` is a separate, tidier 14-column frame holding detections *and* upper limits together: `ant_mag` is null on a non-detection, where `ant_maglim` carries the limit. `ant_mag_corrected` is the `magpsf_corr` analogue, `ant_passband` the `fid` analogue. Also on the locus: `timeseries` (~114 columns), `catalog_objects` (crossmatch), and ~20 precomputed `feature_*` entries in `properties`
- ANTARES pre-filters alerts to rb ≥ 0.55, fwhm ≤ 5.0 px, elong ≤ 1.2 — objects in ANTARES already pass these; ALeRCE objects may not
- `antares-client` import is deferred — ALeRCE-only installs are unaffected if the package is absent

[alerce link]: https://alerce.science/
[antares link]: https://antares.noirlab.edu/
