"""
utils.py — shared utilities used across all modules.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOGS_DIR


def prior_business_day(reference_date=None):
    """Return the prior business day as a YYYY-MM-DD string.

    Args:
        reference_date: datetime to count back from. Defaults to today.
    """
    d = reference_date or datetime.today()
    d -= timedelta(days=1)
    while d.weekday() >= 5:   # 5 = Saturday, 6 = Sunday
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def today_str():
    """Return today's date as YYYY-MM-DD."""
    return datetime.today().strftime("%Y-%m-%d")


def setup_logging():
    """Configure logging to console and a dated log file. Returns root logger."""
    Path(LOGS_DIR).mkdir(exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"{today_str()}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    return logging.getLogger("morning_brief")


class RunLog:
    """Accumulates structured run statistics and writes a JSON log at the end.

    Usage:
        run_log = RunLog()
        run_log.set("articles_fetched", 15)
        run_log.add_error("EDGAR timeout on filing XYZ")
        run_log.write()
    """

    def __init__(self):
        self.data = {
            "date": today_str(),
            "articles_fetched": 0,
            "deals_found": 0,
            "script_word_count": 0,
            "tts_chunks": 0,
            "mp3_duration_seconds": 0,
            "errors": [],
            "warnings": [],
        }

    def set(self, key, value):
        self.data[key] = value

    def add_error(self, msg):
        logging.getLogger("morning_brief").error(msg)
        self.data["errors"].append(msg)

    def add_warning(self, msg):
        logging.getLogger("morning_brief").warning(msg)
        self.data["warnings"].append(msg)

    def write(self):
        Path(LOGS_DIR).mkdir(exist_ok=True)
        path = os.path.join(LOGS_DIR, f"{today_str()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        logging.getLogger("morning_brief").info(f"Run log written to {path}")
