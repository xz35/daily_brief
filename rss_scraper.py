"""
rss_scraper.py — fetch, filter, score, and deduplicate RSS articles.

Returns the top MAX_RSS_ARTICLES most relevant articles from the last
LOOKBACK_HOURS, scored against TOPIC_KEYWORDS. Individual feed failures
are caught and logged — they do not crash the pipeline.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser

from config import (
    LOOKBACK_HOURS,
    MAX_RSS_ARTICLES,
    RSS_FEEDS,
    TOPIC_KEYWORDS,
)

logger = logging.getLogger(__name__)


def fetch_articles():
    """Fetch and filter RSS articles from all configured feeds.

    Returns:
        list[dict]: Up to MAX_RSS_ARTICLES articles, each with keys:
            title, source, date (ISO string), summary, url, score
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    all_articles = []

    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and feed.bozo_exception:
                logger.warning(f"{source_name}: feed parse warning — {feed.bozo_exception}")

            count = 0
            for entry in feed.entries:
                pub_date = _parse_date(entry)
                if pub_date is None:
                    continue
                if pub_date < cutoff:
                    continue

                article = {
                    "title":   entry.get("title", "").strip(),
                    "source":  source_name,
                    "date":    pub_date.isoformat(),
                    "summary": _extract_summary(entry),
                    "url":     entry.get("link", ""),
                    "score":   0,
                }
                article["score"] = _score(article)
                all_articles.append(article)
                count += 1

            logger.info(f"{source_name}: {count} articles in last {LOOKBACK_HOURS}h")

        except Exception as e:
            logger.warning(f"Failed to fetch {source_name} ({feed_url}): {e}")

    unique = _deduplicate(all_articles)
    unique.sort(key=lambda x: x["score"], reverse=True)
    selected = unique[:MAX_RSS_ARTICLES]

    logger.info(
        f"RSS total: {len(all_articles)} raw → "
        f"{len(unique)} deduped → "
        f"{len(selected)} selected"
    )
    return selected


# ── Internal helpers ──────────────────────────────────────────────────────

def _parse_date(entry):
    """Try multiple date fields; return UTC-aware datetime or None."""
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

    # feedparser also parses dates into 9-tuples
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass

    return None


def _extract_summary(entry):
    """Get summary text, stripping HTML tags and truncating to 500 chars."""
    text = entry.get("summary", "") or entry.get("description", "") or ""
    text = re.sub(r"<[^>]+>", " ", text)       # strip HTML
    text = re.sub(r"\s+", " ", text).strip()    # collapse whitespace
    return text[:500]


def _score(article):
    """Score by keyword match count across title + summary (case-insensitive)."""
    text = f"{article['title']} {article['summary']}".lower()
    return sum(1 for kw in TOPIC_KEYWORDS if kw.lower() in text)


def _deduplicate(articles):
    """Remove near-duplicate articles using title word overlap (>60% = duplicate)."""
    seen_word_sets = []
    unique = []

    for article in articles:
        words = set(article["title"].lower().split())
        if not words:
            continue

        is_dup = any(
            len(words & seen) / len(words) > 0.6
            for seen in seen_word_sets
        )

        if not is_dup:
            unique.append(article)
            seen_word_sets.append(words)

    return unique
