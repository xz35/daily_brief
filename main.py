"""
main.py — master orchestration script for the Morning Audio Brief pipeline.

Run order:
  1. rss_scraper      → articles (list of dicts)
  2. edgar_fetcher    → edgar_deals (list of dicts)
  3. pr_scraper       → pr_deals (list of dicts), deduplicated against edgar
  4. synthesizer      → script (str), word_count (int)
  5. tts_converter    → mp3_path (str), duration (float)
  6. podcast_publisher → feed.xml updated, retention enforced

The pipeline never crashes on a single component failure. If synthesis or TTS
fails, the run log captures the error and exits with a non-zero code so GitHub
Actions marks the run as failed (alerting you).

Usage:
    python main.py                     # standard run (prior business day for EDGAR)
    python main.py --date 2026-03-14   # override date (testing only)
    python main.py --skip-tts          # synthesize only, no audio (useful for prompt testing)
"""

import argparse
import logging
import sys
import traceback

from dotenv import load_dotenv

load_dotenv(override=True)  # Load .env file for local development (no-op in GitHub Actions)

from utils import RunLog, setup_logging, today_str


def main():
    args = _parse_args()
    logger = setup_logging()
    run_log = RunLog()

    logger.info("=" * 60)
    logger.info(f"Morning Audio Brief pipeline starting — {today_str()}")
    logger.info("=" * 60)

    try:
        # ── Step 1: Fetch RSS articles ────────────────────────────────
        logger.info("Step 1: Fetching RSS articles")
        from rss_scraper import fetch_articles
        articles = fetch_articles()
        run_log.set("articles_fetched", len(articles))
        logger.info(f"  → {len(articles)} articles")

        # ── Step 1b: Fetch FRED market data ───────────────────────────
        logger.info("Step 1b: Fetching FRED bond market indicators")
        from fred_fetcher import fetch_market_data
        market_data = fetch_market_data()
        if market_data:
            logger.info(f"  → FRED data as of {market_data.get('as_of')}")
        else:
            logger.info("  → FRED data unavailable (key not set or fetch failed)")

        # ── Step 2: Fetch EDGAR new issues ────────────────────────────
        logger.info("Step 2: Fetching EDGAR FWP filings")
        from edgar_fetcher import fetch_deals
        edgar_deals = fetch_deals(date=args.date)
        logger.info(f"  → {len(edgar_deals)} EDGAR deals")

        # ── Step 3: Fetch supplemental PR deals ───────────────────────
        logger.info("Step 3: Fetching supplemental PR deals")
        from pr_scraper import fetch_supplemental_deals
        pr_deals = fetch_supplemental_deals(edgar_deals=edgar_deals)
        logger.info(f"  → {len(pr_deals)} supplemental PR deals")

        all_deals = edgar_deals + pr_deals
        run_log.set("deals_found", len(all_deals))
        logger.info(f"  → {len(all_deals)} total deals")

        # ── Step 3b: Load persistent memory ───────────────────────────
        logger.info("Step 3b: Loading deal history and market context")
        from deal_memory import load_deal_history, append_deals, save_deal_history
        from market_context import (
            load_market_context, format_prior_context,
            save_market_context, extract_context_summary,
        )
        deal_history = load_deal_history()
        context_entries = load_market_context()
        prior_context = format_prior_context(context_entries)
        logger.info(f"  → {len(deal_history)} prior deals, {len(context_entries)} days context")

        # ── Step 4: LLM synthesis ─────────────────────────────────────
        logger.info("Step 4: Synthesizing podcast script (2 Gemini calls)")
        from synthesizer import synthesize
        script, word_count = synthesize(
            articles, all_deals,
            market_data=market_data,
            prior_context=prior_context,
            deal_history=deal_history,
        )
        run_log.set("script_word_count", word_count)
        logger.info(f"  → {word_count} words")

        # ── Step 4b: Save persistent memory ───────────────────────────
        logger.info("Step 4b: Saving deal history and market context")
        updated_history = append_deals(all_deals, deal_history, date_str=today_str())
        save_deal_history(updated_history)
        context_summary = extract_context_summary(script)
        save_market_context(today_str(), context_summary, context_entries)

        # ── Step 4c: Email script archive ─────────────────────────────
        logger.info("Step 4c: Sending script to email archive")
        from email_sender import send_daily_brief
        email_sent = send_daily_brief(script, word_count, date_str=today_str())
        run_log.set("email_sent", email_sent)

        if args.skip_tts:
            logger.info("--skip-tts flag set, stopping before TTS")
            _print_script_preview(script)
            run_log.write()
            return 0

        # ── Step 5: Text-to-speech ────────────────────────────────────
        logger.info("Step 5: Converting to audio (Google Cloud TTS)")
        from tts_converter import convert_to_mp3
        mp3_path, duration = convert_to_mp3(script)
        run_log.set("mp3_duration_seconds", round(duration))
        logger.info(f"  → {mp3_path} ({duration/60:.1f} min)")

        # ── Step 6: Publish ───────────────────────────────────────────
        logger.info("Step 6: Publishing episode and updating feed")
        from podcast_publisher import publish
        feed_path = publish(mp3_path, duration)
        logger.info(f"  → Feed updated: {feed_path}")

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        run_log.add_error("Pipeline interrupted by user")
        run_log.write()
        return 1

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        logger.error(traceback.format_exc())
        run_log.add_error(f"Pipeline failed: {e}")
        run_log.write()
        return 1

    logger.info("=" * 60)
    logger.info("Pipeline complete — episode ready")
    logger.info("=" * 60)
    run_log.write()
    return 0


def _parse_args():
    parser = argparse.ArgumentParser(description="Morning Audio Brief pipeline")
    parser.add_argument(
        "--date",
        default=None,
        help="Override EDGAR query date (YYYY-MM-DD). Default: prior business day.",
    )
    parser.add_argument(
        "--skip-tts",
        action="store_true",
        help="Run synthesis only, skip TTS and publishing. Useful for prompt testing.",
    )
    return parser.parse_args()


def _print_script_preview(script):
    """Print the first and last 500 chars of the script for quick review."""
    print("\n" + "=" * 60)
    print("SCRIPT PREVIEW (first 500 chars):")
    print("=" * 60)
    print(script[:500])
    print("...")
    print("=" * 60)
    print("SCRIPT PREVIEW (last 500 chars):")
    print("=" * 60)
    print(script[-500:])
    print("=" * 60 + "\n")


if __name__ == "__main__":
    sys.exit(main())
