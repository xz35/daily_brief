"""
test_tts.py — test Google Cloud TTS with a short script.

Requires GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS_JSON
to be set. Run this after setting up your GCP service account.

Usage:
    python tests/test_tts.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from tts_converter import convert_to_mp3
from utils import setup_logging

logger = setup_logging()

# Short test script (~200 words) — fast and cheap to TTS
TEST_SCRIPT = """
Good morning. Today is a test of the Morning Audio Brief text-to-speech system.

Let's begin with a quick market check. The ten-year Treasury yield sits just above four
and a half percent, with the two-ten spread hovering near flat as markets digest the
latest FOMC minutes. Credit spreads in investment grade are broadly unchanged, with the
CDX Investment Grade index trading in the mid-fifties.

In new issues, the prior day saw a handful of high-quality corporate borrowers come to
market. A major financial institution priced a two billion dollar five-year offering at
Treasury plus eighty basis points, which came at the tight end of initial price talk.
The deal was multiple times oversubscribed, reflecting solid technical conditions in
the investment grade primary market.

Looking ahead, the key watch item today is the morning's CPI print. A hotter-than-expected
number could push yields higher and widen spreads, while a soft print would likely see
the market rally. Either way, this is the number that matters today.

That's your morning brief. Have a good session.
""".strip()


def test_tts():
    print("\n" + "=" * 60)
    print("TTS CONVERTER TEST")
    print("=" * 60)
    print(f"\nTest script: {len(TEST_SCRIPT)} chars, ~{len(TEST_SCRIPT.split())} words")

    mp3_path, duration = convert_to_mp3(TEST_SCRIPT, output_date="test")

    print(f"\nOutput file: {mp3_path}")
    print(f"Duration:    {duration:.1f} seconds ({duration/60:.1f} min)")
    print(f"File size:   {os.path.getsize(mp3_path):,} bytes")

    assert os.path.exists(mp3_path), "MP3 file not created"
    assert os.path.getsize(mp3_path) > 10_000, "MP3 file suspiciously small"
    assert duration > 30, f"Duration too short: {duration:.1f}s"

    print("\nAll assertions passed.")
    print(f"\nListen to the file to verify quality: {mp3_path}")
    return True


if __name__ == "__main__":
    success = test_tts()
    sys.exit(0 if success else 1)
