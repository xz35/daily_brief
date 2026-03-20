"""
tts_converter.py — convert podcast script to MP3 via Google Cloud TTS.

Splits long scripts into sentence-boundary chunks (max TTS_CHUNK_SIZE chars),
calls the TTS API for each chunk, and concatenates all chunks into a single MP3
using pydub. Handles Google Cloud credentials for both local dev and GitHub Actions.
"""

import io
import logging
import os
import re
import tempfile
from pathlib import Path

from pydub import AudioSegment

from config import (
    EPISODES_DIR,
    TTS_CHUNK_SIZE,
    TTS_LANGUAGE_CODE,
    TTS_PITCH,
    TTS_SPEAKING_RATE,
    TTS_VOICE_NAME,
)
from utils import today_str

logger = logging.getLogger(__name__)


def convert_to_mp3(script, output_date=None):
    """Convert a text script to an MP3 file.

    Args:
        script:      Full podcast script as a string.
        output_date: Date string (YYYY-MM-DD) for the filename. Defaults to today.

    Returns:
        tuple: (mp3_path: str, duration_seconds: float)
    """
    output_date = output_date or today_str()
    _setup_credentials()

    script = _normalize_for_tts(script)
    chunks = _split_into_chunks(script)
    logger.info(f"TTS: {len(script)} chars split into {len(chunks)} chunks")

    audio_segments = []
    for i, chunk in enumerate(chunks, 1):
        logger.info(f"TTS chunk {i}/{len(chunks)} ({len(chunk)} chars)")
        audio_data = _synthesize_chunk(chunk)
        if audio_data:
            segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            audio_segments.append(segment)

    if not audio_segments:
        raise RuntimeError("TTS produced no audio — all chunks failed")

    combined = audio_segments[0]
    for seg in audio_segments[1:]:
        combined += seg

    Path(EPISODES_DIR).mkdir(parents=True, exist_ok=True)
    mp3_path = os.path.join(EPISODES_DIR, f"{output_date}.mp3")
    combined.export(mp3_path, format="mp3", bitrate="128k")

    duration = len(combined) / 1000.0  # pydub uses milliseconds
    logger.info(f"MP3 saved: {mp3_path} ({duration:.0f}s / {duration/60:.1f}min)")

    return mp3_path, duration


# ── Credential setup ──────────────────────────────────────────────────────

def _setup_credentials():
    """Handle GCP credentials for local dev and GitHub Actions environments.

    Local dev: GOOGLE_APPLICATION_CREDENTIALS points to tts-service-account.json
    GitHub Actions: GOOGLE_APPLICATION_CREDENTIALS_JSON contains the full JSON
    """
    creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if creds_json:
        # Write JSON content to a temp file and point the SDK at it
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        tmp.write(creds_json)
        tmp.close()
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
        logger.info("GCP credentials loaded from GOOGLE_APPLICATION_CREDENTIALS_JSON")
    elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        logger.info(f"GCP credentials from: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")
    else:
        raise EnvironmentError(
            "Google Cloud credentials not found. Set either:\n"
            "  GOOGLE_APPLICATION_CREDENTIALS (local dev, path to JSON file)\n"
            "  GOOGLE_APPLICATION_CREDENTIALS_JSON (GitHub Actions, JSON contents)"
        )


# ── TTS text normalization ────────────────────────────────────────────────

