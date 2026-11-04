"""
finra_fetcher.py — supplemental new-issue coverage from FINRA's Query API.

Queries the corporateAndAgencySecurities dataset (the TRACE-fed reference
record for every corporate bond that has traded in the past 10 years) for
securities that recently appeared, and surfaces ones EDGAR missed — above
all Rule 144A deals, which never file with the SEC. Replaces the dead
Business Wire / GlobeNewswire press-release scraper.

Data is published by FINRA for NON-COMMERCIAL use (this pipeline is a
personal brief). Free credentials: register an Individual account at
developer.finra.org, create an API credential, and set:

    FINRA_API_CLIENT_ID
    FINRA_API_CLIENT_SECRET

Degrades gracefully: if credentials are not configured or any call fails,
returns no deals and the pipeline continues on EDGAR alone.

New-issue detection is two-layered, because the dataset is a living master
file rather than a "daily list":
  1. systemControlDate >= a few business days ago (server-side filter) —
     catches records recently added or touched;
  2. a persistent seen-CUSIP file (docs/finra_seen_cusips.json) — only
     CUSIPs never surfaced before are treated as new. The caller saves the
     updated file only after the episode is validated, so an aborted run
     never swallows a deal.

Validate live access after registering credentials with:

    python finra_fetcher.py --validate
"""

import json
import logging
import os
import re
from base64 import b64encode
from datetime import datetime, timedelta
from pathlib import Path

import requests

from config import FINRA_SEEN_CUSIPS_PATH, MAX_TRACE_DEALS
from utils import prior_business_day

logger = logging.getLogger(__name__)

TOKEN_URL = "https://ews.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"
DATA_URL = "https://api.finra.org/data/group/FixedIncomeMarket/name/corporateAndAgencySecurities"
REQUEST_TIMEOUT = 20
LOOKBACK_BDAYS = 5          # systemControlDate window for "recently appeared"
SEEN_RETENTION_DAYS = 180   # how long surfaced CUSIPs stay in the seen file


