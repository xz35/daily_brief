"""
test_fred.py — test FRED API connectivity and market data fetch.

Runs against the live FRED API. Requires FRED_API_KEY in .env.
No API cost (FRED is free).

Usage:
    python tests/test_fred.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from fred_fetcher import fetch_market_data
from utils import setup_logging

logger = setup_logging()


def test_fred():
    print("\n" + "=" * 60)
    print("FRED MARKET DATA TEST")
    print("=" * 60)

    data = fetch_market_data()

    if data is None:
        print("\n  FRED_API_KEY not set or fetch failed.")
        print("  Register free at: https://fred.stlouisfed.org/docs/api/api_key.html")
        print("  Then add FRED_API_KEY=your_key to morning-brief/.env")
        return False

    print(f"\nData as of: {data.get('as_of')}")
    print()

    series_keys = ["ig_oas", "hy_oas", "bbb_oas", "t10y", "t2y"]
    for key in series_keys:
        d = data.get(key)
        if d:
            change_str = f"{d['change']:+.2f}" if d['change'] is not None else "n/a"
            print(f"  {d['label']:20s}: {d['value']:8.2f} {d['unit']:3s}  "
                  f"(WoW change: {change_str} {d['unit']})")

    slope = data.get("curve_2s10s")
    if slope is not None:
        sign = "+" if slope >= 0 else ""
        shape = "normal (positive)" if slope > 0 else "inverted (negative)"
        print(f"\n  2s/10s curve: {sign}{slope} bps ({shape})")

    print()
    print("Formatted for synthesizer prompt:")
    print("-" * 40)
    # Import the formatting function from synthesizer
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from synthesizer import _format_market_data
    print(_format_market_data(data))
    print("-" * 40)

    # Basic assertions
    assert "ig_oas" in data, "Missing IG OAS"
    assert "t10y" in data, "Missing 10yr Treasury"
    assert "curve_2s10s" in data, "Missing curve calculation"
    assert data["ig_oas"]["value"] > 0, "IG OAS should be positive"
    assert 0 < data["t10y"]["value"] < 20, "10yr yield out of reasonable range"

    print("\nAssertions passed.")
    return True


def _format_market_data_for_display(data):
    """Alias — the display formatting lives in synthesizer.py."""
    from synthesizer import _format_market_data
    return _format_market_data(data)


if __name__ == "__main__":
    success = test_fred()
    sys.exit(0 if success else 1)
