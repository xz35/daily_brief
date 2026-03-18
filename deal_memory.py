"""
deal_memory.py — persistent log of IG bond deals covered in prior episodes.

Stores docs/deal_history.json in the repo (committed, auto-updated daily).
Used to enrich the new issues segment with historical issuance context:
"Novartis last came to market in January with a $1.5B 10-year..."

The file grows over time and becomes more valuable after 3-6 months of operation.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import DEAL_HISTORY_PATH

logger = logging.getLogger(__name__)


def load_deal_history():
    """Load the deal history JSON. Returns empty list if file doesn't exist."""
    path = Path(DEAL_HISTORY_PATH)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load deal history: {e}")
        return []


def get_issuer_history(issuer_name, history, lookback_days=365):
    """Find prior deal entries for a given issuer, within the lookback window.

    Uses normalized name matching — strips common legal suffixes.
    Returns list of matching prior deals, most recent first.
    """
    if not history or not issuer_name:
        return []

    normalized = _normalize_name(issuer_name)
    cutoff = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    matches = [
        e for e in history
        if _normalize_name(e.get("issuer", "")) == normalized
        and e.get("date", "") >= cutoff
    ]
    matches.sort(key=lambda x: x.get("date", ""), reverse=True)
    return matches


def format_issuer_history(issuer_name, history):
    """Format prior deal history for a given issuer as a text block for the prompt.

    Returns empty string if no history found.
    """
    prior = get_issuer_history(issuer_name, history)
    if not prior:
        return ""

    lines = [f"  Prior issuance history for {issuer_name} (last 12 months):"]
    for e in prior[:5]:  # cap at 5 prior deals
        parts = [f"    - {e['date']}"]
        if e.get("size"):
            parts.append(e["size"])
        if e.get("tenor"):
            parts.append(f"{e['tenor']}yr")
        if e.get("coupon"):
            parts.append(e["coupon"])
        if e.get("spread"):
            parts.append(e["spread"])
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def append_deals(deals, history, date_str=None):
    """Append today's deals to history. Idempotent — won't double-add same issuer/date."""
    date_str = date_str or datetime.today().strftime("%Y-%m-%d")
    today_normalized = {_normalize_name(d.get("issuer", "")) for d in deals}

    # Remove any existing entries for today (idempotent re-runs)
    updated = [
        e for e in history
        if not (
            e.get("date", "")[:10] == date_str
            and _normalize_name(e.get("issuer", "")) in today_normalized
        )
    ]

    for deal in deals:
        updated.append({
            "date": date_str,
            "issuer": deal.get("issuer", "Unknown"),
            "size": deal.get("size"),
            "tenor": deal.get("tenor"),
            "spread": deal.get("spread"),
            "coupon": deal.get("coupon"),
            "ratings": deal.get("ratings"),
            "cik": deal.get("cik"),
        })

    return updated


def save_deal_history(history):
    """Write the deal history to disk."""
    path = Path(DEAL_HISTORY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(history, indent=2, default=str), encoding="utf-8")
        logger.info(f"Deal history saved: {len(history)} total entries")
    except Exception as e:
        logger.warning(f"Could not save deal history: {e}")


def _normalize_name(name):
    """Lowercase and strip common legal suffixes for fuzzy matching."""
    suffixes = [
        " inc.", " inc", " corp.", " corp", " corporation", " ltd.", " ltd",
        " llc", " l.l.c.", " lp", " l.p.", " plc", " company", " co.", " co",
        " n.a.", " na", " ag", " se", " sa", " nv", " bv",
    ]
    n = name.lower().strip()
    for s in suffixes:
        if n.endswith(s):
            n = n[: -len(s)].strip()
    return n
