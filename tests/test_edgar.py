"""
test_edgar.py — integration test for edgar_fetcher.py.

Runs against the live EDGAR API. No API keys needed.
Run this BEFORE writing production code — validate the API response
format and parser coverage with real data.

Usage:
    python tests/test_edgar.py
    python tests/test_edgar.py --date 2026-03-14    # test a specific date
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edgar_fetcher import fetch_deals, _search_edgar
from utils import prior_business_day, setup_logging

logger = setup_logging()


def test_edgar(date=None):
    target_date = date or prior_business_day()

    print("\n" + "=" * 60)
    print(f"EDGAR FETCHER TEST — {target_date}")
    print("=" * 60)

    # First check the raw API response
    print("\n[1] Raw EDGAR API search results:")
    raw = _search_edgar(target_date)
    print(f"    {len(raw)} FWP filings found")
    if raw:
        print(f"    First result keys: {list(raw[0].keys())}")
        print(f"    First result: {raw[0]}")

    if not raw:
        print(f"\n  Note: No FWP filings on {target_date}.")
        print("  This is normal on weekends, holidays, or light issuance days.")
        print("  Try a different date: python tests/test_edgar.py --date YYYY-MM-DD")
        return True   # not a failure — zero-deal day is valid

    # Now test full parsing
    print("\n[2] Full deal parsing:")
    deals = fetch_deals(date=target_date)
    print(f"    {len(deals)} IG deals parsed from {len(raw)} filings")

    if deals:
        print("\nParsed deals:\n")
        for i, d in enumerate(deals, 1):
            print(f"Deal {i}: {d.get('issuer', 'Unknown')}")
            print(f"  Source:    {d.get('source')}")
            print(f"  Size:      {d.get('size')}")
            print(f"  Tenor:     {d.get('tenor')}")
            print(f"  Maturity:  {d.get('maturity')}")
            print(f"  Coupon:    {d.get('coupon')}")
            print(f"  Spread:    {d.get('spread')}")
            print(f"  Ratings:   {d.get('ratings')}")
            print(f"  Proceeds:  {d.get('use_of_proceeds', '')[:80] if d.get('use_of_proceeds') else None}")
            print(f"  Bookrnnrs: {d.get('bookrunners')}")
            print()

        # Parse coverage report
        fields = ["size", "tenor", "coupon", "spread", "ratings", "use_of_proceeds", "bookrunners"]
        print("Parse coverage:")
        for field in fields:
            filled = sum(1 for d in deals if d.get(field))
            print(f"  {field:20s}: {filled}/{len(deals)} ({100*filled//len(deals)}%)")
    else:
        print("  No IG deals parsed. Check parser logs above for details.")

    return True


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Date to test (YYYY-MM-DD)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    success = test_edgar(date=args.date)
    sys.exit(0 if success else 1)
