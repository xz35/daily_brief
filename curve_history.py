"""
curve_history.py — daily Treasury yield curve snapshots and trend analytics.

Stores docs/curve_history.json (committed to repo, auto-updated daily).
Computes curve shape, key spreads, and trend changes so the LLM receives
pre-analyzed curve intelligence — not a table of raw yield values.

The key design principle: raw numbers go in, narrative-ready analysis comes out.
The LLM synthesizes commentary from the analytics block; it never sees a yield table.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import CURVE_HISTORY_PATH

logger = logging.getLogger(__name__)

HISTORY_DAYS = 90   # Keep 90 days of daily curve snapshots


def load_curve_history():
    """Load curve history. Returns list of {date, curve} dicts sorted oldest-first."""
    path = Path(CURVE_HISTORY_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return sorted(data, key=lambda x: x.get("date", ""))
    except Exception as e:
        logger.warning(f"Could not load curve history: {e}")
        return []


def save_curve_snapshot(date_str, curve, history):
    """Append today's curve snapshot, prune old entries, and save.

    Args:
        date_str: YYYY-MM-DD string
        curve:    dict of {series_id: float} (e.g. {"DGS2": 4.71, "DGS10": 4.63, ...})
        history:  existing history list from load_curve_history()

    Returns updated history list.
    """
    if not curve:
        return history

    cutoff = (datetime.today() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")

    # Remove stale and today's existing entries (idempotent re-runs)
    updated = [
        e for e in history
        if e.get("date", "") != date_str and e.get("date", "") >= cutoff
    ]
    updated.append({"date": date_str, "curve": curve})
    updated.sort(key=lambda x: x.get("date", ""))

    path = Path(CURVE_HISTORY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        logger.info(f"Curve history saved: {len(updated)} entries")
    except Exception as e:
        logger.warning(f"Could not save curve history: {e}")

    return updated


def compute_curve_analytics(today_curve, history):
    """Pre-compute curve analysis for LLM consumption.

    Takes today's full curve + history, returns a formatted text block
    with shape characterization, key spreads, and 1-week/1-month trends.

    The LLM uses this to generate interesting curve commentary — it synthesizes
    narrative from the analysis, rather than reciting raw yield values.

    Args:
        today_curve: dict of {series_id: float} — today's full curve from FRED
        history:     list of {date, curve} dicts from load_curve_history()

    Returns:
        str: formatted analytics block, or "" if insufficient data
    """
    if not today_curve:
        return ""

    # Pull key points
    t3mo = today_curve.get("DGS3MO")
    t2y  = today_curve.get("DGS2")
    t5y  = today_curve.get("DGS5")
    t10y = today_curve.get("DGS10")
    t30y = today_curve.get("DGS30")

    if not (t2y and t10y):
        return ""   # Need at least 2s and 10s to say anything useful

    # Key spreads in bps
    two_ten     = round((t10y - t2y) * 100)
    three_ten   = round((t10y - t3mo) * 100) if t3mo else None
    five_thirty = round((t30y - t5y) * 100)  if (t5y and t30y) else None

    lines = ["YIELD CURVE ANALYSIS:"]

    # Shape — use 3m-10y as the primary recession indicator when available
    shape = _describe_shape(two_ten, three_ten)
    lines.append(f"Shape: {shape}")

    # Selective levels — 5 representative points, not a full table
    level_parts = []
    if t3mo: level_parts.append(f"3M: {t3mo:.2f}%")
    if t2y:  level_parts.append(f"2Y: {t2y:.2f}%")
    if t5y:  level_parts.append(f"5Y: {t5y:.2f}%")
    if t10y: level_parts.append(f"10Y: {t10y:.2f}%")
    if t30y: level_parts.append(f"30Y: {t30y:.2f}%")
    lines.append(f"Selective levels: {' | '.join(level_parts)}")

    # Key spreads
    spread_parts = [f"2s10s: {two_ten:+d}bps"]
    if three_ten is not None:
        spread_parts.append(f"3m-10y: {three_ten:+d}bps")
    if five_thirty is not None:
        spread_parts.append(f"5s30s: {five_thirty:+d}bps")
    lines.append(f"Key spreads: {' | '.join(spread_parts)}")

    # 1-week trend
    week_curve = _get_curve_n_bdays_ago(history, 5)
    if week_curve:
        w_t2y  = week_curve.get("DGS2",  t2y)
        w_t10y = week_curve.get("DGS10", t10y)
        week_2s10s = round((w_t10y - w_t2y) * 100)
        week_delta = two_ten - week_2s10s
        front_move = round((t2y  - w_t2y)  * 100)
        back_move  = round((t10y - w_t10y) * 100)
        trend_dir  = _trend_description(front_move, back_move)
        lines.append(
            f"1-week: 2s10s {week_delta:+d}bps ({trend_dir}; "
            f"2Y {front_move:+d}bps, 10Y {back_move:+d}bps)"
        )

    # 1-month trend
    month_curve = _get_curve_n_bdays_ago(history, 21)
    if month_curve:
        m_t2y  = month_curve.get("DGS2",  t2y)
        m_t10y = month_curve.get("DGS10", t10y)
        month_2s10s = round((m_t10y - m_t2y) * 100)
        month_delta = two_ten - month_2s10s
        # Flag inversion regime changes — meaningful signal
        regime_note = ""
        if (two_ten > 0) != (month_2s10s > 0):
            direction = "un-inverted" if two_ten > 0 else "re-inverted"
            regime_note = f" — 2s10s has {direction} over the past month (regime shift)"
        lines.append(
            f"1-month: 2s10s {month_delta:+d}bps (was {month_2s10s:+d}bps){regime_note}"
        )

    return "\n".join(lines)


# ── Internal helpers ───────────────────────────────────────────────────────

def _describe_shape(two_ten, three_ten):
    """Return a plain-language curve shape description."""
    # 3m-10y is the canonical recession indicator; use when available
    primary = three_ten if three_ten is not None else two_ten

    if primary < -75:
        base = "deeply inverted"
    elif primary < -25:
        base = "inverted"
    elif primary < 0:
        base = "slightly inverted"
    elif primary < 25:
        base = "flat"
    elif primary < 75:
        base = "modestly upward-sloping"
    else:
        base = "steep"

    # Add the 2s10s level in parentheses for context
    slope_str = f"{two_ten:+d}bps"
    if three_ten is not None and abs(two_ten - three_ten) > 30:
        # The two segments are telling different stories — call it out
        return f"{base} (3m-10y: {three_ten:+d}bps, 2s10s: {slope_str} — segments diverging)"
    return f"{base} (2s10s: {slope_str})"


def _trend_description(front_bps, back_bps):
    """Classify the weekly curve move using standard bond market terminology."""
    if abs(front_bps) < 3 and abs(back_bps) < 3:
        return "little change"
    if front_bps > 0 and back_bps > 0:
        return "bear flattening" if front_bps > back_bps else "bear steepening"
    if front_bps < 0 and back_bps < 0:
        return "bull steepening" if abs(front_bps) > abs(back_bps) else "bull flattening"
    if front_bps > 0 and back_bps < 0:
        return "flattening"
    if front_bps < 0 and back_bps > 0:
        return "steepening (front rallying, long end selling)"
    return "mixed"


def _get_curve_n_bdays_ago(history, n_bdays):
    """Return the stored curve from approximately n business days ago.

    Uses calendar-day approximation (1 bday ≈ 1.4 calendar days).
    Returns the most recent stored curve at or before that target date.
    Returns None if history is empty or doesn't go back far enough.
    """
    if not history:
        return None
    today = datetime.today()
    target = today - timedelta(days=int(n_bdays * 1.4))
    target_str = target.strftime("%Y-%m-%d")

    candidates = [e for e in history if e.get("date", "") <= target_str]
    return candidates[-1].get("curve") if candidates else None
