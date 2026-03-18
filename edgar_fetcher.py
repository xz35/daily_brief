"""
edgar_fetcher.py — query SEC EDGAR for prior-day FWP filings and parse deal terms.

FWP (Free Writing Prospectus) filings are the primary source for IG bond
new issues. The SEC EDGAR full-text search API is public and requires no
API key, but DOES require a descriptive User-Agent header with contact info.

Parsing notes:
- FWP documents vary widely in format (HTML table, plain text, PDF).
- This parser handles HTML and plain text. PDFs are logged and skipped.
- Expect ~80% parse success on first pass; iterate as needed.
- The pipeline never crashes on a single filing failure.
"""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from config import (
    EDGAR_SEARCH_URL,
    EDGAR_USER_AGENT,
    IG_RATING_INDICATORS,
    MAX_DEALS_PER_EPISODE,
)
from utils import prior_business_day

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": EDGAR_USER_AGENT}
REQUEST_DELAY = 1.0    # seconds between EDGAR requests (be polite — SEC rate limits)
REQUEST_TIMEOUT = 15   # seconds per request
MAX_RETRIES = 2        # retries on 503 with exponential backoff

SUBMISSIONS_BASE = "https://data.sec.gov/submissions/CIK{}.json"
COMPANY_FACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"


def fetch_deals(date=None):
    """Fetch and parse IG bond new issues for the given date (prior business day by default).

    Returns:
        list[dict]: Parsed deal records. Empty list on zero-deal days or total failure.
    """
    target_date = date or prior_business_day()
    logger.info(f"Querying EDGAR FWP filings for {target_date}")

    filings = _search_edgar(target_date)
    if not filings:
        logger.info(f"No FWP filings found for {target_date}")
        return []

    logger.info(f"Found {len(filings)} FWP filings — pre-filtering")
    filings = _prefilter_filings(filings)
    logger.info(f"After pre-filter: {len(filings)} candidate bond filings to fetch")

    deals = []
    for filing in filings:
        if len(deals) >= MAX_DEALS_PER_EPISODE:
            logger.info(f"Hit MAX_DEALS_PER_EPISODE ({MAX_DEALS_PER_EPISODE}), stopping")
            break
        try:
            deal = _process_filing(filing)
            if deal:
                deals.append(deal)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            accession = filing.get("adsh", "unknown")
            logger.warning(f"Failed to process filing {accession}: {e}")

    ig_deals = [d for d in deals if _is_investment_grade(d)]
    logger.info(f"EDGAR: {len(deals)} deals parsed → {len(ig_deals)} IG deals")
    return ig_deals


# ── EDGAR search ──────────────────────────────────────────────────────────

