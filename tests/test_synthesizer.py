"""
test_synthesizer.py — test the Gemini synthesis with sample or live data.

Uses sample_data/ files by default (no API cost). Pass --live to test
with real scraped data (costs ~2 Gemini API calls).

Usage:
    python tests/test_synthesizer.py                  # use sample data
    python tests/test_synthesizer.py --live           # use live RSS + EDGAR data
    python tests/test_synthesizer.py --skip-tts       # same as above, print script only
"""

import sys
import os
import argparse
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from synthesizer import synthesize
from utils import setup_logging

logger = setup_logging()

SAMPLE_ARTICLES    = os.path.join(os.path.dirname(__file__), "sample_data", "sample_articles.json")
SAMPLE_DEALS       = os.path.join(os.path.dirname(__file__), "sample_data", "sample_deals.json")
SAMPLE_MARKET_DATA = os.path.join(os.path.dirname(__file__), "sample_data", "sample_market_data.json")


def test_synthesizer(use_live=False):
    print("\n" + "=" * 60)
    print(f"SYNTHESIZER TEST — {'live data' if use_live else 'sample data'}")
    print("=" * 60)

    if use_live:
        articles, deals, market_data = _fetch_live_data()
    else:
        articles, deals, market_data = _load_sample_data()

    print(f"\nInput: {len(articles)} articles, {len(deals)} deals, market_data={'yes' if market_data else 'no'}")

    script, word_count = synthesize(articles, deals, market_data=market_data)

    print(f"\nOutput: {word_count} words (~{word_count // 130:.0f} min at 130 wpm)")
    print("\n" + "-" * 60)
    print("FULL SCRIPT:")
    print("-" * 60)
    print(script)
    print("-" * 60)

    # Save output for review
    output_path = os.path.join(os.path.dirname(__file__), "sample_data", "last_script_output.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(script)
    print(f"\nScript saved to: {output_path}")

    assert word_count > 500, f"Script too short: {word_count} words"
    print("\nAssertions passed.")
    return True


def _load_sample_data():
    with open(SAMPLE_ARTICLES, encoding="utf-8") as f:
        articles = json.load(f)
    with open(SAMPLE_DEALS, encoding="utf-8") as f:
        deals = json.load(f)
    with open(SAMPLE_MARKET_DATA, encoding="utf-8") as f:
        market_data = json.load(f)
    return articles, deals, market_data


def _fetch_live_data():
    from rss_scraper import fetch_articles
    from edgar_fetcher import fetch_deals
    from pr_scraper import fetch_supplemental_deals
    from fred_fetcher import fetch_market_data

    print("Fetching live RSS articles...")
    articles = fetch_articles()

    print("Fetching EDGAR deals...")
    edgar_deals = fetch_deals()

    print("Fetching PR supplemental deals...")
    pr_deals = fetch_supplemental_deals(edgar_deals=edgar_deals)

    print("Fetching FRED market data...")
    market_data = fetch_market_data()
    if market_data:
        print(f"  -> FRED data as of {market_data.get('as_of')}")
    else:
        print("  -> FRED unavailable (FRED_API_KEY not set?)")

    return articles, edgar_deals + pr_deals, market_data


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use live data instead of sample data")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    success = test_synthesizer(use_live=args.live)
    sys.exit(0 if success else 1)