def _normalize_for_tts(text):
    """Pre-process script text to fix TTS pronunciation of financial abbreviations.

    Apply longer/more-specific patterns first to avoid partial matches.
    E.g. BBB+ must be substituted before BBB, AA- before AA, etc.
    """
    substitutions = [
        # Triple-letter ratings (with modifiers first)
        (r'\bAAA\b',   'triple-A'),
        (r'\bBBB\+',   'triple-B plus'),
        (r'\bBBB-',    'triple-B minus'),
        (r'\bBBB\b',   'triple-B'),
        (r'\bCCC\+',   'triple-C plus'),
        (r'\bCCC-',    'triple-C minus'),
        (r'\bCCC\b',   'triple-C'),
        # Double-letter ratings
        (r'\bAA\+',    'double-A plus'),
        (r'\bAA-',     'double-A minus'),
        (r'\bAA\b',    'double-A'),
        (r'\bBB\+',    'double-B plus'),
        (r'\bBB-',     'double-B minus'),
        (r'\bBB\b',    'double-B'),
        (r'\bCC\b',    'double-C'),
        # Single-letter with modifier only (plain "A" is too ambiguous)
        (r'\bA\+\b',   'single-A plus'),
        (r'\bA-\b',    'single-A minus'),
        # Curve shorthand
        (r'\b2s10s\b', 'twos-tens'),
        (r'\b2s/10s\b','twos-tens'),
        (r'\b3m10y\b', 'three-month ten-year'),
        (r'\b5s30s\b', 'fives-thirties'),
        # Common abbreviations
        (r'\bbps\b',   'basis points'),
        (r'\bWoW\b',   'week over week'),
        (r'\bYoY\b',   'year over year'),
    ]
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)

    # Convert integers before "basis points" to spoken words
    # e.g. "176 basis points" → "one hundred seventy-six basis points"
    text = re.sub(
        r'\b(\d{1,3}) basis points',
        lambda m: f"{_int_to_words(int(m.group(1)))} basis points",
        text,
    )

    return text


def _int_to_words(n):
    """Convert integer 0–999 to spoken English words."""
    if n == 0:
        return 'zero'
    ones = [
        '', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
        'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
        'seventeen', 'eighteen', 'nineteen',
    ]
    tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + (f'-{ones[n % 10]}' if n % 10 else '')
    hundreds = ones[n // 100] + ' hundred'
    remainder = n % 100
    if remainder == 0:
        return hundreds
    return hundreds + ' ' + _int_to_words(remainder)


# ── Text chunking ─────────────────────────────────────────────────────────

def _split_into_chunks(text):
    """Split text into chunks at sentence boundaries, respecting TTS_CHUNK_SIZE.

    Never cuts mid-sentence. Always produces at least one chunk.
    """
    # Split into sentences
    sentences = _split_sentences(text)
    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # If a single sentence exceeds chunk size, split at word boundary
        if len(sentence) > TTS_CHUNK_SIZE:
            if current:
                chunks.append(current.strip())
                current = ""
            word_chunks = _split_by_words(sentence, TTS_CHUNK_SIZE)
            chunks.extend(word_chunks)
            continue

        if len(current) + len(sentence) + 1 > TTS_CHUNK_SIZE:
            if current:
                chunks.append(current.strip())
            current = sentence + " "
        else:
            current += sentence + " "

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


def _split_sentences(text):
    """Split text into sentences using punctuation as boundaries."""
    # Split on . ! ? followed by whitespace or end of string
    # But don't split on decimal numbers (3.5%) or abbreviations (U.S.)
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"])', text)
    return parts


def _split_by_words(text, max_chars):
    """Split a long string at word boundaries."""
    words = text.split()
    chunks = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = word + " "
        else:
            current += word + " "
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ── TTS API call ──────────────────────────────────────────────────────────

def _synthesize_chunk(text):
    """Call Google Cloud TTS for a single text chunk. Returns MP3 bytes or None."""
    # Import here so the module can be imported without credentials in tests
    from google.cloud import texttospeech

    try:
        client = texttospeech.TextToSpeechClient()

        synthesis_input = texttospeech.SynthesisInput(text=text)

        voice = texttospeech.VoiceSelectionParams(
            language_code=TTS_LANGUAGE_CODE,
            name=TTS_VOICE_NAME,
        )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=TTS_SPEAKING_RATE,
            pitch=TTS_PITCH,
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        return response.audio_content

    except Exception as e:
        logger.error(f"TTS API call failed: {e}")
        return None
