"""
photometry.py - broker-neutral fetch layer for the transient monitor.

Whatever a broker returns is converted into one record shape, keyed on the
survey's own object ID (a ZTF objectId, an LSST diaObjectId), never on a broker
ID. Brokers become data sources: what is interesting is decided downstream,
on photometry.

  PhotPoint     one epoch of one survey object, flux in nJy (difference image)
                  detection:    flux, flux_err, detected=True
                  upper limit:  flux=NaN, flux_err = limit flux / its sigma
                  forced phot:  flux, flux_err, detected=False
  SurveyObject  one survey object: position, its points, and report-only
                broker context (tags, classifications, broker catalogue
                matches, TNS, broker IDs)

Sources, by default (measured 2026-09-27, see CLAUDE.md):
  ZTF   ANTARES: one alerts call per locus gives detections and upper limits,
        identical to ALeRCE's; ANTARES is the faster, steadier API. ALeRCE is
        the per-object fallback.
  LSST  ALeRCE: the only broker serving forced photometry (ANTARES has LSST
        detections only). ANTARES loci also contribute LSST discovery for free.
"""

import math
from dataclasses import dataclass, field

import pandas as pd
from elasticsearch.dsl import Search
from requests.exceptions import RequestException

from antares_client.exceptions import AntaresException

AB_NJY_ZP = 31.4  # m_AB = 31.4 - 2.5 log10(flux / nJy)
ZTF_LIMIT_SIGMA = 5.0  # ZTF diffmaglim is a 5-sigma limit
# ant_survey codes in ANTARES lightcurves and alerts (verified 2026-09-27; 2 is not LSST)
ANT_ZTF_DET, ANT_ZTF_LIMIT, ANT_LSST_DET = 1, 2, 4
ZTF_FID_BAND = {1: "g", 2: "r", 3: "i"}
NEGATIVE_DIFF = {"f", "0", "-1", "false"}  # isdiffpos spellings meaning flux went down
# The listing walk and the lazy per-locus fetches raise these.
NETWORK_ERRORS = (RequestException, AntaresException)

Cone = tuple[float, float, float]  # ra, dec, radius, all degrees: ALeRCE searches cones only, so everything does


@dataclass(frozen=True)
class PhotPoint:
    survey: str
    survey_object_id: str
    mjd: float
    band: str
    flux: float  # nJy; NaN for an upper limit
    flux_err: float  # nJy; for an upper limit, the limit flux / its sigma
    detected: bool
    broker: str


@dataclass
class SurveyObject:
    survey: str
    survey_object_id: str
    ra: float
    dec: float
    points: list[PhotPoint] = field(default_factory=list)
    sso_id: str | None = None  # the survey's own asteroid association
    broker_refs: dict[str, str] = field(default_factory=dict)
    # report only, never filtered on:
    tags: list[str] = field(default_factory=list)
    classifications: list[str] = field(default_factory=list)
    broker_matches: list[str] = field(default_factory=list)
    tns: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)  # listing fields (firstmjd, lastmjd, n_det)
    fetch_errors: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.survey}:{self.survey_object_id}"


def mag_to_njy(mag: float) -> float:
    return 10 ** ((AB_NJY_ZP - mag) / 2.5)


def njy_to_mag(flux: float) -> float:
    return AB_NJY_ZP - 2.5 * math.log10(flux)


def magerr_to_njy(mag: float, magerr: float | None) -> float:
    if magerr is None or not math.isfinite(magerr):
        return math.nan
    return mag_to_njy(mag) * math.log(10) / 2.5 * magerr


def _sign(isdiffpos) -> int:
    return -1 if str(isdiffpos).strip().lower() in NEGATIVE_DIFF else 1


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def is_solar_system(props: dict) -> bool:
    """
    ZTF matched this alert (or ANTARES locus) to a known asteroid. ANTARES
    stores the string "null" when it did not.
    """
    return props.get("ztf_ssnamenr") not in (None, "", "null")


