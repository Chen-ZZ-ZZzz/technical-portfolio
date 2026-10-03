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
                broker context (classifications, TNS, broker IDs)

One broker, one adapter, one set of failure modes (2026-10-03, the user's call):
ZTF and LSST both come from ALeRCE - the cone listing (query_objects) and one
light-curve call per object worth it (query_lightcurve: ZTF detections and upper
limits; LSST detections and forced photometry, Rubin's quiet points). ANTARES left
the live path then: before, ZTF came from ANTARES loci, LSST from ALeRCE, and TNS
from ANTARES's crossmatch, which left every LSST object without a locus "not
checked". ANTARES remains only in the retired SSO monitor and in the validation
tool's TNS sample.
"""

import math
from dataclasses import dataclass, field

from requests.exceptions import RequestException

AB_NJY_ZP = 31.4  # m_AB = 31.4 - 2.5 log10(flux / nJy)
ZTF_LIMIT_SIGMA = 5.0  # ZTF diffmaglim is a 5-sigma limit
ZTF_FID_BAND = {1: "g", 2: "r", 3: "i"}
NEGATIVE_DIFF = {"f", "0", "-1", "false"}  # isdiffpos spellings meaning flux went down
# What a broker call can raise past its own retry (ALeRCE errors come back as values).
NETWORK_ERRORS = (RequestException,)

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
    classifications: list[str] = field(default_factory=list)
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


def truncate(points: list[PhotPoint], until_mjd: float | None) -> list[PhotPoint]:
    """Drop epochs after until_mjd (replay: nothing after the replay date exists)."""
    return points if until_mjd is None else [p for p in points if p.mjd <= until_mjd]


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


# per survey: the listing's detection-count field, class field, classifier field
ALERCE_LISTING_FIELDS = {"ztf": ("ndet", "class", "classifier"), "lsst": ("n_det", "class_name", "classifier_name")}
LISTING_PAGE_SIZE = 1000


def _alerce_listing_query(survey: str):
    """query(**kw) -> (items, error) on ALeRCE's object listing for the survey."""
    api_call, client = _alerce()
    # ZTF goes the legacy client path (the multisurvey client refuses survey="ztf"); its
    # JSON answer is a page dict with "items", LSST's a bare list (both seen 2026-10-03)
    extra = {"survey": "lsst"} if survey == "lsst" else {}

    def query(**kw):
        res, err = api_call(client.query_objects, format="json", **extra, **kw)
        return (res.get("items", []) if isinstance(res, dict) else res), err
    return query


def discover_alerce(
    survey: str, cone: Cone, since_mjd: float, until_mjd: float | None, max_pages: int = 50, query=None
) -> tuple[list[SurveyObject], str | None]:
    """
    The survey's objects in the cone active since since_mjd. Points are not fetched here:
    the listing's firstmjd, lastmjd and detection count let the caller decide which objects
    are worth a light-curve call. Returns (objects, error); on error, what was gathered before it.
    """
    query = query or _alerce_listing_query(survey)
    n_field, class_field, classifier_field = ALERCE_LISTING_FIELDS[survey]
    ra0, dec0, radius_deg = cone
    last = [since_mjd, 99999.0]
    first = [0.0, until_mjd if until_mjd is not None else 99999.0]
    objs: dict[str, SurveyObject] = {}
    for page in range(1, max_pages + 1):
        items, err = query(ra=ra0, dec=dec0, radius=radius_deg * 3600, lastmjd=last, firstmjd=first,
                           page=page, page_size=LISTING_PAGE_SIZE)
        if err is not None:
            return list(objs.values()), f"alerce {survey} listing page {page}: {err}"
        items = items or []
        for i in items:  # LSST lists an object once per classifier: merged here
            oid = str(i["oid"])
            obj = objs.get(oid)
            if obj is None:
                obj = objs[oid] = SurveyObject(
                    survey, oid, float(i["meanra"]), float(i["meandec"]), broker_refs={"alerce": oid},
                    summary={"firstmjd": i.get("firstmjd"), "lastmjd": i.get("lastmjd"), "n_det": i.get(n_field)},
                )
            if i.get(class_field):
                label = (f"alerce {i.get(classifier_field)}: {i[class_field]} "
                         f"{float(i.get('probability') or 0):.2f}")
                if label not in obj.classifications:
                    obj.classifications.append(label)
        if len(items) < LISTING_PAGE_SIZE:
            return list(objs.values()), None
    return list(objs.values()), f"alerce {survey} listing capped at {max_pages} pages"


def alerce_newest_in_cone(survey: str, cone: Cone, floor_mjd: float | None,
                          query=None) -> tuple[dict | None, str | None]:
    """
    ALeRCE's object of the survey with the latest lastmjd in the cone, among those with
    lastmjd >= floor_mjd (None: the whole cone - heavy for LSST, ~55 s on ecdfs, 2026-10-02).
    Returns (item or None, error). One row, ordered on the server: with a recent floor it is
    cheap (~2 s LSST, ~1 s ZTF), since only the last nights' objects are sorted.
    """
    query = query or _alerce_listing_query(survey)
    ra0, dec0, radius_deg = cone
    kw = dict(ra=ra0, dec=dec0, radius=radius_deg * 3600, order_by="lastmjd", order_mode="DESC", page=1, page_size=1)
    if floor_mjd is not None:
        kw["lastmjd"] = [floor_mjd, 99999.0]
    items, err = query(**kw)
    if err is not None:
        return None, f"alerce {survey} newest-in-cone check: {err}"
    return (items[0] if items else None), None
