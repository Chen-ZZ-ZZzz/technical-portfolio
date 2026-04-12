# Technical Portfolio

This portfolio showcases technical skills, projects, and exercises across multiple domains. Each folder contains a README with details on purpose, skills demonstrated, and examples.

---

## Projects

### [LSST-Alert-QA](LSST-Alert-QA/)
Data quality pipeline for astronomical transient alerts from ZTF and LSST via the [ALeRCE](https://alerce.science/) and [ANTARES](https://antares.noirlab.edu/) brokers. Weighted classifier consensus, completeness validation, structured QA reporting. Built iteratively with Claude Code.

### [Health Checker](health-checker/)
Network and log health tools for lab and CI environments. Ping-based host reachability checks with latency reporting, and log file scanning for ERROR/WARN patterns with per-file and aggregate summaries. CI-friendly exit codes.

---

## Scripts & Exercises

| Folder | Focus |
|--------|-------|
| [Python](Python/) | File system utilities, regex tools, CLI automation, data parsing |
| [Bash](Bash/) | Shell scripting — safe iteration, error handling, archiving |
| [Linux](Linux/) | CLI tools — filename sanitizer, date converter, file renumbering, whitespace normalizer |
| [QA_Automation](QA_Automation/) | Test automation scaffolding, HIL/SIL stubs, log analysis |
| [C](C/) | Embedded / low-level practice |
| [HIL-SIL](HIL-SIL/) | Hardware-in-the-loop test exploration |

---

## AI-Assisted Development

The [AI-Prototypes](AI-Prototypes/) folder documents projects built as human–AI collaborations (Claude Code). I define requirements, investigate APIs, direct architecture, and review all output. The AI handles implementation and refactoring. The goal is working software I can fully explain and maintain.

---

## Tech Stack

- **Languages:** Python, Bash, C
- **Tools:** Git, pytest, argparse, pandas, pathlib, subprocess
- **Environment:** Debian/Linux (since 2005), Emacs (since 2014)