def truncate(points: list[PhotPoint], until_mjd: float | None) -> list[PhotPoint]:
    """Drop epochs after until_mjd (replay: nothing after the replay date exists)."""
    return points if until_mjd is None else [p for p in points if p.mjd <= until_mjd]


# ------------------------------------------------------------------ ANTARES


def antares_cone_query(cone: Cone, since_mjd: float, until_mjd: float | None, min_detections: int) -> dict:
    """
    Loci inside the cone with an alert since since_mjd (active, not new: LSST
    alerts join old ZTF loci, so locus age says nothing), existing by until_mjd.
    The cone is ANTARES's own server-side sky_distance filter (as cone_search
    builds it), which combines with the range filters (checked 2026-09-27).
    """
    ra, dec, radius = cone
    s = (
        Search()
        .filter("range", **{"properties.newest_alert_observation_time": {"gte": since_mjd}})
        .filter("range", **{"properties.num_mag_values": {"gte": min_detections}})
    )
    if until_mjd is not None:
        s = s.filter("range", **{"properties.oldest_alert_observation_time": {"lte": until_mjd}})
    query = s.to_dict()
    # sky_distance is ANTARES's own query type; elasticsearch-dsl cannot build it
    query["query"]["bool"]["filter"].insert(
        0, {"sky_distance": {"distance": f"{radius} degree", "htm16": {"center": f"{ra} {dec}"}}}
    )
    return query


def _antares_point(alert, broker: str = "antares") -> tuple[str, str, PhotPoint, str | None] | None:
    """(survey, object_id, point, sso_id) for one ANTARES alert, None if unusable."""
    p = alert.properties
    code = p.get("ant_survey")
    mjd = float(alert.mjd)
    if code in (ANT_ZTF_DET, ANT_ZTF_LIMIT):
        oid = p.get("ztf_object_id")
        band = str(p.get("ant_passband") or ZTF_FID_BAND.get(p.get("ztf_fid"), "?")).lower()
        if not oid:
            return None
        if code == ANT_ZTF_DET and _finite(p.get("ant_mag")):
            mag = float(p["ant_mag"])
            flux = _sign(p.get("ztf_isdiffpos")) * mag_to_njy(mag)
            point = PhotPoint("ztf", oid, mjd, band, flux, magerr_to_njy(mag, p.get("ant_magerr")), True, broker)
            sso = p.get("ztf_ssnamenr") if is_solar_system(p) else None
            return "ztf", oid, point, sso
        if _finite(p.get("ant_maglim")):
            lim = mag_to_njy(float(p["ant_maglim"]))
            return "ztf", oid, PhotPoint("ztf", oid, mjd, band, math.nan, lim / ZTF_LIMIT_SIGMA, False, broker), None
        return None
    if code == ANT_LSST_DET:
        oid = p.get("lsst_diaSource_diaObjectId")
        flux, err = p.get("lsst_diaSource_psfFlux"), p.get("lsst_diaSource_psfFluxErr")
        if oid is None or not _finite(flux):
            return None
        band = str(p.get("lsst_diaSource_band") or p.get("ant_passband") or "?").lower()
        point = PhotPoint("lsst", str(oid), mjd, band, float(flux), float(err) if _finite(err) else math.nan, True, broker)
        ss = p.get("lsst_diaSource_ssObjectId")
        return "lsst", str(oid), point, (str(ss) if ss not in (None, 0, "0") else None)
    return None


def objects_from_antares_locus(locus, alerts) -> dict[str, SurveyObject]:
    """Split one locus into its survey objects. A locus is a place, not an object."""
    objs: dict[str, SurveyObject] = {}
    for alert in alerts:
        got = _antares_point(alert)
        if got is None:
            continue
        survey, oid, point, sso = got
        obj = objs.get(f"{survey}:{oid}")
        if obj is None:
            obj = objs[f"{survey}:{oid}"] = SurveyObject(
                survey, oid, float(locus.ra), float(locus.dec),
                broker_refs={"antares": locus.locus_id},
                tags=list(locus.tags or []),
                broker_matches=list(locus.catalogs or []),
            )
        obj.points.append(point)
        if sso and obj.sso_id is None:
            obj.sso_id = sso
    return objs


