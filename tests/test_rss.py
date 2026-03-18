"""
test_rss.py — integration test for rss_scraper.py.

Runs against live RSS feeds. No API keys needed.

Usage:
    python tests/test_rss.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rss_scraper import fetch_articles
from utils import setup_logging

logger = setup_logging()


def test_rss():
    print("\n" + "=" * 60)
    print("RSS SCRAPER TEST — live feeds")
    print("=" * 60)

    articles = fetch_articles()

    print(f"\nTotal articles returned: {len(articles)}")

    if not articles:
        print("WARNING: No articles returned. Check feed URLs and internet connection.")
        return False

    print("\nTop 10 articles (by score):\n")
    for i, a in enumerate(articles[:10], 1):
        print(f"{i:2d}. [score={a['score']:2d}] [{a['source']}]")
        print(f"    {a['title']}")
        print(f"    {a['date'][:10]}")
        if a.get("summary"):
            print(f"    {a['summary'][:120]}...")
        print()

    # Assertions
    assert len(articles) >= 5, f"Expected at least 5 articles, got {len(articles)}"
    assert all("title" in a for a in articles), "All articles must have a title"
    assert all("source" in a for a in articles), "All articles must have a source"
    assert all("date" in a for a in articles), "All articles must have a date"

    scores = [a["score"] for a in articles]
    assert scores == sorted(scores, reverse=True), "Articles must be sorted by score"

    print("All assertions passed.")
    return True


if __name__ == "__main__":
    success = test_rss()
    sys.exit(0 if success else 1)
