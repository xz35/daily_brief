"""
podcast_publisher.py — manage the podcast RSS feed and episode files.

Responsibilities:
  1. Copy the new MP3 into docs/episodes/
  2. Add the new episode entry to docs/feed.xml
  3. Enforce 5-day rolling retention (delete old MP3s + their feed entries)

The git commit/push is handled by the GitHub Actions workflow, not here.
For local testing, call publish() and then commit manually.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from config import (
    AUDIO_RETENTION_DAYS,
    DOCS_DIR,
    EPISODES_DIR,
    FEED_PATH,
    GITHUB_PAGES_BASE_URL,
    PODCAST_AUTHOR,
    PODCAST_DESCRIPTION,
    PODCAST_LANGUAGE,
    PODCAST_TITLE,
)
from utils import today_str

logger = logging.getLogger(__name__)

# Register iTunes namespace so it round-trips cleanly
ET.register_namespace("itunes", "http://www.itunes.com/dtunes/podcast/1.0")
ET.register_namespace("", "http://www.rssboard.org/rss-specification")

ITUNES_NS = "http://www.itunes.com/dtunes/podcast/1.0"


def publish(mp3_path, duration_seconds, date=None):
    """Add today's episode to the feed and enforce retention policy.

    Args:
        mp3_path:         Path to the generated MP3 file.
        duration_seconds: Episode duration (float).
        date:             Episode date (YYYY-MM-DD). Defaults to today.

    Returns:
        str: Path to the updated feed.xml
    """
    date = date or today_str()

    Path(EPISODES_DIR).mkdir(parents=True, exist_ok=True)

    # Ensure the MP3 is in the right place
    episode_filename = f"{date}.mp3"
    episode_path = os.path.join(EPISODES_DIR, episode_filename)
    if os.path.abspath(mp3_path) != os.path.abspath(episode_path):
        import shutil
        shutil.copy2(mp3_path, episode_path)
        logger.info(f"Copied MP3 to {episode_path}")

    mp3_size = os.path.getsize(episode_path)
    episode_url = _episode_url(episode_filename)

    # Update feed
    feed_path = _update_feed(date, episode_url, mp3_size, duration_seconds)

    # Enforce retention
    _enforce_retention()

    logger.info(f"Published: {episode_url}")
    return feed_path


# ── Feed management ───────────────────────────────────────────────────────

def _update_feed(date, episode_url, mp3_size, duration_seconds):
    """Add a new episode entry to feed.xml, creating it if needed."""
    feed_path = FEED_PATH

    if os.path.exists(feed_path):
        tree = ET.parse(feed_path)
        root = tree.getroot()
        channel = root.find("channel")
    else:
        root, channel = _create_empty_feed()
        tree = ET.ElementTree(root)

    # Upsert channel-level image tags (safe to run on every update)
    _upsert_channel_image(channel)

    # Remove any existing entry for this date (idempotent re-runs)
    for item in channel.findall("item"):
        guid = item.findtext("guid")
        if guid and date in guid:
            channel.remove(item)
            logger.info(f"Removed existing entry for {date}")

    # Build new item
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = f"Morning Brief — {date}"
    ET.SubElement(item, "description").text = (
        f"Daily market briefing for {date}. "
        "Macro, rates, credit, and IG new issues."
    )
    ET.SubElement(item, "pubDate").text = _rfc2822_date(date)
    ET.SubElement(item, "guid", isPermaLink="false").text = episode_url

    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set("url", episode_url)
    enclosure.set("length", str(mp3_size))
    enclosure.set("type", "audio/mpeg")

    ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = _format_duration(duration_seconds)
    ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "no"

    # Move channel's items: new episode first, then existing
    _sort_items_newest_first(channel)

    Path(DOCS_DIR).mkdir(exist_ok=True)
    _write_feed(tree, feed_path)
    logger.info(f"Feed updated: {feed_path}")
    return feed_path


def _create_empty_feed():
    """Create a minimal valid podcast RSS 2.0 feed structure."""
    root = ET.Element("rss", version="2.0")
    root.set("xmlns:itunes", ITUNES_NS)

    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = PODCAST_TITLE
    ET.SubElement(channel, "description").text = PODCAST_DESCRIPTION
    ET.SubElement(channel, "link").text = GITHUB_PAGES_BASE_URL or "https://github.com"
    ET.SubElement(channel, "language").text = PODCAST_LANGUAGE
    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = PODCAST_AUTHOR
    ET.SubElement(channel, f"{{{ITUNES_NS}}}explicit").text = "no"
    ET.SubElement(channel, f"{{{ITUNES_NS}}}category", text="Business")

    # Add Logo
    image_url = f"{GITHUB_PAGES_BASE_URL.rstrip('/')}/logo.png"
    itunes_image = ET.SubElement(channel, f"{{{ITUNES_NS}}}image")
    itunes_image.set("href", image_url)
    
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = image_url
    ET.SubElement(image, "title").text = PODCAST_TITLE
    ET.SubElement(image, "link").text = GITHUB_PAGES_BASE_URL or "https://github.com"

    return root, channel


def _upsert_channel_image(channel):
    """Add or update <itunes:image> and <image> tags in the channel header.

    Tags are inserted before the first <item> so they appear in the channel
    metadata block — Apple Podcasts ignores image tags placed after items.
    """
    if not GITHUB_PAGES_BASE_URL:
        return
    image_url = f"{GITHUB_PAGES_BASE_URL.rstrip('/')}/logo.png"

    # Find insertion point: just before the first <item>
    children = list(channel)
    items = channel.findall("item")
    insert_at = children.index(items[0]) if items else len(children)

    # itunes:image — used by Apple Podcasts and most modern apps
    itunes_image = channel.find(f"{{{ITUNES_NS}}}image")
    if itunes_image is None:
        itunes_image = ET.Element(f"{{{ITUNES_NS}}}image")
        channel.insert(insert_at, itunes_image)
    itunes_image.set("href", image_url)

    # RSS 2.0 <image> — fallback for older clients
    image = channel.find("image")
    if image is None:
        image    = ET.Element("image")
        url_el   = ET.SubElement(image, "url")
        title_el = ET.SubElement(image, "title")
        link_el  = ET.SubElement(image, "link")
        channel.insert(insert_at, image)
    else:
        url_el   = image.find("url")   or ET.SubElement(image, "url")
        title_el = image.find("title") or ET.SubElement(image, "title")
        link_el  = image.find("link")  or ET.SubElement(image, "link")
    url_el.text   = image_url
    title_el.text = PODCAST_TITLE
    link_el.text  = GITHUB_PAGES_BASE_URL or "https://github.com"


def _sort_items_newest_first(channel):
    """Re-order <item> elements in channel so newest pubDate is first."""
    items = channel.findall("item")
    for item in items:
        channel.remove(item)
    items.sort(key=lambda el: el.findtext("pubDate") or "", reverse=True)
    for item in items:
        channel.append(item)


def _write_feed(tree, path):
    """Write the ElementTree to disk with proper XML declaration."""
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


# ── Retention policy ──────────────────────────────────────────────────────

def _enforce_retention():
    """Delete MP3 files and feed entries older than AUDIO_RETENTION_DAYS business days."""
    cutoff = _retention_cutoff_date()
    episodes_dir = Path(EPISODES_DIR)

    deleted = []
    for mp3_file in sorted(episodes_dir.glob("*.mp3")):
        file_date_str = mp3_file.stem   # filename is YYYY-MM-DD.mp3
        try:
            file_date = datetime.strptime(file_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if file_date < cutoff:
            mp3_file.unlink()
            deleted.append(file_date_str)
            logger.info(f"Deleted old episode: {mp3_file.name}")

    if deleted:
        _remove_feed_entries(deleted)


def _retention_cutoff_date():
    """Return the earliest date to keep (today minus AUDIO_RETENTION_DAYS business days)."""
    d = datetime.today().date()
    days_back = 0
    while days_back < AUDIO_RETENTION_DAYS:
        d -= timedelta(days=1)
        if d.weekday() < 5:   # weekday only
            days_back += 1
    return d


def _remove_feed_entries(date_strings):
    """Remove feed items whose GUIDs contain any of the given date strings."""
    if not os.path.exists(FEED_PATH):
        return

    tree = ET.parse(FEED_PATH)
    root = tree.getroot()
    channel = root.find("channel")

    for item in list(channel.findall("item")):
        guid = item.findtext("guid") or ""
        if any(d in guid for d in date_strings):
            channel.remove(item)
            logger.info(f"Removed feed entry for old episode")

    _write_feed(tree, FEED_PATH)


# ── Utilities ─────────────────────────────────────────────────────────────

def _episode_url(filename):
    """Construct the public URL for an episode MP3."""
    base = GITHUB_PAGES_BASE_URL.rstrip("/")
    return f"{base}/episodes/{filename}"


def _rfc2822_date(date_str):
    """Convert YYYY-MM-DD to RFC 2822 date string required by RSS spec."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%a, %d %b %Y 12:00:00 +0000")


def _format_duration(seconds):
    """Format seconds as HH:MM:SS for iTunes duration tag."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