def locus_survey_ids(props: dict) -> dict[str, list[str]]:
    s = props.get("survey") or {}
    ztf = list((s.get("ztf") or {}).get("id") or [])
    if not ztf and props.get("ztf_object_id"):
        ztf = [props["ztf_object_id"]]
    return {"ztf": ztf, "lsst": [str(x) for x in (s.get("lsst") or {}).get("dia_object_id") or []]}


def discover_antares(
    cone: Cone,
    since_mjd: float,
    until_mjd: float | None,
    min_detections: int,
    keep_locus=None,
    search=None,
    ztf_fallback=None,
    stats: dict | None = None,
):
    """
    Yield SurveyObjects from every ANTARES locus in the cone. keep_locus(locus)
    may veto a locus before its alerts are fetched (one call each). If that
    fetch fails, ZTF objects fall back to ztf_fallback(oid) -> (points, err),
    and LSST objects are yielded without points for another source to fill.
    Listing failures propagate: the caller owns the retry of the whole walk.
    """
    if search is None:
        from antares_client.search import search
    stats = stats if stats is not None else {}
    seen: set[str] = set()
    for locus in search(antares_cone_query(cone, since_mjd, until_mjd, min_detections)):
        # the listing pages are fetched one by one, sorted on a field that moves
        # as alerts arrive, so a locus can come back twice in one walk
        if locus.locus_id in seen:
            stats["duplicates"] = stats.get("duplicates", 0) + 1
            continue
        seen.add(locus.locus_id)
        stats["loci"] = stats.get("loci", 0) + 1
        if keep_locus is not None and not keep_locus(locus):
            stats["loci_skipped"] = stats.get("loci_skipped", 0) + 1
            continue
        try:
            objs = objects_from_antares_locus(locus, locus.alerts)
        except NETWORK_ERRORS as e:
            objs = _fallback_objects(locus, f"antares alerts: {e}", ztf_fallback)
        for obj in objs.values():
            obj.points = truncate(obj.points, until_mjd)
            yield obj


def _fallback_objects(locus, err: str, ztf_fallback) -> dict[str, SurveyObject]:
    objs = {}
    ids = locus_survey_ids(locus.properties)
    for survey in ("ztf", "lsst"):
        for oid in ids[survey]:
            obj = SurveyObject(survey, oid, float(locus.ra), float(locus.dec),
                               broker_refs={"antares": locus.locus_id}, tags=list(locus.tags or []),
                               broker_matches=list(locus.catalogs or []), fetch_errors=[err])
            if survey == "ztf" and ztf_fallback is not None:
                points, ferr = ztf_fallback(oid)
                if ferr is None:
                    obj.points = points
                else:
                    obj.fetch_errors.append(f"alerce ztf: {ferr}")
            objs[obj.key] = obj
    return objs


def antares_tns(locus_id: str) -> list[str]:
    """TNS names from ANTARES's catalogue matches, for validation in the report."""
    from antares_client.search import get_by_id

    locus = get_by_id(locus_id)
    rows = (locus.catalog_objects or {}).get("tns_public_objects", []) if locus else []
    return [f"{r.get('name', '?')} {r.get('type') or ''}".strip() for r in rows]


# ------------------------------------------------------------------ ALeRCE


def _alerce():
    from .client import _api_call, _client

    return _api_call, _client


def ztf_points_from_alerce(oid: str, lightcurve: dict) -> list[PhotPoint]:
    points = []
    for d in lightcurve.get("detections") or []:
        if not _finite(d.get("magpsf")):
            continue
        mag = float(d["magpsf"])
        points.append(PhotPoint(
            "ztf", oid, float(d["mjd"]), ZTF_FID_BAND.get(d.get("fid"), "?"),
            _sign(d.get("isdiffpos")) * mag_to_njy(mag), magerr_to_njy(mag, d.get("sigmapsf")), True, "alerce",
        ))
    for n in lightcurve.get("non_detections") or []:
        if not _finite(n.get("diffmaglim")):
            continue
        lim = mag_to_njy(float(n["diffmaglim"]))
        points.append(PhotPoint("ztf", oid, float(n["mjd"]), ZTF_FID_BAND.get(n.get("fid"), "?"),
                                math.nan, lim / ZTF_LIMIT_SIGMA, False, "alerce"))
    return points


