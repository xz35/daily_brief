"""
test_podcast_publisher.py - no-API checks for RSS feed maintenance helpers.

Usage:
    python tests/test_podcast_publisher.py
"""

import os
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import podcast_publisher
from podcast_publisher import _sort_items_newest_first, _upsert_channel_image


def test_channel_image_cleanup():
    channel = ET.Element("channel")
    image = ET.SubElement(channel, "image")
    for _ in range(3):
        ET.SubElement(image, "url").text = "old"
        ET.SubElement(image, "title").text = "old"
        ET.SubElement(image, "link").text = "old"

    podcast_publisher.GITHUB_PAGES_BASE_URL = "https://xz35.github.io/daily_brief"
    _upsert_channel_image(channel)

    image = channel.find("image")
    assert len(image.findall("url")) == 1
    assert len(image.findall("title")) == 1
    assert len(image.findall("link")) == 1


def test_items_sort_by_parsed_pubdate():
    channel = ET.Element("channel")
    _add_item(channel, "old", "Tue, 19 May 2026 14:14:46 +0000")
    _add_item(channel, "new", "Mon, 25 May 2026 14:23:49 +0000")
    _add_item(channel, "middle", "Wed, 20 May 2026 14:16:28 +0000")

    _sort_items_newest_first(channel)

    titles = [item.findtext("title") for item in channel.findall("item")]
    assert titles == ["new", "middle", "old"]


def _add_item(channel, title, pub_date):
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "pubDate").text = pub_date


if __name__ == "__main__":
    test_channel_image_cleanup()
    test_items_sort_by_parsed_pubdate()
    print("All assertions passed.")
