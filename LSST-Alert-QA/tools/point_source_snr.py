"""
Measure a signal-to-noise floor for sky_catalog's point-source rule.

The rule: the nearest non-piece DR10 source within 1" of a transient is a PSF -> the
transient is labelled point_source (a variable star beyond Gaia, a QSO, a compact
nucleus) instead of host. Every PSF counts, however faint: SN 2026kfw, a spectroscopic
SN Ia, came out point_source on a PSF of r = 26.1, below DR10's usual depth. The user's
question (2026-10-02): would a floor on the PSF's signal-to-noise (not its magnitude, so
depth differences between fields count) keep the real point sources and drop the noise?

Every source the rule fires on, from data already scored:
  - known hosts (logs/host_check/eval_known.jsonl): the 12 point-like hosts within 1"
    (DES chose that PSF as the host) and the correct hosts the rule relabels;
  - random positions (eval_randoms.jsonl): what the rule catches by chance;
  - the footprint-D replay's point_source alerts, with their TNS prefix (SN = classified).
For each, what the position gets if the rule ignores that PSF: the DLR host (d_DLR <= 4)
or none. S/N is fetched per source (flux and flux_ivar in g, r, i, z and Tractor's
dchisq) by position from Data Lab and cached in cache/host_check/psf_snr.jsonl; the
production tile cache does not carry the optical flux_ivar.

Three S/N definitions are compared: the best single band, all bands combined
(sqrt of the summed (flux^2 ivar)), and sqrt(dchisq_1), the PSF model's fit improvement.
Not magnitude: DR10 measures r for a source detected in another band, so a faint red
galaxy can read r = 26 at high S/N, and depth varies (the DES deep stacking in ECDFS), so a
magnitude floor would mean something different from place to place (user, 2026-10-02).
Also printed: how far the PSF fit beats the best extended one (dchisq), by S/N bin - to
see whether "PSF" stops meaning point-like at low S/N.

    uv run tools/point_source_snr.py          # fetch what is missing (cached), then the tables
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import host_match_check as h  # noqa: E402

from rubin_qa import sky_catalog as sc  # noqa: E402
from rubin_qa.config import ERROR_PREFIX, PROJECT_ROOT  # noqa: E402
from rubin_qa.transient_monitor import FOOTPRINTS  # noqa: E402

ROOT = PROJECT_ROOT
OUT = ROOT / "cache" / "host_check" / "psf_snr.jsonl"  # fetched rows: refetchable
REPLAY = ROOT / "logs" / "replay_D_2026-03-12_2026-05-15.json"
REPLAY_TNS = ROOT / "logs" / "replay_tns_D_2026-03-12_2026-05-15.json"
CANDIDATES = ROOT / "cache" / "replay" / "candidates"
BANDS = ("g", "r", "i", "z")
MATCH_ARCSEC = 0.1  # the counterpart's own position, to 6 decimals in the scored files
FLOORS = (5, 6, 6.5, 7, 7.5, 8, 10, 15, 20)
WORKERS = 4


def collect() -> list[dict]:
    """Every PSF the rule fires on, with its group and what the position gets without it."""
    out = []
    for r in map(json.loads, open(h.OUT_DIR / "eval_known.jsonl")):
        if not r.get("point_key") or "error" in r:
            continue
        v = r[h.PRODUCTION_VARIANT]
        host = v.get("best_d") is not None and v["best_d"] <= sc.HOST_DLR_MAX
        if r["truth"] == "host_is_point_source":
            group, without = "known: PSF is the host", ("wrong host" if host else "orphan")
        else:
            group, without = "known: correct host relabelled", ("correct host" if host and v.get("best_is_true") else
                                                               "wrong host" if host else "orphan")
        out.append({"group": group, "name": f"{r['source']} {r['name']}", "key": r["point_key"], "without": without})
    for r in map(json.loads, open(h.OUT_DIR / "eval_randoms.jsonl")):
        if r.get("point_key") and "error" not in r:
            v = r[h.PRODUCTION_VARIANT]
            host = v.get("best_d") is not None and v["best_d"] <= sc.HOST_DLR_MAX
            out.append({"group": "random position", "name": f"{r['footprint']} {r['ra']:.4f},{r['dec']:.4f}",
                        "key": r["point_key"], "without": "chance host" if host else "none"})
    return out + replay_sources()


def replay_sources() -> list[dict]:
    replay = json.loads(REPLAY.read_text())
    cone = tuple(replay["cone"])
    tns = {r["key"]: r for r in json.loads(REPLAY_TNS.read_text())["rows"]} if REPLAY_TNS.exists() else {}
    where = {f"{r['survey']}:{r['oid']}": (r["ra"], r["dec"])
             for p in CANDIDATES.glob(f"{replay['footprint']}_*.json") for r in json.loads(p.read_text())}
    atlas = sc.footprint_atlas(cone)
    out = []
    for a in replay["alerts"]:
        if a["crossmatch"] != "point_source":
            continue
        ra, dec = where[a["key"]]
        rows = sc.neighbours(ra, dec, sc.SEARCH_RADIUS_ARCSEC)
        psf = sc.point_counterpart(rows)
        best = sc.best_host(rows, atlas_rows=sc.atlas_near(atlas, ra, dec))
        t = tns.get(a["key"], {})
        label = t.get("tns_name", "not in TNS" if t.get("tns") == "not_in_tns" else "TNS not checked")
        out.append({"group": "replay alert", "name": f"{a['key']} ({a['category']}; {label})",
                    "key": f"{psf['ra']:.6f},{psf['dec']:.6f}",
                    "without": f"host d_DLR {best[0]:.1f}" if best else "orphan"})
    return out


def fetch(key: str) -> dict:
    ra, dec = map(float, key.split(","))
    cols = ["ra", "dec", "type", *(f"flux_{b}" for b in BANDS), *(f"flux_ivar_{b}" for b in BANDS),
            *(f"nobs_{b}" for b in BANDS), *(f"dchisq_{k}" for k in range(1, 6))]
    query = (f"SELECT {', '.join(cols)} FROM {sc.TABLE} "
             f"WHERE 't' = q3c_radial_query(ra, dec, {ra:.6f}, {dec:.6f}, {MATCH_ARCSEC / 3600:.8f})")
    for wait in (5, 30, 60, None):
        try:
            r = requests.get(sc.TAP_URL, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                                 "MAXREC": 10, "QUERY": query}, timeout=sc.TAP_TIMEOUT)
            if r.status_code == 200 and r.text.startswith("ra,"):
                lines = r.text.strip().splitlines()
                if len(lines) != 2:
                    raise RuntimeError(f"{len(lines) - 1} DR10 rows within {MATCH_ARCSEC}\" of {key}")
                return dict(zip(lines[0].split(","), lines[1].split(",")))
            err = f"HTTP {r.status_code} {r.text[:60]!r}"
        except requests.RequestException as e:
            err = type(e).__name__
        if wait is None:
            raise RuntimeError(f"{key}: {err}")
        time.sleep(wait)


def snr(row: dict) -> dict:
    f = {b: float(row[f"flux_{b}"] or 0) for b in BANDS}
    iv = {b: float(row[f"flux_ivar_{b}"] or 0) for b in BANDS}
    per = {b: f[b] * math.sqrt(iv[b]) for b in BANDS}
    return {"best_band": max(per.values()), "combined": sc.combined_snr(row),
            "dchisq_psf": math.sqrt(max(float(row["dchisq_1"] or 0), 0)), "per_band": per,
            "r_mag": sc.nmgy_to_mag(f["r"])}


def two_sided(sources: list[dict]) -> None:
    """
    Both directions at each floor on the combined S/N, as for the DLR threshold: what the
    floor costs on the point-like true hosts (they lose the point_source label and become
    orphans or pick up a galaxy) against how far it lowers the rate at which a chance PSF
    takes the label at random positions.
    """
    randoms = [json.loads(l) for l in open(h.OUT_DIR / "eval_randoms.jsonl")]
    scored = {fp: sum(1 for r in randoms if r["footprint"] == fp and "error" not in r and not r.get("catalogue_gap"))
              for fp in sorted({r["footprint"] for r in randoms})}
    n_rand = sum(scored.values())
    psf_hosts = [s for s in sources if s["group"] == "known: PSF is the host"]
    relabelled = [s for s in sources if s["group"] == "known: correct host relabelled"]
    rand = [s for s in sources if s["group"] == "random position"]
    replay = [s for s in sources if s["group"] == "replay alert"]
    print(f"\nTwo-sided, by floor on the combined S/N ({len(psf_hosts)} point-like true hosts; {n_rand} random positions):")
    print(f"  {'floor':>5} | {'PSF hosts below floor':>21} {'-> orphan':>9} {'-> galaxy':>9} | "
          f"{'relabelled hosts back':>21} | {'random point_source rate':>24} {'per footprint':>24} "
          f"{'dropped -> host/none':>20} | replay dropped")
    for floor in (0, *FLOORS):
        below = [s for s in psf_hosts if s["combined"] < floor]
        kept = [s for s in rand if s["combined"] >= floor]
        dropped = [s for s in rand if s["combined"] < floor]
        per = "  ".join(f"{fp} {100 * sum(s['name'].startswith(fp + ' ') for s in kept) / scored[fp]:.2f}%" for fp in scored)
        print(f"  {floor:>5g} | {len(below):>21} {sum(s['without'] == 'orphan' for s in below):>9} "
              f"{sum(s['without'] == 'wrong host' for s in below):>9} | "
              f"{sum(s['combined'] < floor for s in relabelled):>18}/{len(relabelled)} | "
              f"{len(kept):>4}/{n_rand} = {100 * len(kept) / n_rand:.2f}% ± {100 * math.sqrt(len(kept)) / n_rand:.2f} "
              f"{per:>24} {sum(s['without'] == 'chance host' for s in dropped):>9}/{sum(s['without'] == 'none' for s in dropped):<10} | "
              + (", ".join(s["name"].split(" (")[1].rstrip(")").split("; ")[1] for s in replay if s["combined"] < floor) or "-"))
    print("  (± is Poisson on the count of firings; the PSF-host 'galaxy' is one DES did not choose, i.e. a wrong host)")


def main() -> int:
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0]).parse_args()
    sources = collect()
    have = {json.loads(l)["key"]: json.loads(l) for l in OUT.read_text().splitlines()} if OUT.exists() else {}
    todo = sorted({s["key"] for s in sources} - set(have))
    print(f"{len(sources)} firings of the point-source rule; {len(todo)} DR10 rows to fetch", flush=True)
    failed = []

    def one(key):
        try:
            return key, fetch(key)
        except RuntimeError as e:
            failed.append(str(e))
            return key, None

    with ThreadPoolExecutor(WORKERS) as ex, open(OUT, "a") as f:
        for key, row in ex.map(one, todo):
            if row is not None:
                have[key] = {"key": key, **row}
                f.write(json.dumps(have[key]) + "\n")
    if failed:
        print(f"{ERROR_PREFIX}{len(failed)} fetches failed (rerun to retry): {failed[:3]}", file=sys.stderr)
        return 1

    for s in sources:
        s.update(snr(have[s["key"]]), type=have[s["key"]]["type"])
    print(f"\nPSF S/N at each firing (best band / combined g+r+i+z / sqrt(dchisq_psf)); r mag; what it gets without the rule")
    for group in ("known: PSF is the host", "known: correct host relabelled", "random position", "replay alert"):
        rows = sorted((s for s in sources if s["group"] == group), key=lambda s: s["combined"])
        print(f"\n{group} ({len(rows)}):")
        for s in rows:
            mag = f"{s['r_mag']:.1f}" if s["r_mag"] else "  - "
            print(f"  {s['best_band']:7.1f} {s['combined']:7.1f} {s['dchisq_psf']:7.1f}   r {mag:>5}   "
                  f"{s['type']:4s} without: {s['without']:16s} {s['name']}")
    print("\nPSF type margin (dchisq_PSF - best of REX/DEV/EXP/SER) by combined S/N:")
    for lo, hi in ((0, 7), (7, 10), (10, 20), (20, math.inf)):
        g = []
        for s in sources:
            if lo <= s["combined"] < hi:
                d = [float(have[s["key"]][f"dchisq_{k}"] or 0) for k in range(1, 6)]
                g.append(d[0] - max(d[1:]))
        if g:
            g.sort()
            print(f"  S/N {lo:>3}-{'up' if hi == math.inf else hi:>3}: {len(g):3d} PSFs, median margin {g[len(g) // 2]:6.1f}, "
                  f"{sum(x < 1 for x in g) / len(g):.0%} within dchisq 1 of an extended model")
    two_sided(sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
