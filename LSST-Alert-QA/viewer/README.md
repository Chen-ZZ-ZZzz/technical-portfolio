# Transient Monitor Report Viewer

**Status (2026-11-24):** the page is built to the rules specified so far: the overview, and the report page's heading, links, Report region and cone line. The test suite is being written. The report's own content, from its top-level keys on, is the next round.

A web page that shows the run reports of the [transient monitor](../README.md#transient-monitor) (`reports/transient_*.json`): an overview listing every report, and a page for each one. The page is also the test object of a Playwright suite, written from the rules in [SPEC.md](SPEC.md), not from the page's code.

## Where the Work Stands

- **Overview page:** specified in [SPEC.md](SPEC.md) and built.
- **Report page:** its heading, the Overview and Raw JSON links, the Report region and the cone line are specified and built. The rest of the report's content is being defined in [SPEC.md](SPEC.md).
- **Test suite:** being written, in Python with pytest-playwright.

## Running

The page is static, `site/index.html` and `site/viewer.js`, with nothing to build or install. It reads `reports/` relative to its own address, from the server's directory listing, so it needs a server that lists a directory as HTML links, as Python's `http.server` does. `site/reports` is a symlink to the workspace's reports (`ln -s ../../reports/ site/reports`; gitignored, so a fresh checkout makes it once). Serve `site/` and open http://127.0.0.1:8000/:

```
cd viewer
uv run python -m http.server --bind 127.0.0.1 --directory site 8000
```

The suite is a project of its own (`pyproject.toml`, `uv.lock`, `.venv`), apart from the root's, because Playwright is needed only here. Run it from `viewer/`:

```
cd viewer
uv run pytest
```

- Playwright's browsers live in the workspace's `cache/ms-playwright`, and `PLAYWRIGHT_BROWSERS_PATH` must point there, as an absolute path, in every shell that installs or runs them.
- Never run the suite from the root as `uv run pytest viewer/tests`: the root's `testpaths` applies only when pytest gets no path, so that command runs the viewer's tests in the root's venv, which has no Playwright.
- `pyproject.toml` keeps its own `[tool.pytest.ini_options]` table. Without it, pytest walks up and takes the root's config and rootdir.
- Keep `__init__.py` out of `tests/`: the root's `tests/` is a package named `tests`, and a second one clashes when one pytest session collects both.

## Test Data

The example files the rules name, real and made, each with what it checks and how it was made: [tests/data/README.md](tests/data/README.md).