def fetch_trace_deals(edgar_deals=None):
    """Fetch recently-appeared bonds from FINRA TRACE not already covered by EDGAR.

    Args:
        edgar_deals: list of deal dicts already fetched from EDGAR (for dedup).

    Returns:
        (deals, updated_seen): deals is a list of standard deal dicts
        (source "FINRA TRACE"); updated_seen is the seen-CUSIP store the
        caller must persist via save_seen_cusips() AFTER the episode is
        validated. Returns ([], None) when skipped or failed.
    """
    client_id = os.getenv("FINRA_API_CLIENT_ID")
    client_secret = os.getenv("FINRA_API_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.info("FINRA: credentials not configured — skipping TRACE supplemental fetch")
        return [], None

    try:
        token = _get_token(client_id, client_secret)
        records = _query_recent_securities(token)
    except Exception as e:
        logger.warning(f"FINRA: TRACE fetch failed (non-fatal): {e}")
        return [], None

    logger.info(f"FINRA: {len(records)} recently-touched security records")

    seen = load_seen_cusips()
    seen_cusips = {e["cusip"] for e in seen}
    edgar_issuers = [d.get("issuer", "") for d in (edgar_deals or []) if d.get("issuer")]

    deals = []
    newly_seen = []
    for rec in records:
        cusip = (rec.get("cusip") or "").strip()
        if not cusip or cusip in seen_cusips:
            continue
        seen_cusips.add(cusip)

        keep, why = _is_reportable(rec)
        if not keep:
            logger.debug(f"FINRA: skipping {cusip} ({rec.get('issuerName')}): {why}")
            continue
        # Remember every reportable CUSIP we evaluated, so tomorrow's run
        # doesn't re-surface it even if it is dropped below.
        newly_seen.append({"cusip": cusip, "first_seen": datetime.today().strftime("%Y-%m-%d")})

        if _matches_edgar(rec.get("issuerName", ""), edgar_issuers):
            logger.info(f"FINRA: {rec.get('issuerName')} already covered via EDGAR — skipping")
            continue
        if len(deals) < MAX_TRACE_DEALS:
            deals.append(_to_deal_dict(rec))

    logger.info(f"FINRA: {len(deals)} supplemental TRACE deals "
                f"({sum(1 for d in deals if d.get('is_144a'))} are 144A)")
    updated_seen = _prune_seen(seen + newly_seen)
    return deals, updated_seen


# ── FINRA API internals ────────────────────────────────────────────────────

def _get_token(client_id, client_secret):
    """OAuth2 client-credentials grant against the FINRA Identity Platform."""
    basic = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {basic}"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("FINRA token response had no access_token")
    return token


def _query_recent_securities(token):
    """Pull records whose systemControlDate falls in the lookback window."""
    cutoff = _business_days_ago(LOOKBACK_BDAYS)
    body = {
        "limit": 2000,
        "compareFilters": [
            {"fieldName": "systemControlDate", "compareType": "GTE", "fieldValue": cutoff},
        ],
        "fields": [
            "cusip", "issuerName", "couponRate", "maturityDate", "couponType",
            "moodysRating", "standardAndPoorsRating", "moodyRatingDate",
            "standardAndPoorsRatingDate", "traceGradeCode", "is144A",
            "isConvertible", "isCallable", "nextCallDate", "industryGroup",
            "productSubTypeCode", "systemControlDate", "lastTradeDate",
        ],
    }
    resp = requests.post(
        DATA_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # Query API returns either a bare list or {"data": [...]} depending on version
    return data if isinstance(data, list) else data.get("data", [])


# ── Record filtering & mapping ─────────────────────────────────────────────

def _is_reportable(rec):
    """Decide whether a master-file record looks like a fresh IG new issue."""
    if str(rec.get("isConvertible", "")).upper().startswith("Y"):
        return False, "convertible"

    sub_type = str(rec.get("productSubTypeCode") or "").upper()
    if any(tag in sub_type for tag in ("AGCY", "AGENCY", "GSE", "CHRC", "CHURCH", "ELN")):
        return False, f"non-corporate subtype {sub_type}"

    maturity = _parse_date(rec.get("maturityDate"))
    if not maturity or maturity <= datetime.today():
        return False, "matured or missing maturity"
    # New issues have at least ~1y tenor; sub-year paper is CP/remnants
    if maturity < datetime.today() + timedelta(days=360):
        return False, "tenor under a year"

    if rec.get("couponRate") in (None, ""):
        return False, "no coupon"

    grade = str(rec.get("traceGradeCode") or "")
    moodys = str(rec.get("moodysRating") or "")
    sp = str(rec.get("standardAndPoorsRating") or "")
    if _looks_hy(grade, moodys, sp):
        return False, f"high yield ({grade or moodys or sp})"

    # A genuinely NEW issue was rated recently. Old bonds re-entering the
    # master (first trade in years, control-record churn) carry old rating
    # dates — this is the main guard against false "new issue" positives.
    rating_dates = [_parse_date(rec.get(f)) for f in
                    ("moodyRatingDate", "standardAndPoorsRatingDate")]
    rating_dates = [d for d in rating_dates if d]
    fresh_cutoff = datetime.today() - timedelta(days=30)
    if rating_dates and max(rating_dates) < fresh_cutoff:
        return False, "rating dates stale (existing bond, not a new issue)"

    return True, ""


def _looks_hy(grade, moodys, sp):
    """True when the record is identifiably high yield.

    Split-rated names (one agency IG, one HY) are kept — crossover credits
    are exactly the kind of deal worth a mention.
    """
    if grade.upper().startswith("H"):   # traceGradeCode "High Yield"
        return True
    hy_votes = ig_votes = 0
    for r in (moodys.strip(), sp.strip()):
        if not r:
            continue
        if _rating_is_hy(r):
            hy_votes += 1
        else:
            ig_votes += 1
    return hy_votes > 0 and ig_votes == 0


def _rating_is_hy(r):
    """Classify one agency rating symbol as high yield."""
    # IG prefixes first — some contain HY prefixes as substrings (BBB vs BB)
    for p in ("Aaa", "Aa", "AAA", "AA", "BBB", "Baa"):
        if r.startswith(p):
            return False
    for p in ("Caa", "Ca", "Ba", "CCC", "CC", "BB", "D"):
        if r.startswith(p):
            return True
    if r.startswith("A"):   # A1/A2/A3/A+/A-/A — IG
        return False
    return r.startswith("B")   # B1/B2/B3/B+/B- — HY


def _to_deal_dict(rec):
    """Map a TRACE record onto the standard deal dict schema."""
    maturity = _parse_date(rec.get("maturityDate"))
    tenor = None
    if maturity:
        years = round((maturity - datetime.today()).days / 365.25)
        tenor = str(years) if years >= 1 else None

    ratings = {}
    if rec.get("moodysRating"):
        ratings["moodys"] = str(rec["moodysRating"]).strip()
    if rec.get("standardAndPoorsRating"):
        ratings["sp"] = str(rec["standardAndPoorsRating"]).strip()

    call_structure = None
    if str(rec.get("isCallable", "")).upper().startswith("Y"):
        call_structure = "callable"
        if rec.get("nextCallDate"):
            call_structure += f" (next call {rec['nextCallDate']})"

    is_144a = str(rec.get("is144A", "")).upper().startswith("Y")

    return {
        "issuer":          (rec.get("issuerName") or "Unknown").strip().title(),
        "size":            None,   # not in the TRACE reference record
        "tenor":           tenor,
        "maturity":        rec.get("maturityDate"),
        "coupon":          f"{rec.get('couponRate')}%",
        "spread":          None,
        "ratings":         ratings or None,
        "use_of_proceeds": None,
        "bookrunners":     None,
        "call_structure":  call_structure,
        "filing_date":     rec.get("systemControlDate", ""),
        "accession_no":    None,
        "source":          "FINRA TRACE" + (" (144A)" if is_144a else ""),
        "cusip":           rec.get("cusip"),
        "is_144a":         is_144a,
        "industry":        rec.get("industryGroup"),
    }


_GENERIC_NAME_TOKENS = {
    "inc", "corp", "co", "llc", "ltd", "plc", "the", "of", "de", "and",
    "company", "corporation", "holdings", "group", "financial", "finance",
}


def _name_tokens(name):
    """Lowercased significant words, punctuation stripped, suffixes dropped."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return [w for w in cleaned.split() if len(w) >= 3 and w not in _GENERIC_NAME_TOKENS]


def _matches_edgar(issuer_name, edgar_issuers):
    """True when an EDGAR issuer's significant words appear in this name."""
    name_toks = set(_name_tokens(issuer_name))
    for issuer in edgar_issuers:
        toks = _name_tokens(issuer)
        if not toks:
            continue
        overlap = sum(1 for w in toks if w in name_toks)
        # Multi-word names need 2 shared words; single-word names (Meta,
        # Equinix, GATX...) match on that one word.
        if overlap >= 2 or (len(toks) == 1 and overlap == 1):
            return True
    return False


# ── Seen-CUSIP persistence ─────────────────────────────────────────────────

def load_seen_cusips():
    """Load the surfaced-CUSIP store. Returns list of {cusip, first_seen}."""
    path = Path(FINRA_SEEN_CUSIPS_PATH)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"FINRA: could not load seen-CUSIP file: {e}")
        return []


def save_seen_cusips(entries):
    """Persist the seen-CUSIP store. Call only after the episode is validated."""
    if entries is None:
        return
    path = Path(FINRA_SEEN_CUSIPS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        logger.info(f"FINRA: seen-CUSIP store saved ({len(entries)} entries)")
    except Exception as e:
        logger.warning(f"FINRA: could not save seen-CUSIP file: {e}")


def _prune_seen(entries):
    cutoff = (datetime.today() - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
    unique = {}
    for e in entries:
        if e.get("cusip") and e.get("first_seen", "") >= cutoff:
            unique[e["cusip"]] = e
    return sorted(unique.values(), key=lambda e: e["first_seen"])


# ── Utilities ──────────────────────────────────────────────────────────────

def _parse_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(value)[:10], fmt)
        except ValueError:
            continue
    return None


def _business_days_ago(n):
    d = datetime.today()
    back = 0
    while back < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            back += 1
    return d.strftime("%Y-%m-%d")


# ── Live validation (run after registering credentials) ───────────────────

def _validate():
    """Answer the two open scoping questions against the live API."""
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    client_id = os.getenv("FINRA_API_CLIENT_ID")
    client_secret = os.getenv("FINRA_API_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Set FINRA_API_CLIENT_ID and FINRA_API_CLIENT_SECRET in .env first.")
        return 1

    print("1) Token grant...")
    token = _get_token(client_id, client_secret)
    print("   OK")

    print("2) Dataset access (does the free tier include corporateAndAgencySecurities?)...")
    records = _query_recent_securities(token)
    print(f"   OK — {len(records)} records with systemControlDate in the last {LOOKBACK_BDAYS} business days")

    print("3) Field sanity + new-issue semantics:")
    from collections import Counter
    subtypes = Counter(str(r.get("productSubTypeCode")) for r in records)
    print("   productSubTypeCode values:", dict(subtypes))
    fresh = [r for r in records if _is_reportable(r)[0]]
    print(f"   records passing new-issue heuristics: {len(fresh)}")
    for r in fresh[:10]:
        print(f"   - {r.get('issuerName')} | {r.get('couponRate')}% | mat {r.get('maturityDate')} "
              f"| Moody's {r.get('moodysRating')} / S&P {r.get('standardAndPoorsRating')} "
              f"| 144A={r.get('is144A')} | ctrl {r.get('systemControlDate')}")
    print("\nCross-check the list above against deals that actually priced this week.")
    return 0


if __name__ == "__main__":
    import sys
    if "--validate" in sys.argv:
        sys.exit(_validate())
    print("Usage: python finra_fetcher.py --validate")