def lsst_points_from_alerce(oid: str, lightcurve: dict) -> tuple[list[PhotPoint], str | None]:
    """Detections and forced photometry (Rubin's quiet points), plus any ssObjectId."""
    points, sso = [], None
    for rows, detected in ((lightcurve.get("detections") or [], True),
                           (lightcurve.get("forced_photometry") or [], False)):
        for r in rows:
            if not _finite(r.get("psfFlux")):
                continue
            band = str(r.get("band_name") or r.get("band") or "?").lower()
            err = r.get("psfFluxErr")
            points.append(PhotPoint("lsst", oid, float(r["mjd"]), band, float(r["psfFlux"]),
                                    float(err) if _finite(err) else math.nan, detected, "alerce"))
            if detected and r.get("ssObjectId") not in (None, 0, "0") and sso is None:
                sso = str(r["ssObjectId"])
    return points, sso


def fetch_alerce_ztf(oid: str) -> tuple[list[PhotPoint], str | None]:
    api_call, client = _alerce()
    lc, err = api_call(client.query_lightcurve, oid, format="json")
    return (ztf_points_from_alerce(oid, lc), None) if err is None and lc else ([], err or "empty")


def fetch_alerce_lsst(oid: str) -> tuple[list[PhotPoint], str | None, str | None]:
    """(points, sso_id, error). One call: detections and forced photometry together."""
    api_call, client = _alerce()
    lc, err = api_call(client.query_lightcurve, oid, survey="lsst", format="json")
    if err is not None or not lc:
        return [], None, err or "empty"
    points, sso = lsst_points_from_alerce(oid, lc)
    return points, sso, None


def sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a = math.radians
    c = (math.sin(a(dec1)) * math.sin(a(dec2))
         + math.cos(a(dec1)) * math.cos(a(dec2)) * math.cos(a(ra2 - ra1)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def discover_alerce_lsst(
    cone: Cone, since_mjd: float, until_mjd: float | None, max_pages: int = 50, query=None
) -> tuple[list[SurveyObject], str | None]:
    """
    LSST objects in the cone active since since_mjd. Points are not fetched here: the listing's firstmjd,
    lastmjd and n_det let the caller decide which objects are worth a call.
    Returns (objects, error); on error, what was gathered before it.
    """
    if query is None:
        api_call, client = _alerce()

        def query(**kw):
            return api_call(client.query_objects, survey="lsst", format="json", **kw)

    ra0, dec0, radius_deg = cone
    last = [since_mjd, 99999.0]
    first = [0.0, until_mjd if until_mjd is not None else 99999.0]
    objs: dict[str, SurveyObject] = {}
    for page in range(1, max_pages + 1):
        items, err = query(ra=ra0, dec=dec0, radius=radius_deg * 3600, lastmjd=last, firstmjd=first,
                           page=page, page_size=1000)
        if err is not None:
            return list(objs.values()), f"alerce lsst listing page {page}: {err}"
        items = items or []
        for i in items:
            oid = str(i["oid"])
            obj = objs.get(oid)
            if obj is None:
                obj = objs[oid] = SurveyObject(
                    "lsst", oid, float(i["meanra"]), float(i["meandec"]), broker_refs={"alerce": oid},
                    summary={"firstmjd": i.get("firstmjd"), "lastmjd": i.get("lastmjd"), "n_det": i.get("n_det")},
                )
            if i.get("class_name"):
                label = f"alerce {i.get('classifier_name')}: {i['class_name']} {float(i.get('probability') or 0):.2f}"
                if label not in obj.classifications:
                    obj.classifications.append(label)
        if len(items) < 1000:
            return list(objs.values()), None
    return list(objs.values()), f"alerce lsst listing capped at {max_pages} pages"
