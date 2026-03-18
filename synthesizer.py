"""
synthesizer.py — LLM synthesis via Gemini API.

Two sequential calls per run:
  Call 1 (market_news_prompt.txt) → Segments 1, 2, 4 (news, snapshot, themes)
  Call 2 (new_issues_prompt.txt)  → Segment 3 (IG new issues)

Prompts are loaded from the prompts/ directory as plain text files.
Edit the .txt files to iterate on tone, content, and style — no code changes needed.
"""

import json
import logging
import os
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from config import GEMINI_API_KEY, GEMINI_MAX_TOKENS, GEMINI_MODEL, PROMPTS_DIR
from utils import today_str

logger = logging.getLogger(__name__)


def synthesize(articles, deals, market_data=None, prior_context="", deal_history=None):
    """Run both Gemini synthesis calls and assemble the full podcast script.

    Args:
        articles:       list of article dicts from rss_scraper
        deals:          list of deal dicts from edgar_fetcher / pr_scraper
        market_data:    dict from fred_fetcher (optional)
        prior_context:  formatted string from market_context.py (optional)
        deal_history:   list of prior deal entries from deal_memory.py (optional)

    Returns:
        tuple: (script_text: str, word_count: int)
    """
    logger.info(f"Synthesizing: {len(articles)} articles, {len(deals)} deals, "
                f"market_data={'yes' if market_data else 'no'}, "
                f"prior_context={'yes' if prior_context else 'no'}")

    news_script = _call_market_news(articles, market_data, prior_context)
    deals_script = _call_new_issues(deals, deal_history or [])

    full_script = _assemble_script(news_script, deals_script)
    word_count = len(full_script.split())

    logger.info(f"Script assembled: {word_count} words")
    return full_script, word_count


# ── Gemini calls ──────────────────────────────────────────────────────────

def _call_market_news(articles, market_data=None, prior_context=""):
    """Gemini call 1: market news → Segments 1, 2, 4."""
    prompt_template = _load_prompt("market_news_prompt.txt")

    articles_text = _format_articles(articles)
    market_data_text = _format_market_data(market_data)
    prompt = prompt_template.format(
        date=today_str(),
        articles=articles_text,
        market_data=market_data_text,
        prior_context=prior_context or "No prior context available (first episode or context file missing).",
    )

    logger.info("Gemini call 1: market news synthesis")
    return _generate(prompt, context="market_news")


def _call_new_issues(deals, deal_history=None):
    """Gemini call 2: new issues → Segment 3."""
    prompt_template = _load_prompt("new_issues_prompt.txt")

    deals_text = _format_deals(deals, deal_history or [])
    prompt = prompt_template.format(deals=deals_text)

    logger.info("Gemini call 2: new issues synthesis")
    return _generate(prompt, context="new_issues")


def _generate(prompt, context=""):
    """Call Gemini and return generated text. Returns fallback string on failure."""
    try:
        client = _get_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=GEMINI_MAX_TOKENS,
                temperature=0.7,
            ),
        )
        text = response.text.strip()
        logger.info(f"Gemini [{context}]: {len(text.split())} words returned")
        return text
    except Exception as e:
        logger.error(f"Gemini call failed [{context}]: {e}")
        return f"[Content unavailable for this segment due to synthesis error: {e}]"


# ── Script assembly ───────────────────────────────────────────────────────

def _assemble_script(news_script, deals_script):
    """Combine market news and new issues scripts into a single podcast script.

    The market_news_prompt produces a single output containing:
      intro + segment 1 + segment 2 + segment 4 + outro

    The new_issues_prompt produces segment 3.

    We insert segment 3 between segment 2 and segment 4 (the themes segment).
    The prompts are designed so this ordering makes natural sense.
    """
    # Simple assembly: news script already contains intro/segments 1,2,4/outro
    # We insert the new issues block after the market snapshot (segment 2).
    # Marker approach: look for a transition phrase in the news script.

    new_issues_header = (
        "\n\nTurning now to the new issues desk. "
        "Here is the prior day investment grade bond market report.\n\n"
    )
    new_issues_footer = "\n\nBack to the broader picture now.\n\n"

    # Find insertion point: after "SEGMENT 2" content, before "SEGMENT 4"
    # If markers aren't present, just concatenate with a clear transition.
    insertion_markers = [
        "themes and so what",
        "themes & so what",
        "turning to our themes",
        "stepping back",
        "zooming out",
        "the broader picture",
    ]

    lower_news = news_script.lower()
    insert_pos = -1
    for marker in insertion_markers:
        pos = lower_news.find(marker)
        if pos != -1:
            # Find the start of the sentence containing this marker
            sentence_start = news_script.rfind(".", 0, pos) + 1
            insert_pos = sentence_start if sentence_start > 0 else pos
            break

    if insert_pos != -1:
        script = (
            news_script[:insert_pos].rstrip()
            + new_issues_header
            + deals_script
            + new_issues_footer
            + news_script[insert_pos:].lstrip()
        )
    else:
        # Fallback: insert new issues before the final paragraph
        paragraphs = news_script.strip().split("\n\n")
        if len(paragraphs) > 2:
            script = (
                "\n\n".join(paragraphs[:-2])
                + new_issues_header
                + deals_script
                + new_issues_footer
                + "\n\n".join(paragraphs[-2:])
            )
        else:
            script = news_script + new_issues_header + deals_script

    return script.strip()


