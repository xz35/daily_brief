"""
fred_fetcher.py — fetch key bond market indicators from FRED.

Provides quantitative context for the synthesizer: IG/HY OAS levels,
treasury yields, and yield curve shape. This is the missing piece for
high-quality bond market commentary — the RSS feeds give narrative,
FRED gives the actual numbers.

Requires a free FRED API key from https://fred.stlouisfed.org/docs/api/api_key.html
Set FRED_API_KEY in your .env file.

All values expressed in consistent units:
  - Spread series (OAS): basis points (bps)
  - Yield series (DGS): percent (e.g. 4.23)
  - Calculated curve slope: basis points (bps)

FRED data lags: BofA index series update with 1 business day lag.
Treasury yield series (DGS10, DGS2) update same day.
"""

import logging
import os
from datetime import datetime, timedelta

import requests

from config import FRED_API_KEY

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
REQUEST_TIMEOUT = 10

# Key bond market series — the minimal set needed for daily credit commentary.
# All BofA/ICE index series are in basis points (OAS over comparable Treasury).
FRED_SERIES = {
    "ig_oas":  ("BAMLC0A0CM",   "IG OAS",   "bps"),   # ICE BofA US Corporate Master
    "hy_oas":  ("BAMLH0A0HYM2", "HY OAS",   "bps"),   # ICE BofA US High Yield
    "bbb_oas": ("BAMLC0A4CBBB", "BBB OAS",  "bps"),   # ICE BofA BBB Corporate
    "t10y":    ("DGS10",        "10yr Treasury", "%"), # 10-Year Constant Maturity
    "t2y":     ("DGS2",         "2yr Treasury",  "%"), # 2-Year Constant Maturity
}

# Full Treasury curve for shape/trend analysis. Stored in curve_history.json.
# We fetch these separately and pass to curve_history.compute_curve_analytics().
CURVE_SERIES = {
    "DGS1MO": "1-Month Treasury",
    "DGS3MO": "3-Month Treasury",
    "DGS6MO": "6-Month Treasury",
    "DGS1":   "1-Year Treasury",
    "DGS2":   "2-Year Treasury",   # overlaps with FRED_SERIES t2y
    "DGS3":   "3-Year Treasury",
    "DGS5":   "5-Year Treasury",
    "DGS7":   "7-Year Treasury",
    "DGS10":  "10-Year Treasury",  # overlaps with FRED_SERIES t10y
    "DGS20":  "20-Year Treasury",
    "DGS30":  "30-Year Treasury",
}

# How many trading days back to look for the "prior week" comparison.
# 7 calendar days reliably captures 5 trading days for WoW change.
LOOKBACK_DAYS = 7


def fetch_market_data():
    """Fetch key bond market indicators from FRED.

    Returns a dict with current values, prior-week values, and changes.
    Returns None if FRED_API_KEY is not configured (fails gracefully —
    the synthesizer will note that market data is unavailable).

    Example return value:
        {
            "as_of": "2026-03-16",
            "ig_oas":  {"value": 93.0,  "prev": 96.1,  "change": -3.1,  "label": "IG OAS",   "unit": "bps"},
            "hy_oas":  {"value": 310.5, "prev": 315.2, "change": -4.7,  "label": "HY OAS",   "unit": "bps"},
            "bbb_oas": {"value": 112.0, "prev": 116.0, "change": -4.0,  "label": "BBB OAS",  "unit": "bps"},
            "t10y":    {"value": 4.23,  "prev": 4.31,  "change": -0.08, "label": "10yr Treasury", "unit": "%"},
            "t2y":     {"value": 4.05,  "prev": 4.15,  "change": -0.10, "label": "2yr Treasury",  "unit": "%"},
            "curve_2s10s": 18,   # bps: t10y - t2y in basis points
        }
    """
    api_key = FRED_API_KEY or os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning("FRED_API_KEY not set — skipping market data fetch. "
                       "Register free at fred.stlouisfed.org to enable bond market indicators.")
        return None

    result = {}
    earliest_date = None

    for key, (series_id, label, unit) in FRED_SERIES.items():
        obs = _fetch_series(api_key, series_id)
        if obs is None:
            logger.warning(f"FRED: failed to fetch {series_id} ({label})")
            continue

        current = obs[0] if obs else None
        prev = _find_obs_from(obs, days_back=LOOKBACK_DAYS)

        if current is None:
            continue

        try:
            current_val = float(current["value"])
            prev_val    = float(prev["value"]) if prev else None
            change      = round(current_val - prev_val, 2) if prev_val is not None else None

            result[key] = {
                "value":  current_val,
                "prev":   prev_val,
                "change": change,
                "label":  label,
                "unit":   unit,
                "date":   current["date"],
            }

            if earliest_date is None or current["date"] > earliest_date:
                earliest_date = current["date"]

        except (ValueError, TypeError) as e:
            logger.warning(f"FRED: could not parse {series_id}: {e}")

    if not result:
        logger.warning("FRED: no series fetched successfully")
        return None

    # Derived: 2s10s slope in bps
    if "t10y" in result and "t2y" in result:
        slope = round((result["t10y"]["value"] - result["t2y"]["value"]) * 100)
        result["curve_2s10s"] = slope

    # Full yield curve snapshot for curve_history.py analysis
    result["full_curve"] = _fetch_full_curve(api_key)

    result["as_of"] = earliest_date or "unknown"
    logger.info(f"FRED: market data fetched as of {result['as_of']} "
                f"(IG OAS={result.get('ig_oas', {}).get('value')} bps, "
                f"10yr={result.get('t10y', {}).get('value')}%, "
                f"curve points={len(result.get('full_curve') or {})})")
    return result


# ── FRED API internals ─────────────────────────────────────────────────────

def _fetch_full_curve(api_key):
    """Fetch all Treasury curve points. Returns {series_id: float} or {}."""
    curve = {}
    for series_id in CURVE_SERIES:
        obs = _fetch_series(api_key, series_id, limit=3)
        if obs:
            try:
                curve[series_id] = float(obs[0]["value"])
            except (ValueError, TypeError):
                pass
    return curve or None


def _fetch_series(api_key, series_id, limit=15):
    """Fetch recent observations for a FRED series. Returns list sorted newest-first."""
    params = {
        "series_id":   series_id,
        "api_key":     api_key,
        "file_type":   "json",
        "sort_order":  "desc",
        "limit":       limit,
    }
    try:
        resp = requests.get(FRED_BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        observations = data.get("observations", [])
        # Filter out missing values (FRED uses "." for unavailable data)
        return [o for o in observations if o.get("value") not in (".", "", None)]
    except Exception as e:
        logger.warning(f"FRED: request failed for {series_id}: {e}")
        return None


def _find_obs_from(observations, days_back=7):
    """Find the observation closest to `days_back` calendar days ago."""
    if not observations:
        return None
    latest_date = datetime.strptime(observations[0]["date"], "%Y-%m-%d")
    target_date = latest_date - timedelta(days=days_back)
    # Find the observation closest to target (FRED only has business days)
    best = None
    best_delta = None
    for obs in observations[1:]:
        try:
            obs_date = datetime.strptime(obs["date"], "%Y-%m-%d")
            delta = abs((obs_date - target_date).days)
            if best_delta is None or delta < best_delta:
                best = obs
                best_delta = delta
        except ValueError:
            continue
    return best
