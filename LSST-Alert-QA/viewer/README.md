# Transient Monitor Report Viewer

**Status (2026-11-15):** the overview page is specified and the report page is being defined. Neither the page nor its tests exist yet.

A web page that shows the run reports of the [transient monitor](../README.md#transient-monitor) (`reports/transient_*.json`): an overview listing every report, and a page for each one. The page is also the test object of a Playwright suite, written from the rules in [SPEC.md](SPEC.md), not from the page's code.

## Where the Work Stands

- **Overview page:** specified in [SPEC.md](SPEC.md).
- **Report page:** being defined; the rules finished so far are in [SPEC.md](SPEC.md).
- **The page and the test suite:** not written yet.

## Running

How to run the suite and serve the page will be written here once those exist.

## Test Data

The example files the rules name, real and made, each with what it checks and how it was made: [tests/data/README.md](tests/data/README.md).