# ── Formatting helpers ────────────────────────────────────────────────────

def _format_market_data(market_data):
    """Format FRED market data snapshot into a concise text block for the prompt."""
    if not market_data:
        return "Market data unavailable (FRED_API_KEY not configured)."

    as_of = market_data.get("as_of", "unknown date")
    lines = [f"As of {as_of}:"]

    def _change_str(change, unit):
        if change is None:
            return "n/a WoW"
        if unit == "bps":
            return f"{change:+.1f} bps WoW"
        else:  # percent
            return f"{change*100:+.1f} bps WoW"

    # Spread indicators
    for key in ("ig_oas", "hy_oas", "bbb_oas"):
        d = market_data.get(key)
        if d:
            lines.append(
                f"  {d['label']}: {d['value']:.1f} bps "
                f"({_change_str(d['change'], d['unit'])})"
            )

    # Treasury yields
    for key in ("t10y", "t2y"):
        d = market_data.get(key)
        if d:
            lines.append(
                f"  {d['label']}: {d['value']:.2f}% "
                f"({_change_str(d['change'], d['unit'])})"
            )

    # Curve slope
    slope = market_data.get("curve_2s10s")
    if slope is not None:
        sign = "+" if slope >= 0 else ""
        lines.append(f"  2s/10s curve: {sign}{slope} bps")

    return "\n".join(lines)


def _format_articles(articles):
    """Format article list into a numbered text block for the prompt."""
    if not articles:
        return "No articles available for today."

    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. [{a['source']}] {a['title']}")
        if a.get("summary"):
            lines.append(f"   {a['summary']}")
        lines.append("")
    return "\n".join(lines)


def _format_deals(deals, deal_history=None):
    """Format deal list into a structured text block for the prompt.

    Includes company facts (balance sheet) and prior issuance history when available.
    """
    if not deals:
        return "No IG bond deals recorded for the prior business day."

    from deal_memory import format_issuer_history

    lines = []
    for i, d in enumerate(deals, 1):
        lines.append(f"DEAL {i}: {d.get('issuer', 'Unknown Issuer')}")
        lines.append(f"  Source: {d.get('source', 'EDGAR FWP')}")

        if d.get("size"):
            lines.append(f"  Size: {d['size']}")
        if d.get("tenor"):
            lines.append(f"  Tenor: {d['tenor']}")
        if d.get("maturity"):
            lines.append(f"  Maturity: {d['maturity']}")
        if d.get("coupon"):
            lines.append(f"  Coupon: {d['coupon']}")
        if d.get("spread"):
            lines.append(f"  Spread: {d['spread']}")
        if d.get("ratings"):
            r = d["ratings"]
            ratings_str = " / ".join(f"{k.upper()}: {v}" for k, v in r.items())
            lines.append(f"  Ratings: {ratings_str}")
        if d.get("use_of_proceeds"):
            lines.append(f"  Use of Proceeds: {d['use_of_proceeds']}")
        if d.get("bookrunners"):
            bk = d["bookrunners"]
            bk_str = ", ".join(bk) if isinstance(bk, list) else bk
            lines.append(f"  Bookrunners: {bk_str}")
        if d.get("call_structure"):
            lines.append(f"  Call Structure: {d['call_structure']}")

        # Company facts from EDGAR XBRL
        cf = d.get("company_facts")
        if cf:
            facts_parts = []
            if cf.get("revenue_bn") is not None:
                facts_parts.append(f"Revenue: ${cf['revenue_bn']}B (latest annual)")
            if cf.get("total_debt_bn") is not None:
                facts_parts.append(f"Total Debt: ${cf['total_debt_bn']}B (latest annual)")
            if facts_parts:
                lines.append(f"  Balance Sheet: {' | '.join(facts_parts)}")

        # Prior issuance history from deal memory
        if deal_history:
            history_text = format_issuer_history(d.get("issuer", ""), deal_history)
            if history_text:
                lines.append(history_text)

        lines.append("")

    return "\n".join(lines)


# ── Utilities ─────────────────────────────────────────────────────────────

def _load_prompt(filename):
    """Load a prompt template from env var (GitHub Actions) or file (local dev).

    Env var names: MARKET_NEWS_PROMPT (for market_news_prompt.txt)
                   NEW_ISSUES_PROMPT  (for new_issues_prompt.txt)
    """
    env_var_map = {
        "market_news_prompt.txt": "MARKET_NEWS_PROMPT",
        "new_issues_prompt.txt": "NEW_ISSUES_PROMPT",
    }
    env_var = env_var_map.get(filename)
    if env_var:
        value = os.getenv(env_var)
        if value:
            logger.info(f"Loaded prompt from env var {env_var}")
            return value

    path = Path(PROMPTS_DIR) / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path} and env var {env_var} not set. "
            f"Set {env_var} as a GitHub Secret or create the file locally."
        )
    return path.read_text(encoding="utf-8")


def _get_client():
    """Create and return a Gemini client. Raises if key is missing."""
    api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY environment variable is not set. "
            "Set it in your .env file or GitHub Secrets."
        )
    return genai.Client(api_key=api_key)
