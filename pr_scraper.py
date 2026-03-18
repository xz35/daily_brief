"""
pr_scraper.py — scrape Business Wire and GlobeNewswire RSS feeds for
bond offering press releases. Supplements EDGAR for 144A deals that
do not file FWPs with the SEC.

Deduplicates against EDGAR results by issuer name similarity before
returning supplemental deals.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from bs4 import BeautifulSoup

from config import LOOKBACK_HOURS, PR_FEEDS, PR_BOND_KEYWORDS

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
REQUEST_DELAY = 0.3


def fetch_supplemental_deals(edgar_deals=None):
    """Fetch bond offering press releases not already covered by EDGAR.

    Args:
        edgar_deals: list of deal dicts already fetched from EDGAR (for dedup)

    Returns:
        list[dict]: Supplemental deal records with keys matching edgar_fetcher output.
    """
    edgar_issuers = _extract_issuers(edgar_deals or [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    all_releases = []
    for source_name, feed_url in PR_FEEDS:
        try:
            releases = _fetch_feed(source_name, feed_url, cutoff)
            all_releases.extend(releases)
            logger.info(f"{source_name}: {len(releases)} bond-related releases")
        except Exception as e:
            logger.warning(f"Failed to fetch {source_name}: {e}")

    # Filter to releases that look like bond offerings
    bond_releases = [r for r in all_releases if _is_bond_offering(r)]
    logger.info(f"PR feeds: {len(all_releases)} total → {len(bond_releases)} bond-related")

    # Deduplicate against EDGAR
    new_deals = [r for r in bond_releases if not _matches_edgar(r, edgar_issuers)]
    logger.info(f"PR supplemental: {len(new_deals)} deals not in EDGAR")

    return [_to_deal_dict(r) for r in new_deals]


# ── Feed fetching ─────────────────────────────────────────────────────────

def _fetch_feed(source_name, feed_url, cutoff):
    """Fetch a PR RSS feed and return recent bond-related entries."""
    try:
        resp = requests.get(feed_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except requests.exceptions.Timeout:
        logger.warning(f"{source_name}: feed fetch timed out after {REQUEST_TIMEOUT}s — skipping")
        return []
    except Exception as e:
        logger.warning(f"{source_name}: feed fetch failed — {e}")
        return []
    if feed.bozo and feed.bozo_exception:
        logger.debug(f"{source_name}: parse warning — {feed.bozo_exception}")

    results = []
    for entry in feed.entries:
        pub_date = _parse_date(entry)
        if pub_date is None or pub_date < cutoff:
            continue
        results.append({
            "title":   entry.get("title", "").strip(),
            "summary": _clean_text(entry.get("summary", "") or entry.get("description", "")),
            "url":     entry.get("link", ""),
            "date":    pub_date.isoformat(),
            "source":  source_name,
        })
    return results


def _parse_date(entry):
    for field in ("published", "updated"):
        raw = entry.get(field)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


# ── Filtering ─────────────────────────────────────────────────────────────

def _is_bond_offering(release):
    """Return True if this press release appears to be a bond/note offering."""
    text = f"{release['title']} {release['summary']}".lower()
    return any(kw.lower() in text for kw in PR_BOND_KEYWORDS)


def _matches_edgar(release, edgar_issuers):
    """Return True if this release issuer is already in the EDGAR deal list."""
    title_lower = release["title"].lower()
    for issuer in edgar_issuers:
        # Simple overlap check: if ≥2 words of the issuer name appear in the title
        issuer_words = [w for w in issuer.lower().split() if len(w) > 3]
        matches = sum(1 for w in issuer_words if w in title_lower)
        if matches >= 2:
            return True
    return False


# ── Conversion ────────────────────────────────────────────────────────────

def _to_deal_dict(release):
    """Convert a press release entry to the standard deal dict format."""
    issuer = _extract_issuer_from_title(release["title"])
    size = _extract_size(release["title"] + " " + release["summary"])
    return {
        "issuer":          issuer or release["title"][:60],
        "size":            size,
        "tenor":           None,
        "maturity":        None,
        "coupon":          None,
        "spread":          None,
        "ratings":         None,
        "use_of_proceeds": None,
        "bookrunners":     None,
        "call_structure":  None,
        "filing_date":     release["date"][:10],
        "accession_no":    None,
        "source":          f"PR / {release['source']}",
        "pr_url":          release["url"],
        "pr_title":        release["title"],
    }


# ── Field extractors ─────────────────────────────────────────────────────

def _extract_issuer_from_title(title):
    """Try to extract company name from press release title."""
    # Common pattern: "Company Name Prices $X billion of Senior Notes"
    m = re.match(r"^([A-Z][A-Za-z0-9\s,\.&]+?)\s+(?:prices|announces|prices offering|launches)", title, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _extract_size(text):
    m = re.search(r"\$([\d,]+(?:\.\d+)?)\s*(billion|million)", text, re.IGNORECASE)
    if m:
        return f"${m.group(1)} {m.group(2)}"
    return None


def _extract_issuers(deals):
    """Get list of issuer names from a list of deal dicts."""
    return [d.get("issuer", "") for d in deals if d.get("issuer")]


def _clean_text(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]
