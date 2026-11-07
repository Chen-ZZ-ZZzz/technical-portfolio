# LSST Alert Data Quality Pipeline

A data quality pipeline for LSST (and ZTF) alert data via the [ALeRCE][alerce link] and [ANTARES][antares link] brokers. Fetches objects, validates completeness, and produces a QA report with weighted classifier consensus. In the development, more tools and a few spin-offs are added to the project; the most recent is the transient monitor watching for new transients in a fixed region.

---

## The Alert Ecosystem

The [Vera C. Rubin Observatory](https://rubinobservatory.org/) is an astronomical observatory in Chile. Its main task is to conduct an astronomical survey of the southern sky every few nights, creating a ten-year time-lapse record, termed the **Legacy Survey of Space and Time (LSST)**.

The telescope would generate up to 10 million alerts per night, about objects that have changed brightness or position relative to archived images. The alerts are immediately available to the public, via alert streams from external "_event brokers_".

The Zwicky Transient Facility (ZTF) serves as a prototype of the system, generating 1 million alerts per night.

**ALeRCE** (Automatic Learning for the Rapid Classification of Events) is a Chilean-led Community Broker. Its current support of LSST is clean and mostly full.

**ANTARES** (Arizona-NOIRLab Temporal Analysis and Response to Events System) is an NSF NOIRLab broker that processes ZTF alerts and is approved for the full LSST stream. Unlike ALeRCE's probability-based classifiers, ANTARES uses discrete _tags_ produced by Python filters. Each tag is a science signal (e.g. `nuclear_transient`, `dimmers`) or a pipeline annotation. The search API is open; real-time Kafka streaming requires credentials. ANTARES _started ingesting LSST alerts_ Feb 24, 2026.

---

## Context

Built as a QA engineering showcase using real astronomical alert data from the Vera C. Rubin Observatory (LSST) and the Zwicky Transient Facility (ZTF). Rubin's alerts became public in February 2026; the survey itself started on June 30, 2026. Development assisted by Claude Code.

The validation patterns include completeness checks, classifier consensus, threshold tuning, and structured reporting. This mimics sensor data validation in HIL/SIL test environments.

It also includes:

The [Bright SSO Monitor](#bright-solar-system-objects-sso-monitor) — a worked false-positive investigation run daily for four months; now frozen because its design assumption was falsified

The [Broker-Latency Sampler](#broker-latency-sampling-campaign) — latency sampling campaign successfully concluded; now dormant but maintained and ready to re-arm

The [Transient Monitor](#transient-monitor) — supernova, tidal disruption event watcher; active

---

## Built With

- Python 3, pandas, pytest
- [ALeRCE broker][alerce link] API and Python client
- [ANTARES broker][antares link] and `antares-client`
- Claude Code (AI-assisted development)

---

## Install

Requires Python 3.13. Uses `uv` for package management.

```bash
git clone <repo>
cd <repo>
uv sync
```

Dev dependencies (pytest) are included; uv sync handles everything

---

## Transient Monitor

`rubin_qa.transient_monitor` looks for new transients in a fixed cone of sky: supernovae, tidal disruption events, and orphans with no visible host, and observes their characteristics over a time period. It is designed broker-neutral; a broker is only where the photometry comes from. The monitor makes decisions following in-house developed scientific and procedural rules, on the light curve of each survey object (a ZTF objectId, an LSST diaObjectId). Broker classifications are printed for context.

> **Active since 2026-10-03**, on the Extended Chandra Deep Field South (ECDFS). It runs once a day on ZTF and LSST data from ALeRCE. The LSST alert stream was silent from 2026-07-14 into October 2026 (new detections by 2026-10-30); meanwhile the Rubin path was exercised by replaying past dates ([Replay Validation](#replay-validation-transient-monitor), below).

### Two Labels per Alert, Judged apart

The *path* says why the object counts: `bracketed` (a recent first detection, with a quiet point shortly before it, deep enough to have seen it), `rising` (a recent onset that is still brightening; the main path for LSST, whose photometry never starts before the first detection), `rapid_rise` (a fast jump), and `re_brightening` (the same jump on an old object, seen on two nights).

The *sky context* says what the Legacy Surveys DR10 catalogue shows at the position: `host`, `orphan`, `point_source`, `stellar`, `agn`, or `unchecked` where the catalogue has a gap or cannot be reached.

Two things are filtered out before any judgement: the Galactic plane, and objects LSST itself associates with an asteroid. A report line reads `host · rising`. Nothing is dropped on the sky context. (A rising star is reported as `stellar`, not removed) An object is reported again when its pair of labels changes, for example when `unchecked` becomes a real verdict.

### How It Is Built

The pipeline's QA habits are applied to the detector.

*No state is an error, not a first run.* The monitor reports what changed since its last report. From an empty state everything that qualifies would look new, so starting from empty takes a deliberate `--init`.

*An empty listing is checked before it is believed.* With Rubin off sky, an empty LSST listing is normal for weeks, and ALeRCE's listing can also come back empty when it has failed. Each empty listing is compared with the newest object ALeRCE holds in the cone, which tells "quiet" from "failed". A failure exits 1.

*A lookup that could not be made is never an answer.* Where DR10 has a gap or cannot be reached, the verdict is `unchecked`, never `orphan`. A TNS lookup that failed says so and is asked again.

*A rejection names its reason.* Every object left out carries the criterion that left it out.

*One event, one alert.* When the second survey detects an event already reported, it becomes a note on the first alert.

*A known weakness is watched, not assumed away.* Every `point_source` alert is followed in TNS. If spectroscopic supernovae keep turning up on faint point sources, the report says it is time to measure the point-source rule again.

### Tools for Evidence

Each rule traces to a section below:

- [Replay Box Census](#replay-box-census-transient-monitor): where and when the LSST path could be replayed.
- [Host Matching Validation](#host-matching-validation-transient-monitor): the DR10 host matcher, tested on known supernova hosts and on random positions.
- [Replay Validation](#replay-validation-transient-monitor): two months of COSMOS replayed day by day by the live code, then checked against TNS.
- [Asteroid Probe](#asteroid-probe-transient-monitor): why every path needs detections on more than one night.

### How to Use

```bash
# today's report against the saved state; nothing saved
uv run python -m rubin_qa.transient_monitor --dry-run

# price a replay
uv run python -m rubin_qa.transient_monitor --footprint D --replay 2026-03-12 2026-05-15 --estimate
```

Each live or `--init` run also writes its report as JSON, `reports/transient_<footprint>_<timestamp>.json`; what it holds is in `scan`'s docstring. `--dry-run` writes none; a replay writes its results to `logs/`.

The daily unit and its setup: [`systemd/README.md`](systemd/README.md).

---

## What the Pipeline Does

- **Completeness validation** — checks for missing detections, null magnitudes, absent real/bogus scores, sparse observations, and API fetch failures

- **Weighted classifier consensus** — aggregates votes from up to 24 independent classifiers, weighting by method relevance (light curve vs stamp), confidence, and model recency

- **ANTARES tag classification** — maps discrete science tags to the same verdict schema, filtering out pipeline/infrastructure tags before scoring

- **Survey-aware checks** — adapts validation rules for ZTF (mature, data-rich) vs LSST (sparser broker support, some endpoints unimplemented), with graceful degradation for unimplemented API endpoints, and without letting a structurally-absent endpoint masquerade as a data defect

- **Single-object profiler** — a diagnostic deep-dive on any of the three brokers, deriving per-band photometry where the broker serves no magstats

- **Structured QA reporting** — each object gets a tiered status (PASS / REVIEW_MINOR / REVIEW_MAJOR / FLAG) with detailed flags explaining why

---

## Pipeline Usage

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

# LSST — fetch N objects; N ≤ 200: the LSST listing's page limit
uv run pipeline.py lsst 196

# ANTARES — the 20 most recently updated loci
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

# ANTARES: the most recently updated loci
df = run_antares_pipeline(page_size=20)

# ANTARES explicit locus IDs or ZTF object IDs
df = run_antares_pipeline(locus_ids=["ANT2020j7wo4", "ZTF20aafqubg"])

# Deadline defaults to DEADLINE_SLACK x the job's own estimate (config.py); override or disable it
df = run_antares_pipeline(page_size=256, max_run_seconds=900)
df = run_antares_pipeline(page_size=1000, max_run_seconds=0)   # no deadline; 1000 is the most the listing takes

# Inspect the sizing without running
from rubin_qa.reporting import estimate_runtime, deadline_for
estimate_runtime(1000, "ztf") / 60   # minutes, from SECONDS_PER_OBJECT
deadline_for(1000, "ztf") / 60       # minutes, DEADLINE_SLACK x that, at least DEADLINE_FLOOR

print(df[["oid", "top_class", "consensus", "status"]])
```

### Diagnostic Profiler

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

  Verdict:     SN  (best prob 0.985, weighted consensus 100%)
  Classifiers: 1/1 vote for 'SN'
  Families:    voting stamp_classifier_rubin_beta; compared stamp_classifier_rubin_beta

-- Light curve --
  Detections:     60 epochs
  Non-detections: 0 upper limits
  Forced phot.:   79 epochs
  MJD range:      61135.0 – 61179.0
  Bands seen:     ['g', 'i', 'r', 'z']
  Mag range:      20.85 – 23.70
  Reliability:    0.500 – 1.000
  SNR:            10.518 – 68.204
```

---

## Pipeline QA Report

Reports are saved to `reports/qa_{run}_{timestamp}_n{count}.csv` after each run, where `{run}` is `alerce_ztf`, `alerce_lsst` or `antares` and `{timestamp}` is a local time; the CLI's header, report heading and summary line name the run the same way (`ALeRCE ZTF`, `ALeRCE LSST`, `ANTARES`). The CLI also prints a summary table (`oid`, `ndet`, `top_class`, `consensus`, `n_classifiers`, `status`), the newest detection per survey among the objects checked, aged from the run's start (`newest ZTF of the 229 checked: 2026-11-03 13:22 UTC (17.5 h)`; a sample, so no figure is a broker's newest), and the flagged count split by `flag_reasons` (`flagged=196/196 (insufficient_classifiers 196, ndet_lt_2 115, mag_null 4)`). **Two report formats, told apart by the `run_utc` column:** a QA CSV with it is named at the run's start (the same moment as `run_utc`) and carries the columns the split and the newest line are read from, so it reproduces both; a CSV without it is named at the run's finish and lacks them.

One row per object. Columns:

| Column | Description |
|---|---|
| `run_utc` | The run's start in UTC, the same on every row: the time of record |
| `broker` | `alerce` or `antares` |
| `survey` | `ZTF` or `LSST`: for ALeRCE the survey the run queried, for ANTARES the survey of the locus's newest alert (empty when its alerts did not say). Counted, it is the run's survey split, the input of the comparability rule (CLAUDE.md, [QA pipeline](CLAUDE.md#qa-pipeline)) |
| `oid` | Object identifier |
| `ndet` | Total detection count |
| `mag_range` | Brightness amplitude: magmax − magmin (ANTARES: the faintest minus the brightest `ant_mag` of the detections) |
| `timespan_days` | Last − first detection epoch |
| `newest_ztf_mjd` / `newest_lsst_mjd` | The row's newest detection of that survey, from the same detections as `timespan_days` (ANTARES: those `mag_range` uses, so never an upper limit); an ALeRCE row fills only its own survey's |
| `unknown_survey_dets` | ANTARES: detections whose `ant_survey` code names no known survey, counted rather than dropped; ALeRCE: 0 |
| `top_class` | ALeRCE: plurality class from weighted consensus; ANTARES: all science tags, sorted and joined |
| `class_prob` | Best classifier probability for `top_class` |
| `consensus` | Weighted consensus score [0, 1] |
| `n_classifiers` | ALeRCE: live model families compared, one vote each ([Independent Evidence in ALeRCE's Classifiers](#independent-evidence-in-alerces-classifiers)); ANTARES: science tags present |
| `n_agree` / `n_disagree` | Votes for/against the plurality class |
| `confirmed` | `True` if ndet > 1 |
| `has_issues` | `True` if any completeness issues |
| `completeness_issues` | List of issue tokens (see below) |
| `flag` | Combined flag string, or `None` |
| `flag_reasons` | One fixed key per reason in `flag`, each once; empty exactly when `flag` is `None` |
| `status` | `PASS` / `REVIEW_MINOR` / `REVIEW_MAJOR` / `FLAG` |

**Status tiers (evaluated in order):**

| Status | Condition |
|---|---|
| `FLAG` | Any completeness issues, a classifier the registry does not list (`unknown_classifier: …`), or fewer than 2 comparable families (`insufficient_classifiers`) |
| `PASS` | No issues, consensus ≥ 0.90 across ≥ 2 comparable families |
| `REVIEW_MINOR` | No issues, consensus ≥ 0.65, all dissenters below prob 0.30 |
| `REVIEW_MAJOR` | No issues, genuine classifier split — needs human inspection |

Note: `PASS` and both `REVIEW` tiers need two comparable families, and since 2026-11-03 ALeRCE offers none on LSST or ZTF, so every ALeRCE row is `FLAG` ([Independent Evidence in ALeRCE's Classifiers](#independent-evidence-in-alerces-classifiers)).

**Completeness issue tokens** (appear in `completeness_issues` and `flag`): `no_detections`, `no_magstats` (ZTF only — see below), `ndet_lt_2`, `coordinates_missing`, `mag_null`, `rb_absent`, `drb_absent` (ZTF only), `no_classification`, `fetch_error_<field>`, `brightest_mismatch` (ANTARES only: the detections' brightest magnitude is not the server's own `brightest_alert_magnitude`, so the server counted a different set of alerts)

`no_magstats` is deliberately **not** emitted for LSST. ALeRCE raises `NotImplementedError` for every LSST object, so the token fired on 100% of rows — and since any completeness issue forces `FLAG`, it made every LSST object FLAG and hid its classification verdict. A condition that is always true of a survey describes the survey, not the object. It comes back if ALeRCE ships LSST magstats.

**Classification flag tokens** (appear in `flag` only):

- ALeRCE: `unknown_classifier: <name>@<version>`, `insufficient_classifiers`, `minor disagreement: ...`, `genuine split: ...`, `no_ranking1_rows`, `no_live_classifier` (only retired or context versions), `zero_total_weight`
- ANTARES: `no_science_tags (pipeline: [...]; unknown: [...])`, `minor: N science tags — ...`, `multiple science tags (N): ...`

**Keys in `flag_reasons`:** a completeness token is its own key, and so are `insufficient_classifiers`, `no_ranking1_rows`, `no_live_classifier` and `zero_total_weight`. The others: `unknown_classifier` (once per row, however many versions), `minor_disagreement`, `genuine_split`, `no_science_tags`, `minor_science_tags`, `multiple_science_tags`. `no_classification` is a completeness token only; a report without `flag_reasons` can name it twice, because its classifier wrote it too (`completeness: ndet_lt_2, no_classification; no_classification`).

---

## Broker Classifications

### ALeRCE

Votes are model families, not classifier rows: one vote per family, its live version's top class. Retired versions, hierarchy levels and `nuclear_classifier` never vote, and families are compared only when their class lists match exactly. The registry is `classifier.py` (`VOTERS`, `CONTEXT`), the rule CLAUDE.md [QA pipeline](CLAUDE.md#qa-pipeline), and what ALeRCE's classifiers amount to under it is in [Independent Evidence in ALeRCE's Classifiers](#independent-evidence-in-alerces-classifiers), below. Within the compared families, each vote is weighted by:

- **Method** — a light-curve classifier outweighs a stamp classifier; the gap widens as `ndet` grows (lc data becomes more informative)
- **Confidence** — the vote's own probability for its top class
- **Recency** — small tiebreaker from the classifier version (a string on ZTF, `"1.0.0"`; an integer on LSST, `202`)

| Condition | Status |
|---|---|
| a classifier the registry does not list | `FLAG` (`unknown_classifier: <name>@<version>`) |
| < 2 comparable families | `FLAG` (insufficient_classifiers) |
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

## Independent Evidence in ALeRCE's Classifiers

> **2026-11-03** — A consensus is only as strong as the independent opinions behind it. ALeRCE returned up to 25 ranking-1 classifier rows on a ZTF object of the QA sample (the lowest ZTF IDs, all from 2017), but counted as one vote per live model family it offers no pair of families that can be compared, on LSST or on ZTF. The rule this led to: CLAUDE.md [QA pipeline](CLAUDE.md#qa-pipeline); the registry: `classifier.py` (`VOTERS`, `CONTEXT`).

**Method.**
- A *family* is one line of evidence: the same input and the same lineage of method, whatever the name or version. Hierarchy levels and earlier versions belong to their family.
- A version is *live* if it is on the newest objects, because ALeRCE classifies a new object only with what it runs today. ZTF: the 20 newest objects with at least 8 detections (first detected 2026-10-19 to 10-27). LSST: the 2638 ECDFS objects first detected after the July stop ([Known API Quirks](#known-api-quirks)). The test is now `tools/alerce_live_classifiers.py`, with its blind spot in its docstring: a live version that runs on few objects can be missed in a small sample, which errs toward fewer votes.
- Two families are compared only when their class lists match exactly. A version's class list is read from the object's own rows: every version returned a row for each of its classes, on all 40 ZTF objects (27 versions) and 196 LSST objects (3 versions) checked.
- Samples, with `query_probabilities` fetched 2026-11-03, every row at every ranking: the 2026-11-02 LSST QA run (196 objects); the 2026-11-03 ZTF QA run (225), 20 of them, stratified by status and vote count and holding all 4 PASS; the 20 newest ZTF objects.

**LSST: one live family.** `stamp_classifier_rubin_beta_20260421` (version 202) is the stamp family's live version. `stamp_classifier_rubin_beta` (201) sits only on objects first detected before the July stop, and the alerce client's 2.3.1 release notes (2026-08-07) record the older Rubin stamp classifier as retired. `nuclear_classifier` (1) is a position label, `nuclear` or `off-nuclear`, at probability 1.0: context, not a vote.

**ZTF: three live families, and no two share a class list.**

| family | live | retired, or context | live class list |
|---|---|---|---|
| stamp | `stamp_classifier` 1.0.4 and `stamp_classifier_2025_beta` 2.1.1_beta, both on 20 of the 20 newest; 2025_beta votes | 1.0.0, 1.0.1, 2.0.0_beta, 2.1.0_beta | AGN, SN, VS, asteroid, bogus; 2025_beta adds satellite |
| BHRF | `lc_classifier_BHRF_forced_phot` 2.1.0, on 8 of 20 | its betas 1.0.7–2.0.0; the alert-photometry `lc_classifier` (`hierarchical_rf_1.1.0`, `lc_classifier_1.1.13`), on none of the 20; every hierarchy level | 21 classes, with SESN |
| ATAT | `LC_classifier_ATAT_forced_phot(beta)` 1.0.0, on 8 of 20 | 0.2.0–0.3.6 | 21 classes, with SNIbc |

The live pair differs by one class: ATAT 1.0.0 has SNIbc where BHRF 2.1.0 has SESN. Matching lists pair the live ATAT only with a retired BHRF (ATAT 1.0.0 with beta 2.0.0), or retired versions with each other (ATAT 0.x with betas 1.0.7–1.1.5). Class names are not mapped onto each other: SESN (stripped-envelope) also covers IIb, so SNIbc ↔ SESN would not be exact.

**What counting every row as a vote made of it**, before 2026-11-03:
- *LSST, the 2026-11-02 run:* 117 FLAG, 79 REVIEW_MAJOR, 0 PASS. `nuclear_classifier` was on all 196 objects and counted as a dissent; its class list never overlaps the stamp's, so the highest consensus any object reached was 0.70. Without that row, 20 of the 45 objects carrying both stamp versions would have PASSed on one model's two versions agreeing.
- *ZTF, the 2026-11-03 run:* objects carried up to 25 ranking-1 rows, mostly one model counted at every hierarchy level and every version. The run's 225 are the lowest ZTF IDs ALeRCE holds, all from 2017 (ZTF17aaaaaak to ZTF17aaaaasu: the listing's default order, [Known API Quirks](#known-api-quirks)), so the 25 rows may come from old objects that carry every model version ALeRCE has run, not from a typical object. Every object with more than 2 votes was REVIEW_MAJOR (160 of 225). The hierarchy's top level made false splits: 151 of the 225 flags name a top-level class, for example "'RSCVn' 43%, 'Periodic-Other' 17%, 'Periodic' 15%". The 4 PASS all rested on two stamp votes, 2 of them two versions of `stamp_classifier`.

**Why the LSST PASS rate dropped:** 11 of 256 on 2026-09-29, 0 of 196 on 2026-11-02, 0 by construction since 2026-11-03. The 2026-09-29 PASSes rested on the two stamp versions agreeing, the only two LSST classifiers then. On 2026-11-02 the position label, counted as a vote, blocked every PASS. Since 2026-11-03 LSST has one live family, and one family cannot confirm itself.

**One vote per live family** makes every object in the samples FLAG `insufficient_classifiers`: 196 of 196 LSST, the 18 of the 20 sampled ZTF objects that carry probabilities, and 20 of the 20 newest. None of the 236 carried a classifier the registry does not list. The consensus stage stays: a new version or family arrives as `unknown_classifier` and FLAGs until the registry lists it. `tools/alerce_live_classifiers.py` then says where it goes; its first run, 2026-11-03, found the registry matching.

---

## Broker / Survey Support

| Feature | ZTF (ALeRCE) | LSST (ALeRCE) | ANTARES |
|---|---|---|---|
| Detections | ✓ | ✓ | ✓ (alerts, upper limits filtered out) |
| Magstats | ✓ | — (falls back to raw detections) | derived from the detections, checked against the server's brightest ([Known API Quirks](#known-api-quirks)) |
| Classifiers | ✓ | ✓ (2 stamp classifiers) | ✓ (tag-based, science tags only) |
| `rb`/`drb` scores | ✓ | `reliability` only | — (pre-filtered upstream, rb ≥ 0.55) |
| Catalog cross-matches | — | — | ✓ (Gaia, Sloan, WISE, Chandra) |
| Real-time stream | — | — | ✓ (Kafka, requires credentials) |
| Profiler | ✓ | ✓ | ✓ |

---

## Broker Latency Sampling Campaign

> **Concluded 2026-09-01** — 329 samples over 21 days; the hourly timer is dormant (Re-measuring, below). The constants it was collected to check turned out to be right, so nothing changed. That is the result, not the absence of one.

`SECONDS_PER_OBJECT` drives the runtime estimate, the run deadline and the long-run
confirm prompt. It was seeded on 2026-08-06 from a single afternoon's measurement,
which put ZTF at 17.0s per object. A constant sized off one sitting is a guess; the
campaign was run to find out whether it was a baseline or an episode.

**Method.** `tools/sample_latency.py` appends one record per run to
`state/latency_samples.jsonl`, timing each client call directly with no retry wrapper.
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

**No change was made.** (ANTARES, re-measured on antares-client 2.x, below.)

**What the constant is.** The seconds one object costs a run on average, the
inter-object delay included. `reporting.estimate_runtime` multiplies it by the run's
object count, and that estimate sizes the confirm prompt and the run deadline. **The
estimate leaves out the listing**, the one-off candidate fetch, and the deadline's clock
starts after it. The constant is not a ceiling: what bounds a unit's run is its
`TimeoutStartSec`, so the check is p95 × n plus the listing's worst case against that:

| unit | n | p95 per object | listing, worst | p95 × n + listing | `TimeoutStartSec` |
|---|---|---|---|---|---|
| `lsst-pipeline-alerce` | 196 | 2.38s | 20.4s | 487s | `65min` |
| `lsst-pipeline-antares` | 256 | 2.88s | 5.1s | 742s | `65min` |

The lsst p95 is the campaign's, over per-run medians (above); the antares p95 is over the
single objects of the 2026-11-03 sitting (below), the more cautious of the two. LSST's
listing: the worst of the campaign's 111 listings of 100 objects (the unit asks 196).
ANTARES's: the worst of 7 timed one-page listings of 256 (`list_loci`, 2026-11-04); the
paged listing it replaced took 42.5 s (2026-11-03). n is what the unit asks for; a paged
ANTARES listing returned fewer, its unique loci, and one page returns all n. The listing
column is measured (ANTARES 5.1 s); `tests/test_unit_sizing.py` checks the bound
instead, antares-client's own timeout per listing request.

**ANTARES re-measured, 2026-11-03**, after the move to antares-client 2.x and the v2 API
([Known API Quirks](#known-api-quirks)). One sitting of `tools/bench_latency.py --targets
antares --samples 300` (248 unique loci), timing the pipeline's own path, the locus and
then its alerts, 2 requests (the campaign had timed the locus alone). Per object, the
inter-object delay included: p50 1.69s, p95 2.88s, worst 4.56s; a lookup by ZTF ID, one
request more, p50 2.00s and p95 3.39s over 20 loci. The constant for antares is now
**1.71s**, this sitting's mean (1.7076s). It comes from one sitting, so the campaign
method (Re-measuring, below) is what would settle it.

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
failed fetches across 329 runs. Eleven are ZTF `ReadTimeout`s that ran the full
request timeout (60 s during the campaign) — about 3.4% of ZTF runs — clustered
08-12→08-16 and 08-31 rather than spread evenly; the twelfth is a 2026-08-13 event
where ZTF *and* LSST both returned `APIError: 500`, the only broker-wide blip in the
window. At that rate a failure is a normal Tuesday, which is the argument for
ceilings that degrade rather than abort: a run has to survive one and still write
its CSV.

It also priced the call that `SECONDS_PER_OBJECT` does not model at all — the one-off
candidate fetch, the slowest single query in the system. ZTF `query_objects` runs a
median 19.3s, p95 30.9s, worst case 45.4s (98 samples), and is no
faster at `page_size=10` than at 100: server-side variance dominates and page size
barely matters. That call, not the per-object ones, is what sizes `REQUEST_TIMEOUT`
(config.py): its worst case has to fit under it.

**Re-measuring.** The unit examples are kept current in `systemd/`; re-arm the pair after a
broker change or when a fourth survey is added, and let it run for weeks rather than
days — the useful signal here was the failure clustering, which a short run would have
missed entirely. `uv run tools/sample_latency.py --report` still reads the collected
log; `--by-hour` and `--exclude-boot` break it down. Note `state/` is gitignored, so
the samples are local-only. For a one-off, `tools/bench_latency.py` times 10 objects
per survey over two rounds without the retry wrapper.

---

## Bright Solar System Objects (SSO) Monitor

> **Frozen; stopped 2026-08-18** — ran daily for 115 scans, 2026-04-13 to 2026-08-17 (the last scan logged just after midnight on the 18th); its files are never edited (`tests/test_sso_frozen.py`). The [audit](#audit-2026-08-17) below is the reason: the premise was falsified, so there was nothing left to learn from another night of scanning. Kept in the repo as the investigation, not as a running detector.

`antares_sso_monitor.py` is a stand-alone script which scanned ANTARES daily for SSO loci that had suddenly brightened. Proof of concept / exploration. Self built _without assists from Claude Code_.

> **Negative result — and the reason it is in this portfolio.** The premise does not hold: an ANTARES locus is a sky position, not an object, so it cannot track a moving target. Every alert this monitor raised was a stationary variable star or galaxy. The script is kept unfixed because the investigation is the deliverable: a production false-positive rate traced to an invalid design assumption, tested against independent ground truth, with the residual risk quantified instead of tuned away.

First run defaults to 7-day look-back with empty magnitudes. Daily deployment was automated by a systemd user timer (`lsst-sso-monitor`, now frozen). Magnitude states of SSO loci from each scan were stored in `logs/bright_sso_state.json`, and the daily service log in `logs/sso_monitor.log` — the log the audit below had to reconstruct its test population from.

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

## Replay Box Census (transient monitor)

*Box-based: this census was taken in 5×5° boxes on 2026-10-01, before every footprint became a 2.82° cone (25 deg²). It is kept as it was run; the replays themselves use the cones.*

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
- *Catalogue gaps.* Where DR10's fitting gave up (maskbit BAILOUT; brick 0532m280 at the CDFS centre is 49% BAILOUT) no sources exist, so empty sky there says nothing. A position with BAILOUT in its 30″ search area is "unavailable", never an orphan. 14 of 413 cached tiles had such a gap, clustered at the CDFS centre, which lies in ecdfs, the live footprint.
- *AGN check.* The WISE colour check used to read whichever source sat nearest, before any host was known. It is now a flag on the source actually assigned, applied only when both WISE bands have S/N ≥ 5, with Stern's 0.8 Vega cut converted to 0.16 AB.

**3 — Test populations and oracles.**
- *Known hosts.* 21 classified TNS supernovae with a reported host (records via ALeRCE's TNS service, host positions from survey J-names or CDS Sesame), plus 172 DES-SN5YR spectroscopic supernovae with the host DES chose by the same method. DES measures **agreement, not truth**: its radii come from deeper stacks, and our d_DLR runs 1.39× theirs (median), so its cut does not carry over. The TNS supernovae are found through ANTARES loci, so the sample holds only what ZTF detected: bright, nearby (median z 0.045 against DES's 0.28) and often in big galaxies (40% atlas hosts against 5%). That is likely why all 20 TNS hosts are found while DES agreement is 86%: the TNS score is the easy end.
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
- *A signal-to-noise floor for the point-source rule was measured and not adopted* (2026-10-02, `tools/point_source_snr.py`). SN 2026kfw, a spectroscopic SN Ia from the replay, came out `point_source` on a DR10 point source of S/N 6.3. Measured two-sided over all 82 firings of the rule: a floor of 6.5–7 loses none of the 12 point-like true hosts (the faintest is at S/N 7.1) and lowers the random point-source rate from 2.03% ± 0.26 to about 1.7%, a change under 1.5σ. Rerun on 2026-11-04 under the rule that a band counts only with both flux and ivar measured: identical, as none of the 82 rows has an empty cell. Both edges of that window are single objects, and the cost on faint variable stars and QSOs is unmeasured, so the rule stays as it is and kfw is a known limitation. A watch looks for a pattern instead, inside the monitor run: every `point_source` alert, live or replayed, is looked up in TNS until classified, and spectroscopic supernovae on a point source below S/N 7 are counted; one whose point source has no band with both flux and ivar measured has no S/N, and is shown on the watch line, never counted. One is kfw, two can be coincidence; from three the daily report carries a notice, every run, until the rule or the threshold changes. The floor would be on S/N, never magnitude: DR10 measures r for sources detected in other bands (one host's point source reads r = 26.4 at S/N 175), and depth varies across the sky.
- *Point source before host costs more than estimated.* About 2% of random positions (1.4–2.6%) have a star-free point source within 1″, not 0.1%. It relabels 4 correct hosts; the reverse order would instead give 4 of the 12 point-like hosts a wrong galaxy. On that tie the point source stays first: a wrong host asserts something false, while the point-source label only withholds a host, the transient is reported either way, and a faint variable star beyond Gaia's reach cannot pass as SN-like. (The 4 relabelled DES supernovae may be the SNe themselves, since DR10's stacks include DES epochs; that cannot happen for new Rubin transients.)
- *Data defects found by auditing the inputs.* ALeRCE's TNS service answers with an empty record once its quota is spent ([Replay Validation](#replay-validation-transient-monitor) below, the oracle); at one call a second the sampler spent it itself, which first looked like intermittent faults. Before the fix, 14 of 44 supernovae were saved as "no host"; refetching recovered 8 real hosts. One host name came back URL-encoded (`2MASXJ09565234%2B0328119`, where `%2B` is `+`) and silently failed to resolve. Names from that service are now decoded once, where they come in, so no stored name and no later lookup sees the encoding; a literal `+` survives the decoding, and tests pin both. Every fetch-failure path now has an injected-failure test, and failed supernovae go on a pending list that the next run retries first.
- *A bug in the validator itself.* Its truth mapping first took NGC 873's point-like nucleus, 0.18″ from the Sesame position, as the host of SN 2022xjk, while the matcher had correctly chosen the galaxy (d_DLR 0.08). A named host now resolves to the atlas galaxy at that position, ahead of any nucleus or piece.
- *One position with no DR10 source within 15″ and no mask bit set:* 1 observed against 3.1 expected by chance from each position's own source density. A chance void, not a catalogue fault.

**Provenance.** Each scoring stage writes a sidecar with the SHA-256 of the code and inputs it ran on. The report prints those fingerprints in its header and opens with a MIXED VERSIONS banner if its stages disagree with each other or with the current files. That guard exists because one run did mix versions: the code was edited between its known-host and random-position stages. The published report was then rescored from the cache in a single version.

**Tools:** `tools/host_match_check.py`, run as sample → evaluate → randoms → report (`uv run tools/host_match_check.py <stage>`; sample until it exits 0, nothing pending; no edits between evaluate and randoms; after a code change, evaluate + randoms + report rescore from the cache — the tool's docstring gives the reasons), `tools/pa_from_pixels.py`, `tools/atlas_query_contract.py` (the test fake's q3c reading agrees with Data Lab's: 1173 = 1173 atlas IDs, edge cut exact; the atlas join misses no atlas galaxy in any footprint).

---

## Replay Validation (transient monitor)

> **2026-10-01** — the monitor's first end-to-end run on real data. Footprint D (COSMOS, a 2.82° cone), 2026-03-12 → 05-15, judged day by day as the live monitor would have judged it, then checked against TNS.

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

**Result — 46 detections in 65 days: 43 alerts and 3 notes** (the first day, 03-12, includes warm-up: the state starts empty). The table counts detections: each cell gives the TNS matches over the detections checked; the 3 stars marked * appeared after the check.

| category · path | LSST | ZTF |
|---|---|---|
| host · bracketed (± rising) | — | 6 / 6 |
| host · rising | 18 / 22 | 0 / 1 |
| agn · rising | 2 / 6 | — |
| orphan · rising / rapid_rise | 1 / 2 | 0 / 2 (incl. 1 bracketed) |
| point_source · rising | 1 / 1 | — |
| stellar · any path | 0 / 2, plus 3* | 0 / 1 |

- *An old object's rapid rise must persist (2026-10-03).* The elevated level has to be seen on two nights: an asteroid passes a position once and a bad detection does not repeat, while a real re-brightening is still bright the next night. Rerun from cache, the replay now gives **42 alerts and 3 notes**. The one dropped is LSST `stellar · re_brightening` on 05-02, a Gaia star: its r-band difference flux read +762 nJy on one detection, 1.2 mag above two nights earlier, with −182 nJy six minutes before it the same night and −192 nJy on the next r detection (05-12). Every other alert and note is unchanged, field by field; no new object's alert moved, so `rising` keeps its scope. The table above is the TNS check as run on the 46.
- *One event, one alert.* Three events — SN 2026ezw, SN 2026fgl and SN 2026ivl — first alerted as LSST `host · rising`, then again days later as ZTF `host · bracketed`, 0.26–0.34″ apart. Another survey's detection of an already reported position (within the monitor's 1.5″ association radius) is now a note on the first alert, keeping its own path: the ZTF notes carry the stronger evidence, the bracketed onset. So 46 detections make 43 alerts and 3 notes.
- *ZTF matched as expected.* All 6 ZTF objects bracketed on a host are in TNS (3 of them now as notes on the LSST alerts). Five list our own ZTF ID among their internal names.
- *LSST matched far more than expected (22 of 32), and the reason is the reporting, not the selection.* TNS records every one of the 22 as discovered from Rubin data. They were reported by SGLF (15), Lasair (5) and ALeRCE (1), and 21 carry our diaObjectId as `LSST-AP-DO-<id>`. Brokers were reporting COSMOS Rubin transients systematically that spring, so the rate measures that practice.
- *In TNS is not confirmed.* A match means another broker flagged the object too. The truth check is the TNS prefix: `SN` once a spectrum classified it, `AT` otherwise. ZTF: 5 of 6 are `SN` (3 Ia, 1 IIb, 1 Ic-BL), all `host · bracketed`. LSST: 7 of 22 are `SN` (6 Ia, 1 Ic-BL), and 15 stay `AT`; 6 of the 7 are `host · rising`. One spectroscopic SN Ia, SN 2026kfw, came out `point_source`: DR10 has a point source within 0.2″ of it at S/N 6.3, among the faintest the catalogue holds — likely a faint compact host or a marginal detection. **Known limitation**, kept as measured: a S/N floor that would free it (to `orphan`, not `host`) was measured and not adopted (see [Host Matching Validation](#host-matching-validation-transient-monitor), negative results).
- *Known property: the alerts trail TNS.* TNS discovery preceded the alert by a median of 4.5 days. That is the price of the paths: every path waits for detections on a second night, and `rising` for a later, brighter epoch, while other brokers report on the first detection. It is accepted for this monitor's purpose. Two alerts came first: SN 2026kfw by 5 days, AT 2026khr by 1.
- *The categories separate.* 18 of 22 host risers are in TNS, against 2 of 6 AGN-coloured risers and none of the stars.

**The oracle had a fault of its own, kept with its evidence.** ALeRCE's TNS service answers 10 requests per 60 s window. Once those are spent it returns empty records until the window turns, which looks exactly like "not in TNS".
- *Measured.* At one call a second, answers came back full for about 20 s and empty for about 40 s, every minute; paced at 7 calls a minute, every answer was full. The windows turned at :23 past the minute.
- *The rule.* The check never relies on that phase, since a restart can move it. It accepts an empty answer only when two (empty answer → full known-object canary) pairs fall within 60 s. At most one window turn fits in that span, so one pair shares a window, and within a window answers only go from full to empty.
- *The test.* A simulated throttled service swept over 120 window phases × 21 moments at which other users spend the quota never fools the rule. A one-pair rule and a wall-clock-minute rule both fail the sweep. So does the real rule against a window shorter than its assumed 60 s (27 of 2,520 cases at 25 s), which makes its one assumption visible.
- *The limit.* A free TNS account removes the ambiguity, through its own API key or the daily public-objects CSV used as a local snapshot. It is worth it only if TNS becomes more than display context.

**What the run changed in the monitor: the decision table.** The run surfaced a combination nobody had written down. Rising objects on Gaia stars, on AGN and on point sources were all reported as `rising`, mixed with supernova candidates.
- *Two axes.* The sky context alone now decides the category: `host`, `agn`, `orphan`, `point_source`, `stellar` or `unchecked`. The photometric path is the confidence label: `bracketed`, `rising`, `rapid_rise` or `re_brightening`. A report reads `host · rising`.
- *Visible names.* The old names `new_candidate`, `agn_flare` and `stellar_flare` claimed a path as well. The "flaring" stars rose over up to two weeks, which is stellar variability.
- *Nothing dropped.* The table has no "dropped" cell. The old rule dropped Gaia stars that did not rise: of the 6 stars in this window it had silently hidden 3, half of them.
- *Tested.* A test runs all 5 path cases × 8 sky contexts through the monitor. An unmapped context raises an error.
- *Re-reporting.* A report is repeated when either axis changes, so an `unchecked` object is reported again once the catalogue gives a verdict.

**First live run.** The monitor reports changes relative to what it has already reported, so a run from an empty state would report everything that currently qualifies, as the replay's first day did. A live scan with no state file is therefore an error, and a footprint's first run is explicit: `uv run python -m rubin_qa.transient_monitor --footprint ecdfs --init` records what already qualifies without reporting it, and lists it in its report (`absorbed`). From the next run on, only new objects and real changes are reported; something genuinely new on exactly that day is absorbed, and still shows up once its category or path changes.

**Tools:** `uv run python -m rubin_qa.transient_monitor --footprint D --replay 2026-03-12 2026-05-15 [--estimate | --compare-cuts]`, `uv run tools/replay_tns_check.py`. Results are kept locally in `logs/` (replay JSON and log, TNS rows with reporting groups).

---

## Asteroid Probe (transient monitor)

*Dated snapshots: unlike the census and the host-matching reports, these cannot be regenerated. A probe report lists the new loci a live broker (ANTARES) returned on the day it ran. A rerun a week later lists different loci, so it will not match an earlier report, and is not meant to. Each row carries `collected_mjd`, and the file name the collection date.*

`tools/asteroid_probe.py` asks whether the monitor's first cuts (a new locus, enough detections, high Galactic latitude) already keep asteroids out, and what an asteroid locus looks like when they do not. It walks new loci all over the sky and attributes every detection to a known asteroid or not, using each alert's own `ztf_ssnamenr`. One CSV row per locus.

The snapshot kept here, [`reports/asteroid_probe_20260927.csv`](reports/asteroid_probe_20260927.csv), is the evidence the monitor's asteroid rules cite. Of its 159 new high-latitude loci with at least 3 detections, 62 were known asteroids and 61 held nothing but asteroid detections. 60 of those fell on one night, 1.4 min to 3.9 h apart, which is why every path needs detections on two nights. The 61st was two different asteroids crossing the same spot 8 days apart, which only an asteroid ID catches. What is reproducible is the reduction: `uv run tools/asteroid_probe.py --report reports/asteroid_probe_20260927.csv` turns a saved snapshot into the same numbers every time, offline.

Because each run sees different loci, a later run can show what an earlier one could not. A rerun on 2026-10-03 did, twice:
- *A single slow asteroid can hold one position across two nights.* The 09-27 snapshot's only multi-night case was the two-asteroid one. The rerun had two loci where one asteroid stayed for about a day. Since the live monitor lost ZTF's asteroid ID (2026-10-03), such an object can pass the two-night rule. It is rare (3 in 185 new loci that day) and would surface as an alert a human can dismiss: accepted as it is.
- *Asteroid detections can merge into a real object's locus.* Three named loci held asteroid detections alongside a genuine object's own. On an old object, a passing asteroid then looks like a sudden rise, which is why an old object's rapid rise must persist (see [Replay Validation](#replay-validation-transient-monitor)).

---

## Project Structure

```
src/rubin_qa/
    config.py          — installation settings and values shared by several modules
    retry_budget.py    — the retry loop both broker clients use, and its run-wide sleep budget
    client.py          — ALeRCE API wrapper
    antares_api.py     — ANTARES API wrapper
    validators.py      — validate_completeness, validate_antares
    classifier.py      — classify_object (weighted consensus), classify_antares (tags)
    reporting.py       — QA row assembly and pipeline orchestration (ALeRCE + ANTARES)
    profiler.py        — single-object diagnostic tool (all three surveys, own CLI)
    photometry.py      — broker-neutral photometry records; ALeRCE listings and light curves (ZTF, LSST)
    sky_catalog.py     — Legacy Surveys DR10 + Siena Galaxy Atlas host matching, cached per catalogue version
    tns.py             — TNS lookups via ALeRCE's throttled TNS service
    transient_monitor.py — new-transient scan of a sky cone (own CLI)
    replay.py          — the monitor replayed day by day over past dates (transient_monitor --replay)
    __main__.py        — CLI entry point
pipeline.py            — backwards-compatible shim
tools/                 — measurement scripts, outside the monitor's imports:
    host_match_check.py  — host-matching validation (known hosts vs random positions)
    pa_from_pixels.py    — galaxy position angles from cutout pixels (source of the ellipse fixtures)
    atlas_query_contract.py — checks the test fake's q3c reading and the atlas join against Data Lab
    lsst_box_census.py   — every LSST object at ALeRCE per 5×5° test box, 2026-02-15 → 07-14 (box-based, from before the cones; kept as is)
    replay_tns_check.py  — a replay's alerts against TNS, per survey (throttle-aware lookups)
    point_source_snr.py  — two-sided measurement of a S/N floor for the point-source rule
    alerce_live_classifiers.py — which ALeRCE classifier versions are live, checked against the classifier registry
    asteroid_probe.py    — asteroid contamination of new loci; reduces a saved snapshot offline
    sample_latency.py    — one broker-latency sample per run (the dormant hourly unit's job)
    bench_latency.py     — one-off timing of raw broker calls, per survey
tests/                 — pytest, mock data only
systemd/               — example user units (Deployment, below)
pyproject.toml
reports/               — report outputs: the QA CSVs, the transient monitor's run reports,
                         the evidence files the docs cite
cache/                 — anything that can be fetched again; deleting it only costs time (gitignored)
state/                 — anything that can't be recreated: monitor state, the point-source watch,
                         pending TNS lookups, latency samples; deleting it loses information (gitignored)
logs/                  — run output; can be deleted at any time (gitignored)
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
| `test_antares.py` | ANTARES broker path, end to end (see below); the ANTARES helpers the tools share, and that no tool re-implements one |
| `test_sso_frozen.py` | the frozen SSO monitor: SHA-256 of its files and of what it imports; recording a new hash is the deliberate unlock |
| `test_validators.py` | completeness tokens, ZTF and LSST |
| `test_classifier.py` | the classifier registry (one vote per live family; retired versions, hierarchy levels and `nuclear_classifier` never vote; unknown pairs FLAG) and the weighted consensus |
| `test_reporting.py` | QA row assembly, status tiers, the newest detection per survey |
| `test_flag_reasons.py` | every flag path of both pipelines through its real validator, classifier and row builder: a row is flagged exactly when `flag_reasons` is not empty, each path carries its fixed keys, `no_classification` is named once |
| `test_ceilings.py` | request timeout, retry budget, run deadline |
| `test_main.py` | CLI argument routing, survey validation, CSV naming (the run's start, against a clock that moves during the run), exit codes (3 for a run its deadline stopped), the summary's newest and flagged lines |
| `test_time_format.py` | the time-and-age formatter: UTC to the minute, hours below 48 h and days from it, decided on the unrounded age |
| `test_unit_sizing.py` | each pipeline unit's `TimeoutStartSec` against the longest run its settings allow: `ExecStartPre`, the listing's worst case, `deadline_for` at the unit's n, and one object in flight (ANTARES requests bounded by antares-client's own timeout); reads the `.example` files, not the installed units |
| `test_profiler.py` | profiler: per-broker branches, derived band tables, its own CLI |
| `test_tools.py` | `tools/sample_latency.py` — pooling, boot filtering, failure reporting |
| `test_alerce_live_classifiers.py` | `tools/alerce_live_classifiers.py` — live vs retired from the newest objects' rows, what the registry must change, a report's `unknown_classifier` tokens |
| `test_photometry.py` | broker-neutral fetch layer: survey objects, ALeRCE listings for both surveys (both answer shapes, paging, partial answers), newest-in-cone, light curves |
| `test_transient_monitor.py` | monitor paths (bracketed, rising, rapid rise, re-brightening), the decision table (every path × sky context through `evaluate`; an unmapped context raises), re-reporting on the (category, path) pair, the listing guard (ZTF and LSST), one event one alert, the TNS line's three states, the point-source watch (only spectroscopic SNe count, threshold 3, notice until the rule or threshold changes, a point source with no S/N measurement shown and never counted, never fails the run), state, footprint atlas loading and its outage flags, the run report (what the printed report said, named in local time while run_utc and an alert's day stay UTC, every TNS status, none on a dry run, written after the state, init without alerts, a short one on a network failure, whole or not at all, strict JSON) |
| `test_live_without_antares.py` | the monitor's daily run (`--dry-run`) in a fresh interpreter where ANTARES cannot be imported, only the network faked; any import attempt fails it, even a swallowed one |
| `test_stay_inside_hook.py` | `.claude/hooks/stay_inside.py`, run as Claude Code runs it: writes pass only inside the project, the session scratchpad and the memory directory; `..`, look-alike prefixes and symlinks out are blocked, and so is any input it cannot check |
| `test_replay.py` | replay: listing in chunks, light-curve cache (failures never cached), day slicing (the future never leaks into a day), the span cut's equivalence on synthetic data, catalogue prefetch, suspend-aware stage timer, the same alert record as the live report |
| `test_sky_catalog.py` | DR10 host matching: ellipse conventions pinned to pixel-measured fixtures, atlas pieces (NGC 873), two-step candidates and the margin (with a q3c-reading fake TAP service, `tests/fake_tap.py`), catalogue gaps, cache rules (no failure or empty answer ever cached), the point source's S/N from a CSV answer with empty cells (a band counts only with both flux and ivar; none left is no measurement) |
| `test_host_match_check.py` | `tools/host_match_check.py` — every fetch-failure path fed its failure (empty TNS record, Sesame down, tile and atlas errors) must come back as "fetch error" or pending, never "no host"; truth mapping; mixed-version detection |
| `test_tns.py` | `rubin_qa/tns.py` — the throttle rule against a fake clock and a simulated throttled service at every window phase; an empty answer is "not in TNS" only when vouched for; names decoded where they come in |
| `test_paths.py` | paths resolve from the repo root, never the current directory: one root shared by the code and every tool (`RUBIN_QA_ROOT` moves them all), relative command-line paths land in the repo |
| `test_asteroid_probe.py` | `tools/asteroid_probe.py` — the evidence behind the monitor's asteroid rules |
| `test_readme_quotes.py` | the docs against what they describe: each recorded result the README quotes against its constant, `systemd/README.md`'s summary of the unit files against the files, the antares-client version against `uv.lock`, and every link in or between the READMEs and CLAUDE.md against the headings that exist |

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

The jobs run as systemd user units, with examples in `systemd/`. Which units exist and their status, installing them, their sandboxing and logging, and the notes for running them: [`systemd/README.md`](systemd/README.md).

---

## Known API Quirks

**ALeRCE:**
- `lc_classifier` returns empty for many objects; `stamp_classifier` works reliably
- API returns duplicate oids — deduplicated in `fetch_candidates`
- The object listing's default order, which is what the QA pipelines sample (`fetch_candidates` asks no `order_by`), is by oid, not by recency. ZTF: ascending, the lowest IDs ALeRCE holds, a fixed set from 2017: every ZTF QA report of 2026-11-02 and 11-03 lists ZTF17aaaaaak upward in the listing's order, and a live check on 2026-11-04 answered HTTP 500 three times (08:04, 08:06, 08:29 UTC). LSST, measured 2026-11-04 at page size 196: descending oid, with none of its objects on the `order_by=lastmjd, order_mode=DESC` page of the same size, and its own `lastmjd` unsorted (it rises at 45 of 195 steps). The `lastmjd` DESC page repeats oids: 196 rows, 152 oids.
- LSST multisurvey client raises `NotImplementedError` for `survey="ztf"` — ZTF uses the legacy client path
- The LSST object listing refuses `page_size` above 200 with HTTP 422 (`less_than_equal`); the live monitor's LSST listing, then asking 1000, failed on it on 2026-11-02. ZTF's listing has no such limit (201 and 256 answered the same day).
- The LSST object listing has one row per object *per classifier*, and since the stream resumed, new objects carry only `stamp_classifier_rubin_beta_20260421`. On 2026-11-03, 0 of the 2638 ECDFS objects first detected after the July stop had a `stamp_classifier_rubin_beta` row, so a listing filtered on that name returns none of them, with no error. An object's rows agree on its fields: the same day, all 1401 ECDFS objects with two rows had the same `firstmjd`, `lastmjd`, `n_det`, `meanra` and `meandec` in both. The replay's rule for it: [CLAUDE.md, Replay](CLAUDE.md#transient-monitor-transient_monitorpy-rebuilt-2026-09-27).
- LSST oids come back as integers from the API — normalized to `str` in `fetch_candidates`
- LSST `classifier_version` is an integer (201, 202) where ZTF sends a string (`"1.0.0"`); the version tiebreaker parses both
- LSST: `query_lightcurve`, `query_detections`, `query_non_detections` and `query_forced_photometry` work; `query_magstats`, `query_classifiers` and `query_classes` do not
- LSST `query_lightcurve` returns **three** keys — `detections`, `non_detections`, `forced_photometry` — against ZTF's two, and `non_detections` is empty even for well-sampled objects: Rubin publishes forced photometry where ZTF publishes upper limits. Detections carry fluxes (`psfFlux`, nJy) and `band`/`band_name`, never `magpsf`/`fid`
- LSST `query_probabilities` works and returns one row per class for each classifier version that classified the object. On 2026-11-02 and 11-03, objects first detected before the July stop carried `stamp_classifier_rubin_beta` (version 201) and `stamp_classifier_rubin_beta_20260421` (202), later ones only 202; `nuclear_classifier` (version 1) adds a position label, `nuclear` or `off-nuclear`, at probability 1.0. How they count as votes: CLAUDE.md [QA pipeline](CLAUDE.md#qa-pipeline); what that leaves: [Independent Evidence in ALeRCE's Classifiers](#independent-evidence-in-alerces-classifiers)
- ALeRCE degrades intermittently, in episodes lasting hours to days, every few weeks. Broad queries are hit first and hardest; narrow queries can be affected briefly too. The pipelines fail loudly and recover without intervention. Evidence: the failure clustering the [Broker Latency Sampling Campaign](#broker-latency-sampling-campaign) caught, and one dated example: 2026-10-06 to 10-08, broad candidate queries timed out on both hosts (ZTF and LSST)

**ANTARES:**
- Kafka streaming requires credentials (request from ANTARES team); search/fetch API is open
- **The v2 API, from 2026-11-04.** Every antares-client before 2.0 calls the v1 API, which ANTARES stops supporting on 2026-11-04 (the 2.0.2 README); 2.0.0 had a paging bug, so the floor is 2.0.1. The entries below were read from the 2.0.2 source and probed live on 2026-11-03.
- **The candidate listing is the most recently updated loci, not a random sample.** The listing (`antares_api.list_loci`) is the client's `search({})`, which it always sorts on `properties.newest_alert_observation_time`, latest first. ANTARES QA reports written on 2.x (the first: 2026-11-03 14:42) are not comparable with earlier ones (the rule: CLAUDE.md, [QA pipeline](CLAUDE.md#qa-pipeline)). One exposure updates hundreds of loci at the same moment (all 60 loci on the first 6 pages shared one time on 2026-11-03), so in practice the sample is the loci the last exposure or two touched, and those exposures' survey: a manual run started at 16:10 CET on 2026-11-03 drew 237 loci whose newest alerts came from 3 ZTF exposures within 1.5 minutes (13:20:54–13:22:20 UTC), and none of the 237 held an LSST detection.
- A listing over more than one page repeats loci *within a single call* — deduplicated in `fetch_antares_candidates`, so a run returns fewer loci than it asks: 256 requested in the client's default pages of 10 → 183 unique on 2026-11-03. The order within a tie is not stable from one page request to the next, so pages overlap (page 6 repeated 5 loci of pages 1–5), and each repeat stands for a locus skipped. On 1.x the listing was an unseeded random sample that reshuffled between page requests the same way (256 → 217, 225, 228 unique on 2026-08-06; 207 on 2026-10-04). Not a row-drop in the pipeline — one row is emitted per deduplicated ID. **One page has no repeats:** pages of 256, 500 and 1000 came back all distinct, every time (2026-11-04), so the listing is one page of n up to `ANTARES_MAX_PAGE_SIZE` (below), and a run on one page lists every locus it asks: its sample line reads `N loci checked of N unique listed (N asked)`, where a paged run's listed fewer than it asked.
- **Page time by page size**, 2026-11-04 on a healthy server (the first page only, the client's own request, 7 interleaved rounds 1 s apart; p50 / max of 7): 1.25 / 1.27 s at 10, 1.38 / 1.95 s at 50, 1.48 / 1.93 s at 100, 1.85 / 2.31 s and, in a second sitting, 2.45 / 2.66 s at 256, 2.72 / 2.92 s at 500, 3.34 / 3.93 s at 1000; all 49 answered 200. A page of 1000 stays within 5 s, half the cutoff (below), so the cap is the client's own ceiling, 1000: antares-client 2.0.2's `search.get_random_loci` and `get_random_locus_ids` refuse more. The pipeline's listing of 256 (`list_loci`, the client's parsing of each locus included) took 4.27 s at the median and 5.13 s at most over 7.
- Each page of the listing links to the next with an `http://` URL (`offset` and `limit`), which the API redirects (301) to https: two requests a page, 51 and 42 s for 256 loci in pages of 10 on 2026-11-03. A one-page listing follows no next link.
- A nonexistent ANT locus ID answers 404, and the client returns `None`: `locus:not_found` at once, no retry (the v1 API answered 500, which cost the full retry backoff). ZTF IDs return `not_found` too.
- Error answers carry no status code in their body (`{"detail": …}`), and the client raises the same `AntaresException(body)` for every error status, so `antares_api` reads the status from the response (`_raise_answered_status`): a 400 or 422, a malformed request, is an answer and not retried, the same rule as ALeRCE's side. A 500 can come as `text/plain`, on which the client's own JSON parse fails (`json.JSONDecodeError`); it is retried as a fault. A range query on a field of the wrong type answered that 500 on 2026-11-03, so a 500 is sometimes the request's fault, indistinguishable from the server's.
- Selecting by survey (probed 2026-11-03): `exists properties.survey.lsst` works on v2, but it means LSST anywhere in the locus's history. All 10 loci it returned had a ZTF alert as their newest (`ztf_candidate:…`), because LSST alerts attach to old ZTF loci. The newest alert's survey is a `prefix` on `properties.newest_alert_id`: `lsst` returned 256 loci, every newest alert an LSST one; the `.keyword` subfield returned none. LSST alert IDs start `lsst:`, ZTF's `ztf_candidate:` or `ztf_upper_limit:`.
- A listing request that runs past about 10 s answers HTTP 500 (`text/plain`): every slow 500 on 2026-11-03 came at 10.2–10.3 s, every success within 9.0 s (on 2026-11-04, a healthy day, nothing came near it: the slowest of the 66 listing requests timed raw that day, a page of 1000, took 3.9 s, and the `prefix` query 0.2–1.6 s), and from about 17:00 CET even a 10-locus page failed that way, while lookups by ID answered in 0.3 s. A fast 500 (0.2 s) is a query the server rejects.
- The cone filter has a new form, `{"radius": "<r> degree", "location": "<hmsdms>"}`; the old `distance`/`htm16` form answers 422. The server does not clamp the radius (the client's own `cone_search` refuses more than 1°, so the tools build the clause: `sky_distance_clause`). It overshoots a little: a 2.82° cone on ecdfs returned loci up to 2.89° from the centre (2026-11-03).
- The locus properties carry `brightest_alert_magnitude` but no `faintest_alert_magnitude` (none of 40 loci on 2026-11-03). `mag_range` read it, so it was empty in every ANTARES QA report from 2026-09-28 to 2026-11-03, under the v1 API too. Since then both ends come from the detections, and the server's brightest is checked against their minimum (`brightest_mismatch`).
- Observed 2026-10-23: a search returned HTTP 500 twice; a lookup by ID later that day returned 200.
- Both brokers' calls go through one retry loop (`retry_budget.call`; retry policy: see `RETRY_*` in config.py); each client passes only its own policy of which errors are worth retrying, and its name, which starts each retry warning. antares-client 2.0.2 (the pinned version) applies its own 60 s request timeout.
- Because retry is per-object, a stalled broker is bounded by three ceilings, applied to both pipelines (values in config.py). All degrade rather than abort — a short CSV still gets written, and a run the deadline stopped then exits 3 (`__main__.EXIT_DEADLINE`), so its unit shows failed instead of passing a partial report as a full one:
  - **Per request** (`REQUEST_TIMEOUT`) — forced onto the ALeRCE client, which passes no timeout of its own and would otherwise block forever. ANTARES already applies its own. It bounds inactivity, not call duration: `requests` applies it per socket operation, so it kills a dead connection, while a response that trickles in never trips it. The sampling campaign caught three LSST calls succeeding at 60.7, 60.7 and 63.3 s with the 60 s timeout then in force. A slow but live call is bounded only by the run deadline.
  - **Retry sleep per run** (`RETRY_BUDGET_SECONDS`) — shared budget; once spent, a failing call is given up at once instead of retried.
  - **Run deadline — sized to the job**, at `DEADLINE_SLACK` × the run's own estimated duration (floor `DEADLINE_FLOOR`). A large scan is entitled to take a long time; what trips the deadline is a run dragging far past what its size predicts. Pass `max_run_seconds` to override, or `0` to disable. The estimate comes from `SECONDS_PER_OBJECT` ([Broker Latency Sampling Campaign](#broker-latency-sampling-campaign), above). The slack is not there for drift — latency proved stable — but to absorb one pathological object: the retry budget caps retry *sleep*, not socket time, so a worst-case ZTF object can block for 3 calls × `RETRY_ATTEMPTS` × `REQUEST_TIMEOUT` with only this deadline to stop it.
- The alerce package sets no request timeout anywhere, so `client._force_session_timeout()` wraps all five `requests.Session` objects the Alerce client holds.
- `locus.alerts` bundles real detections (`ztf_candidate`, have `ant_mag`) and non-detections (`ztf_upper_limit`, no `ant_mag`) — pipeline filters to `ant_mag.notna()` before building the lightcurve
- `locus.lightcurve` is a separate, tidier 14-column frame holding detections *and* upper limits together: `ant_mag` is null on a non-detection, where `ant_maglim` carries the limit. `ant_mag_corrected` is the `magpsf_corr` analogue, `ant_passband` the `fid` analogue. Also on the locus: `timeseries` (~114 columns), `catalog_objects` (crossmatch), and ~20 precomputed `feature_*` entries in `properties`
- ANTARES pre-filters alerts to rb ≥ 0.55, fwhm ≤ 5.0 px, elong ≤ 1.2 — objects in ANTARES already pass these; ALeRCE objects may not
- `antares-client` import is deferred — ALeRCE-only installs are unaffected if the package is absent

[alerce link]: https://alerce.science/
[antares link]: https://antares.noirlab.edu/
