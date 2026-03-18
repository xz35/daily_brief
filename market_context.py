"""
market_context.py — rolling 5-day market context for cross-episode continuity.

Stores docs/market_context.json (committed to repo, auto-updated daily).
Feeds the last 5 days of market summaries into each synthesis prompt so the
LLM can say "continuing from yesterday's theme of..." or "spreads have been
drifting wider all week, and today's data reinforces that picture."
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import MARKET_CONTEXT_PATH

logger = logging.getLogger(__name__)

CONTEXT_WINDOW = 5  # days of prior context to feed into each prompt


def load_market_context():
    """Load rolling context entries. Returns list of {date, summary} dicts."""
    path = Path(MARKET_CONTEXT_PATH)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load market context: {e}")
        return []


def format_prior_context(entries):
    """Format recent context entries as a text block for prompt injection.

    Returns empty string if no prior context available.
    """
    if not entries:
        return ""

    recent = sorted(entries, key=lambda x: x.get("date", ""))[-CONTEXT_WINDOW:]
    lines = ["PRIOR MARKET CONTEXT (last few sessions — reference if relevant, don't force it):"]
    for e in recent:
        lines.append(f"  [{e['date']}] {e['summary']}")
    return "\n".join(lines)


def save_market_context(date_str, summary, existing_entries):
    """Append today's summary, prune old entries, and save to disk.

    Returns the updated entries list.
    """
    cutoff = (datetime.today() - timedelta(days=CONTEXT_WINDOW * 3)).strftime("%Y-%m-%d")

    # Remove stale and today's existing entries (idempotent re-runs)
    updated = [
        e for e in existing_entries
        if e.get("date", "") != date_str and e.get("date", "") >= cutoff
    ]
    updated.append({"date": date_str, "summary": summary})
    updated.sort(key=lambda x: x.get("date", ""))

    path = Path(MARKET_CONTEXT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        logger.info(f"Market context saved: {len(updated)} entries")
    except Exception as e:
        logger.warning(f"Could not save market context: {e}")

    return updated


def extract_context_summary(script, max_chars=400):
    """Extract a brief context summary from today's synthesized script.

    Looks for the themes/so-what section. Falls back to the last substantive
    paragraph if no themes marker is found.
    """
    markers = [
        "themes and so what", "themes & so what",
        "stepping back", "zooming out", "the broader picture",
        "what should", "cross-cutting", "heading into",
    ]
    lower = script.lower()
    for marker in markers:
        pos = lower.find(marker)
        if pos != -1:
            # Take text from this marker forward, up to max_chars
            snippet = script[pos: pos + max_chars].strip()
            # Trim to last complete sentence
            last_period = snippet.rfind(".")
            if last_period > 100:
                snippet = snippet[: last_period + 1]
            return snippet

    # Fallback: second-to-last paragraph
    paragraphs = [p.strip() for p in script.strip().split("\n\n") if p.strip()]
    if len(paragraphs) >= 2:
        snippet = paragraphs[-2][:max_chars]
        last_period = snippet.rfind(".")
        if last_period > 50:
            return snippet[: last_period + 1]
        return snippet

    return script[:max_chars]
