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

It also includes a worked false-positive investigation: the SSO monitor below was run daily for four months, its alerts audited against independent ground truth, and its design assumption falsified rather than its thresholds retuned. Negative results are reported here as findings, not hidden.

---

## Bright Solar System Objects (SSO) Monitor

`antares_sso_monitor.py` is a stand-alone script which scans ANTARES daily for SSO loci that have suddenly brightened. Proof of concept / exploration. Self built _without assists from Claude Code_.

> **Negative result — and the reason it is in this portfolio.** The premise does not hold: an ANTARES locus is a sky position, not an object, so it cannot track a moving target. Every alert this monitor raised was a stationary variable star or galaxy. The script is kept unfixed because the investigation is the deliverable: a production false-positive rate traced to an invalid design assumption, tested against independent ground truth, with the residual risk quantified instead of tuned away.

First run defaults to 7-day look-back with empty magnitudes. Daily deployment automated by systemd user timer. Magnitude states of SSO loci from daily scan are stored in `logs/bright_sso_state.json`. Stores daily service log to `logs/sso_monitor.log`.

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

**Classification flag tokens** (appear in `flag` only):

- ALeRCE: `insufficient_classifiers`, `minor disagreement: ...`, `genuine split: ...`, `no_classification`, `no_ranking1_rows`, `zero_total_weight`
- ANTARES: `no_science_tags (pipeline: [...]; unknown: [...])`, `minor: N science tags — ...`, `multiple science tags (N): ...`

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
    retry_budget.py    — run-wide retry sleep budget, shared by both clients
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
| `test_main.py` | CLI argument routing, CSV naming, exit codes |

**ANTARES coverage.** The ALeRCE path had unit tests from the start; the ANTARES
path had only the ceiling and CLI tests, which drive the loop but never the locus
model inside it. `test_antares.py` closes that: 53 tests taking
`src/rubin_qa/antares_client.py` from 87% to 100% statement coverage, and covering
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

| Unit | Cadence | Runs |
|---|---|---|
| `lsst-sso-monitor` | daily | `antares_sso_monitor.py` |
| `lsst-pipeline-antares` | daily | `pipeline.py antares 256 -q` |
| `lsst-pipeline-alerce` | weekly | `pipeline.py lsst 100` |
| `lsst-latency-sample` | hourly, randomized | `tools/sample_latency.py --quick --quiet` |

Install: copy the pair, drop the `.example` suffix, replace `/path/to/...`, then
`systemctl --user daemon-reload && systemctl --user enable --now <unit>.timer`.

Every service carries the same sandboxing block (`ProtectSystem=strict` +
`ReadWritePaths` on the project directory, syscall filter, no new privileges). Output
goes to the journal; `lsst-sso-monitor` is the one exception, keeping its own
`logs/sso_monitor.log` as the production record. See [`systemd/README.md`](systemd/README.md)
for per-unit rationale, the directive-by-directive breakdown, and the gotchas
(`ReadWritePaths` is mandatory under `strict`; `ProtectHome` must stay unset for `uv`).

The long-run confirm prompt only fires on a TTY — under systemd there is no stdin, so
the estimate is logged to stderr and the run proceeds rather than hanging the unit.

---

## Known API Quirks

**ALeRCE:**
- `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- API returns duplicate oids — deduplicated in `fetch_candidates`
- LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
- LSST oids come back as integers from the API — normalized to `str` in `fetch_candidates`

**ANTARES:**
- Kafka streaming requires credentials (request from ANTARES team); search/fetch API is open
- `get_random_locus_ids` returns duplicates *within a single call* — deduplicated in `fetch_antares_candidates`, so a run returns 10-15% fewer loci than the requested page size (measured: 256 requested → 217-228 unique). The ES query is unseeded and the client pages with a fresh request per page, so results reshuffle mid-walk. Not a row-drop in the pipeline — one row is emitted per deduplicated ID.
- A nonexistent ANT locus ID returns HTTP 500, not 404, so it cannot be told apart from a transient fault and consumes the full retry backoff. ZTF IDs return `not_found` immediately.
- Both ANTARES fetches retry on timeout / server error (4 attempts, 9s exponential), matching the ALeRCE client. ANTARES applies its own 60s read timeout.
- Because retry is per-object, a stalled broker is bounded by three ceilings, applied to both pipelines. All degrade rather than abort — a short CSV still gets written:
  - **Per request (60s)** — forced onto the ALeRCE client, which passes no timeout of its own and would otherwise block forever. ANTARES already applies its own.
  - **Retry sleep (300s per run)** — shared budget; once spent, calls stop waiting between attempts.
  - **Run deadline — sized to the job**, at 6× the run's own estimated duration (floor 5 min). A large scan is entitled to take a long time; what trips the deadline is a run dragging far past what its size predicts. Pass `max_run_seconds` to override, or `0` to disable. The estimate comes from `SECONDS_PER_OBJECT` (ztf 3.7s, lsst 2.4s, antares 1.0s, measured 2026-08-11); broker latency has moved by 4-5× week to week, so the 6× slack is what keeps a slow-but-healthy run from being truncated between re-measurements.
- The alerce package sets no request timeout anywhere, so `client._force_session_timeout()` wraps all five `requests.Session` objects the Alerce client holds.
- `locus.alerts` bundles real detections (`ztf_candidate`, have `ant_mag`) and non-detections (`ztf_upper_limit`, no `ant_mag`) — pipeline filters to `ant_mag.notna()` before building the lightcurve
- ANTARES pre-filters alerts to rb ≥ 0.55, fwhm ≤ 5.0 px, elong ≤ 1.2 — objects in ANTARES already pass these; ALeRCE objects may not
- `antares-client` import is deferred — ALeRCE-only installs are unaffected if the package is absent

[alerce link]: https://alerce.science/
[antares link]: https://antares.noirlab.edu/