def _search_edgar(date):
    """Query the EDGAR full-text search API for FWP filings on a given date.

    Returns all FWP filings for the date — structured product vehicles are
    filtered out downstream by _prefilter_filings(). Keyword filtering in
    the EFTS query is unreliable for FWP document content and returned 0
    results in testing; we rely on the pre-filter instead.
    """
    params = {
        # 424B5 = prospectus supplements for shelf-registered WKSIs (Novartis, ING, HPE, utilities, etc.)
        # FWP  = free writing prospectus (bank structured note term sheets + some corporate term sheets)
        # 424B2 intentionally omitted — 391 filings/day, overwhelmingly structured products from banks.
        # If coverage gaps are found, add 424B2 in a future iteration.
        "forms": "FWP,424B5",
        "dateRange": "custom",
        "startdt": date,
        "enddt": date,
    }
    try:
        resp = requests.get(
            EDGAR_SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        return [h["_source"] for h in hits if "_source" in h]
    except Exception as e:
        logger.error(f"EDGAR search API error: {e}")
        return []


# ── Pre-filtering ─────────────────────────────────────────────────────────

# Structured product finance subsidiaries file FWPs too, but they are NOT
# the traditional IG bond deals we want. Filter them out by name pattern
# before spending any HTTP requests on document fetching.
_STRUCTURED_PRODUCT_PATTERNS = [
    r"\bfinance\s+(llc|corp\.?|co\.?)\b",     # "Goldman Sachs Finance Corp", "GS Finance Corp"
    r"\bfinancial\s+(co\.?|llc|corp\.?)\b",    # "JPMorgan Chase Financial Co. LLC"
    r"\bissuance\s+trust\b",                    # "Synchrony Card Issuance Trust"
    r"\bfunding\s+(llc|corp\.?|trust)\b",       # "Funding LLC"
    r"\bsecurities\s+(llc|corp\.?)\b",          # "Barclays Capital Securities LLC"
    r"\bholdings\s+inc\b.*\bmarkets\b",         # "Citigroup Global Markets Holdings Inc"
    r"\bsecured\s+(notes|lending)\b",           # ABS vehicles
]

def _prefilter_filings(filings):
    """Remove obvious structured product vehicles before fetching documents.

    Prioritizes non-financial issuers (telecoms, pharma, utilities, etc.) over
    bank holdcos, because banks file hundreds of structured note FWPs that look
    like bond deals but aren't. Non-financial SIC codes (< 6000 or > 6999) are
    almost always traditional bond issuers.

    Caps at MAX_DEALS_PER_EPISODE * 4 to leave room for the content-based
    filter in _parse_deal_terms() to reject structured notes before we hit
    the MAX_DEALS_PER_EPISODE cap in fetch_deals().
    """
    import re as _re
    filtered = []
    for f in filings:
        display_names = f.get("display_names") or []
        name_lower = display_names[0].lower() if display_names else ""
        if any(_re.search(p, name_lower) for p in _STRUCTURED_PRODUCT_PATTERNS):
            logger.debug(f"Pre-filter skip (structured product): {display_names[0] if display_names else 'unknown'}")
            continue
        filtered.append(f)

    # Deduplicate by CIK: keep only the most recent filing per company.
    # Banks filing 7+ structured note FWPs on the same day would otherwise
    # consume most of the candidate cap, pushing out real bond issuers.
    seen_ciks = {}
    deduped = []
    for f in filtered:
        ciks = f.get("ciks") or []
        cik = ciks[0] if ciks else ""
        if cik and cik in seen_ciks:
            continue  # already have a filing from this issuer
        if cik:
            seen_ciks[cik] = True
        deduped.append(f)

    # Sort: non-financial SICs first (< 6000 or > 6999), then financial holdcos.
    # Non-financial issuers (pharma, utilities, tech) are almost always filing
    # traditional bonds; financial holdcos may be filing structured notes.
    def _sic_priority(f):
        sics = f.get("sics") or []
        sic = int(sics[0]) if sics else 0
        is_financial = 6000 <= sic <= 6999
        return (1 if is_financial else 0)

    deduped.sort(key=_sic_priority)

    cap = MAX_DEALS_PER_EPISODE * 4
    logger.debug(f"Pre-filter: {len(deduped)} unique-CIK candidates (capped at {cap})")
    return deduped[:cap]


# ── Filing document retrieval ─────────────────────────────────────────────

def _process_filing(source):
    """Fetch the filing document and parse deal terms from it."""
    # Actual EDGAR API field names (validated against live response)
    accession_no = source.get("adsh", "")
    ciks = source.get("ciks") or []
    cik_raw = ciks[0] if ciks else ""
    display_names = source.get("display_names") or []
    entity_name = _clean_display_name(display_names[0]) if display_names else "Unknown"

    if not accession_no or not cik_raw:
        return None

    cik = str(int(cik_raw))   # strip leading zeros: "0000927971" → "927971"

    # Use submissions API to get the exact primary document filename.
    # This avoids having to parse the index page and gives us a direct URL.
    time.sleep(REQUEST_DELAY)
    primary_doc = _get_primary_document_filename(cik, accession_no)

    doc_url = _build_document_url(accession_no, cik, primary_doc)
    if not doc_url:
        logger.warning(f"Could not construct document URL for {accession_no}")
        return None

    logger.info(f"Fetching filing: {entity_name} ({accession_no}) → {primary_doc or 'index.htm fallback'}")
    content, content_type = _fetch_document(doc_url, is_index=(primary_doc is None))
    if not content:
        return None

    deal = _parse_deal_terms(content, content_type)
    if deal:
        deal["issuer"] = deal.get("issuer") or entity_name
        deal["accession_no"] = accession_no
        deal["filing_date"] = source.get("file_date", "")
        deal["source"] = "EDGAR FWP"
        deal["cik"] = cik
        # Enrich with EDGAR company facts (balance sheet context for synthesis)
        time.sleep(REQUEST_DELAY)
        company_facts = _fetch_company_facts(cik)
        if company_facts:
            deal["company_facts"] = company_facts
    return deal


def _get_primary_document_filename(cik, accession_no):
    """Look up the primaryDocument filename for an accession via submissions API.

    data.sec.gov/submissions/ returns filing metadata including the exact
    primaryDocument filename — more reliable than parsing the index page.
    Returns None on failure (caller will fall back to index.htm approach).
    """
    try:
        padded = str(cik).zfill(10)
        url = SUBMISSIONS_BASE.format(padded)
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        forms = recent.get("form", [])

        # Normalize accession number format for comparison (with dashes)
        target = accession_no.replace("-", "")
        for i, acc in enumerate(accession_numbers):
            if acc.replace("-", "") == target:
                if i < len(primary_docs):
                    filename = primary_docs[i]
                    logger.debug(f"submissions API: primaryDocument={filename} form={forms[i] if i < len(forms) else '?'}")
                    return filename
        logger.debug(f"Accession {accession_no} not found in recent filings for CIK {cik}")
        return None
    except Exception as e:
        logger.debug(f"submissions API lookup failed for CIK {cik}: {e}")
        return None


def _build_document_url(accession_no, cik, primary_doc=None):
    """Construct direct document URL.

    If primary_doc filename is known (from submissions API), builds a direct
    URL to the document. Falls back to the -index.htm URL otherwise.
    """
    try:
        accession_nodash = accession_no.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/"
        if primary_doc:
            return base + primary_doc
        # Fallback: index page
        return base + f"{accession_nodash}-index.htm"
    except Exception:
        return None


def _clean_display_name(display_name):
    """Extract company name from EDGAR display_names string.

    e.g. 'BANK OF MONTREAL /CAN/  (BMO, ...)  (CIK 0000927971)'
         → 'Bank of Montreal'
    """
    name = display_name.split("(")[0].strip()          # drop ticker list + CIK
    name = re.sub(r"/[A-Z]+/", "", name).strip()       # drop /CAN/, /DE/, etc.
    return name.title()


def _fetch_document(url, is_index=False):
    """Fetch the FWP document content.

    If is_index=True, the URL points to the filing index page and we need to
    parse it to find the primary document link. If is_index=False (normal
    case), the URL is already the direct document URL from the submissions API.
    """
    try:
        # Skip PDFs regardless of path
        if url.lower().endswith(".pdf"):
            logger.info(f"Skipping PDF filing: {url}")
            return None, None

        resp = _get_with_retry(url)
        if resp is None:
            return None, None

        if is_index:
            # Parse the index HTML to find the main document link
            soup = BeautifulSoup(resp.text, "lxml")
            doc_link = _find_primary_document(soup, url)
            if not doc_link:
                logger.warning(f"No primary document found at {url}")
                return None, None
            if doc_link.lower().endswith(".pdf"):
                logger.info(f"Skipping PDF filing: {doc_link}")
                return None, None
            time.sleep(REQUEST_DELAY)
            doc_resp = _get_with_retry(doc_link)
            if doc_resp is None:
                return None, None
            content_type = "html" if "<html" in doc_resp.text[:500].lower() else "text"
            return doc_resp.text, content_type
        else:
            content_type = "html" if "<html" in resp.text[:500].lower() else "text"
            return resp.text, content_type

    except Exception as e:
        logger.warning(f"Failed to fetch document at {url}: {e}")
        return None, None


def _find_primary_document(soup, base_url):
    """Find the primary FWP document link from the EDGAR filing index page."""
    # Look for links ending in .htm/.html/.txt that are not the index itself
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(href.lower().endswith(ext) for ext in (".htm", ".html", ".txt")):
            if "index" not in href.lower():
                # Make absolute URL
                if href.startswith("http"):
                    candidates.append(href)
                else:
                    base = base_url.rstrip("/")
                    candidates.append(f"{base}/{href.lstrip('/')}")

    # Prefer .htm files (more likely to be formatted term sheets)
    htm = [c for c in candidates if c.lower().endswith((".htm", ".html"))]
    return htm[0] if htm else (candidates[0] if candidates else None)


# ── Deal term parsing ─────────────────────────────────────────────────────

def _parse_deal_terms(content, content_type):
    """Parse deal terms from FWP document content.

    Returns a dict with extracted fields (None for unparsed fields).
    Returns None if content doesn't look like a bond deal at all.
    """
    if content_type == "html":
        soup = BeautifulSoup(content, "lxml")
        text = soup.get_text(separator=" ")
    else:
        text = content

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    # Exclude structured notes, preferred stock, and equity-linked products.
    # These file as FWP too but are NOT traditional IG bond new issues.
    structured_note_signals = [
        # Structured notes / market-linked products
        "reference asset", "reference stock", "reference index",
        "auto-call", "autocall", "auto call",
        "contingent coupon", "contingent interest",
        "knock-in", "barrier level",
        "participation rate",
        "market linked", "market-linked",
        "buffered", "buffer amount",
        "per unit", "per note of $1,000",
        "optimization notes", "performance notes",
        "return linked", "return-linked",
        # Preferred and common stock (not bonds)
        "preferred stock", "term preferred",
        "common stock", "ordinary shares",
        "depositary shares",
        # Equity-linked / hybrid
        "mandatory convertible",
        "contingent convertible",
    ]
    text_lower = text.lower()
    for sig in structured_note_signals:
        if sig in text_lower:
            logger.info(f"Skipping structured product filing (found '{sig}')")
            return None

    # Require at least one strong indicator of a traditional bond offering.
    # Unlike the exclusion above, these signals rarely appear in structured products.
    bond_signals = [
        "aggregate principal amount",
        "joint book-running manager",
        "bookrunning manager",
        "senior unsecured notes",
        "senior notes due",
        "treasury spread",
        "reoffer spread",
        "re-offer spread",
        "spread to treasury",
    ]
    if not any(sig in text_lower for sig in bond_signals):
        logger.info("Filing lacks traditional bond new-issue signals — skipping")
        return None

    return {
        "issuer":        _extract_issuer(text),
        "size":          _extract_size(text),
        "tenor":         _extract_tenor(text),
        "maturity":      _extract_maturity(text),
        "coupon":        _extract_coupon(text),
        "spread":        _extract_spread(text),
        "ratings":       _extract_ratings(text),
        "use_of_proceeds": _extract_use_of_proceeds(text),
        "bookrunners":   _extract_bookrunners(text),
        "call_structure": _extract_call_structure(text),
    }


# ── Field extractors (regex-based) ───────────────────────────────────────

def _extract_issuer(text):
    patterns = [
        # "Issuer: Company Name" followed by field separator
        r"Issuer[:\s]+([A-Z][A-Za-z0-9\s,\.&]{3,60}(?:Inc\.|Corp\.|LLC|Ltd\.|L\.P\.|N\.A\.|PLC|Company|Corporation)?)\s*(?:Security|Securities|Format|Trade|Expected|Rating|Principal|Ticker|CUSIP|\(the)",
        r"Issuer[:\s]+([A-Z][A-Za-z0-9\s,\.&]{3,60}(?:Inc\.|Corp\.|LLC|Ltd\.|L\.P\.|N\.A\.|PLC|Company|Corporation))",
        r"(?:by\s+)([A-Z][A-Za-z0-9\s,\.&]{3,50}(?:Inc\.|Corp\.|LLC|Ltd\.|L\.P\.|N\.A\.|PLC))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result = m.group(1).strip()
            if not result:
                continue
            # Reject if result looks like a prospectus sentence
            reject_words = ["prospectus", "relating", "registration", "dated", "pursuant",
                            "supplement", "filed", "offering", "security"]
            if any(w in result.lower() for w in reject_words):
                continue
            return result[:80]
    return None


def _extract_size(text):
    # Try labeled "Aggregate Principal Amount" field with full dollar number first
    labeled = re.search(
        r"Aggregate Principal Amount(?:\s+Offered)?[:\s]+\$([\d,]+)",
        text, re.IGNORECASE
    )
    if labeled:
        raw = labeled.group(1).replace(",", "")
        try:
            val = int(raw)
            if val >= 1_000_000_000:
                return f"${val / 1_000_000_000:.3g} billion"
            elif val >= 1_000_000:
                return f"${val / 1_000_000:.0f} million"
        except ValueError:
            pass

    # Fall back to "Principal Amount: $1,300,000,000" or "$X billion/million" inline
    patterns = [
        r"Principal Amount[:\s]+\$([\d,]+)",
        r"\$([\d,]+(?:\.\d+)?)\s*(billion|million)\s*(?:aggregate principal|principal amount|of\s+[\w\s]+notes)",
        r"(?:aggregate principal amount of|principal amount:?)\s*\$([\d,]+(?:\.\d+)?)\s*(billion|million)",
        r"\$([\d,]+(?:\.\d+)?)\s*(billion|million)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 1:
                # Full number format — normalize
                raw = groups[0].replace(",", "")
                try:
                    val = int(raw)
                    if val >= 1_000_000_000:
                        return f"${val / 1_000_000_000:.3g} billion"
                    elif val >= 1_000_000:
                        return f"${val / 1_000_000:.0f} million"
                except ValueError:
                    pass
            elif len(groups) >= 2:
                return f"${groups[0]} {groups[1]}"
    return None


def _extract_tenor(text):
    patterns = [
        r"(\d+)[\s-]?year\s+(?:senior\s+)?notes",
        r"(\d+)[\s-]?year\s+(?:fixed|floating)",
        r"Tenor[:\s]+(\d+[\s\w]+(?:year|month)s?)",
    ]
    return _first_match(text, patterns)


def _extract_maturity(text):
    patterns = [
        r"(?:Maturity Date|Maturity)[:\s]+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"(?:due|Due)\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"(?:maturing|Maturing)\s+(?:on\s+)?([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"(\d{4}-\d{2}-\d{2})\s*(?:\(maturity\))?",
    ]
    return _first_match(text, patterns)


def _extract_coupon(text):
    patterns = [
        r"(?:Coupon|Interest Rate|Coupon Rate)[:\s]+(\d+\.?\d*)\s*%",
        r"(\d+\.?\d*)\s*%\s*(?:per annum|Senior Notes|Notes)",
        r"(\d+\.?\d*)\s*%\s*(?:fixed|Fixed)",
    ]
    m = _first_match(text, patterns)
    return f"{m}%" if m and "%" not in m else m


def _extract_spread(text):
    patterns = [
        # Labeled spread fields — highest confidence
        r"(?:Spread to Benchmark Treasury|Spread to Treasury|Treasury Spread|re-offer spread|reoffer spread)[:\s]+\+?(\d+)\s*(?:basis points|bps|bp)",
        # T+NNN bps — require explicit bps unit to avoid matching "T+3" settlement dates
        r"(?:T\+|Treasury\s*\+\s*)(\d+)\s+(?:basis points|bps|bp)",
        r"\+(\d+)\s+(?:bps?|basis points)\s+(?:to|over)\s+(?:the\s+)?(?:\d+-year\s+)?Treasury",
    ]
    m = _first_match(text, patterns)
    return f"T+{m} bps" if m and "T+" not in m else m


def _extract_ratings(text):
    """Extract credit ratings. Handles two common formats:

    Style A (SDG&E): "A1 (stable) by Moody's Investors Service"
    Style B (Southern Co): "Baa2/BBB/BBB- (Moody's/Standard & Poor's/Fitch)"
    Style C (labeled): "Moody's: Baa2  S&P: BBB"
    """
    ratings = {}

    # Style A: "RATING (qualifier) by Moody's / S&P Global / Fitch"
    # Rating can be 1-5 chars: "A", "Aa1", "BBB-", "Baa2", etc.
    style_a = [
        (r"([A-Za-z0-9+\-]{1,5})\s*\([^)]*\)\s*by\s+Moody", "moodys"),
        (r"([A-Za-z0-9+\-]{1,5})\s*\([^)]*\)\s*by\s+S&P", "sp"),
        (r"([A-Za-z0-9+\-]{1,5})\s*\([^)]*\)\s*by\s+Fitch", "fitch"),
    ]
    for pattern, label in style_a:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            ratings[label] = m.group(1).strip()

    # Style B: "Baa2 (X)/BBB (Y)/BBB- (Z) (Moody's/S&P/Fitch)"
    style_b = re.search(
        r"([A-Za-z0-9+\-]{2,5})\s*\([^)]+\)/([A-Za-z0-9+\-]{2,5})\s*\([^)]+\)/([A-Za-z0-9+\-]{2,5})\s*\([^)]+\)\s*\(Moody",
        text, re.IGNORECASE
    )
    if style_b and not ratings:
        ratings["moodys"] = style_b.group(1).strip()
        ratings["sp"]     = style_b.group(2).strip()
        ratings["fitch"]  = style_b.group(3).strip()

    # Style C: "Moody's: Baa2" / "S&P: BBB"
    if not ratings:
        for label, pattern in [
            ("moodys", r"(?:Moody[\'s]*)[:\s/]+([A-Za-z0-9+\-]{2,5})"),
            ("sp",     r"(?:S&P|Standard\s*&\s*Poor[\'s]*)[:\s/]+([A-Za-z0-9+\-]{2,5})"),
            ("fitch",  r"(?:Fitch)[:\s/]+([A-Za-z0-9+\-]{2,5})"),
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                ratings[label] = m.group(1).strip()

    return ratings if ratings else None


def _extract_use_of_proceeds(text):
    patterns = [
        r"(?:Use of Proceeds|Use of Net Proceeds)[:\s]+([^\.]{20,200}\.)",
        r"(?:proceeds will be used)[^\.\n]{0,200}(?:to\s+[^\.]{10,150}\.)",
    ]
    return _first_match(text, patterns)


def _extract_bookrunners(text):
    patterns = [
        r"(?:Joint Book-Running Managers?|Joint Bookrunners?|Bookrunners?)[:\s]+(.{10,600}?)(?=Co-Manager|Co-Lead|Selling Agent|CUSIP|ISIN|Settlement|Trade Date|$)",
        r"(?:Lead Manager[s]?)[:\s]+(.{10,300}?)(?=Co-Manager|CUSIP|Settlement|$)",
    ]
    raw = _first_match(text, patterns)
    if not raw:
        return None
    # Split on company-name separators: "LLC " and next caps, "Inc. " then next, etc.
    # Use newline/multiple-space boundary OR "Inc." / "LLC" / "LLP" as natural separators
    raw = re.sub(r"(?:Inc\.|LLC|L\.L\.C\.|LLP|Corp\.)(?=\s+[A-Z])", r"\g<0>|", raw)
    names = re.split(r"\||\s{2,}", raw)
    cleaned = []
    for n in names:
        n = n.strip().rstrip(".,")
        if len(n) > 4 and not n.lower().startswith("co-"):
            cleaned.append(n)
    return cleaned[:6]  # cap at 6


def _extract_call_structure(text):
    patterns = [
        r"(?:Optional Redemption|Call Structure|Make-Whole Call)[:\s]+([^\n\.]{10,150}\.?)",
        r"(make-whole call[^\.\n]{0,100})",
        r"(non-call\s*\d+[^\.\n]{0,80})",
    ]
    return _first_match(text, patterns)


# ── IG filter ─────────────────────────────────────────────────────────────

def _is_investment_grade(deal):
    """Return True if the deal has at least one IG rating indicator."""
    if not deal:
        return False
    ratings = deal.get("ratings") or {}
    for rating_value in ratings.values():
        if rating_value in IG_RATING_INDICATORS:
            return True

    # If no ratings parsed, check raw text presence of IG indicators
    # (deal may still be IG even if rating parser missed it — include it)
    size = deal.get("size")
    issuer = deal.get("issuer")
    if size or issuer:
        return True   # Include unrated-but-parseable deals; synthesizer can note uncertainty

    return False


# ── Utility ───────────────────────────────────────────────────────────────

def _get_with_retry(url):
    """GET a URL with exponential backoff on 503. Returns response or None."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 503 and attempt < MAX_RETRIES:
                wait = 2 ** attempt * 2   # 2s, 4s
                logger.info(f"503 from EDGAR, retrying in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt * 2
                logger.warning(f"Request error ({e}), retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.warning(f"Failed after {MAX_RETRIES + 1} attempts: {url} — {e}")
                return None
    return None


def _first_match(text, patterns):
    """Return the first captured group from the first matching pattern, or None."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result = m.group(1).strip()
            if result:
                return result
    return None


# ── EDGAR Company Facts API ───────────────────────────────────────────────

def _fetch_company_facts(cik):
    """Fetch key balance sheet metrics from EDGAR company facts API.

    Returns dict with total_debt_bn and/or revenue_bn (latest annual, USD billions).
    Returns None on failure — always degrades gracefully.
    """
    try:
        padded = str(cik).zfill(10)
        url = COMPANY_FACTS_BASE.format(padded)
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        gaap = resp.json().get("facts", {}).get("us-gaap", {})

        result = {}

        # Revenue — try multiple GAAP line items in priority order
        for key in (
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueGoodsNet",
        ):
            val = _latest_annual_value(gaap.get(key, {}))
            if val is not None and val > 0:
                result["revenue_bn"] = round(val / 1e9, 1)
                break

        # Total debt: long-term debt + current portion + short-term borrowings
        ltd = _latest_annual_value(gaap.get("LongTermDebt", {})) or 0
        ltd_current = _latest_annual_value(gaap.get("LongTermDebtCurrent", {})) or 0
        stb = _latest_annual_value(gaap.get("ShortTermBorrowings", {})) or 0
        total_debt = ltd + ltd_current + stb
        if total_debt > 0:
            result["total_debt_bn"] = round(total_debt / 1e9, 1)

        return result if result else None

    except Exception as e:
        logger.debug(f"Company facts fetch failed for CIK {cik}: {e}")
        return None


def _latest_annual_value(concept):
    """Extract the most recent annual (10-K/20-F) USD value from an XBRL concept."""
    usd = concept.get("units", {}).get("USD", [])
    annual = [v for v in usd if v.get("form") in ("10-K", "20-F", "10-K/A") and v.get("val", 0) != 0]
    if not annual:
        return None
    annual.sort(key=lambda x: x.get("end", ""), reverse=True)
    return annual[0]["val"]
