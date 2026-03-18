"""
config.py — all configuration constants in one place.
Edit this file to tune sources, filters, and API settings.
"""

import os

# ── Scheduling ────────────────────────────────────────────────────────────
LOOKBACK_HOURS = 24          # RSS articles: how far back to look
AUDIO_RETENTION_DAYS = 5     # How many days of MP3s to keep

# ── Content limits ────────────────────────────────────────────────────────
MAX_RSS_ARTICLES = 20        # Max articles to pass to LLM
MAX_DEALS_PER_EPISODE = 8    # Cap deal entries on very heavy issuance days

# ── RSS Sources ───────────────────────────────────────────────────────────
# Last validated: March 2026
# Reuters feeds removed (DNS failure). WSJ removed (0 articles, likely paywalled).
RSS_FEEDS = [
    # News wires & financial press
    ("Financial Times",         "https://www.ft.com/rss/home"),
    ("Bloomberg Markets",       "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg Economics",     "https://feeds.bloomberg.com/economics/news.rss"),
    ("CNBC Finance",            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("CNBC Economy",            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("Axios Markets",           "https://api.axios.com/feed/"),
    # Central banks & official institutions
    ("Federal Reserve",         "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("NY Fed Liberty Street",   "https://www.newyorkfed.org/feeds/research"),
    ("BIS Publications",        "https://www.bis.org/doclist/bis_fsi_publs.rss"),
    ("IMF Blog",                "https://blogs.imf.org/feed/"),
    # Economic data & analysis
    ("BLS Economic Releases",   "https://www.bls.gov/feed/bls_latest.rss"),
    ("FRED Blog",               "https://fredblog.stlouisfed.org/feed/"),
    ("Calculated Risk",         "https://www.calculatedriskblog.com/feeds/posts/default"),
    # Markets / real estate
    ("MarketWatch Real Estate", "https://feeds.content.dowjones.io/public/rss/mw_realestate"),
    # Economics blogs
    ("Marginal Revolution",     "https://marginalrevolution.com/feed"),
]

# Keywords scored against article title + summary.
# Higher match count = higher priority. Tuned for a senior institutional
# quant PM running systematic credit with deep macro/equities background.
TOPIC_KEYWORDS = [
    # Rates & monetary policy
    "Fed", "FOMC", "monetary policy", "rate cut", "rate hike", "basis points",
    "treasury", "yield", "yield curve", "2-year", "10-year", "30-year",
    "real rates", "breakeven", "TIPS", "QT", "QE", "balance sheet",
    # Credit
    "credit", "spreads", "IG", "investment grade", "high yield", "HY",
    "bond", "debt", "corporate", "default", "downgrade", "upgrade",
    "CMBS", "CLO", "CDX", "OAS", "option-adjusted spread",
    # Macro / economic data
    "inflation", "CPI", "PCE", "GDP", "NFP", "payroll", "employment",
    "labor market", "recession", "soft landing", "hard landing",
    "PMI", "ISM", "retail sales", "housing", "mortgage",
    # Equities & cross-asset
    "S&P", "equity", "equities", "VIX", "volatility", "risk-off", "risk-on",
    "earnings", "EPS", "margin", "buyback",
    # Quant / systematic
    "factor", "momentum", "carry", "value", "quality", "systematic",
    "quantitative", "dispersion", "correlation", "beta",
    # Global / macro
    "dollar", "DXY", "currency", "FX", "emerging markets", "EM",
    "China", "ECB", "Bank of England", "BOJ", "central bank",
    # Real estate / structured
    "real estate", "REIT", "CMBS", "mortgage", "MBS", "ABS",
]

# ── EDGAR ─────────────────────────────────────────────────────────────────
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_USER_AGENT = "MorningAudioBrief xiaoyu.zheng@gmail.com"

IG_RATING_INDICATORS = [
    # Moody's
    "Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "Baa3",
    # S&P / Fitch
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
]

# ── Press Release Feeds ───────────────────────────────────────────────────
PR_FEEDS = [
    ("Business Wire Finance",   "https://www.businesswire.com/rss/home/?rss=G22"),
    ("GlobeNewswire Finance",   "https://www.globenewswire.com/RssFeed/subjectcode/08-Public+Offerings+%26+Listing/"),
]

PR_BOND_KEYWORDS = [
    "senior notes", "notes due", "bond offering", "debt securities",
    "fixed rate notes", "floating rate notes", "term loan b",
    "144a", "reg s", "investment grade", "aggregate principal",
    "bookrunners", "joint book", "pricing supplement",
]

# ── FRED ──────────────────────────────────────────────────────────────────
# Free API key from https://fred.stlouisfed.org/docs/api/api_key.html
# Optional — pipeline degrades gracefully if not set (no market data numbers)
FRED_API_KEY = os.getenv("FRED_API_KEY")

# ── Email (daily script archive) ──────────────────────────────────────────
# Optional — pipeline runs without this. If set, sends the script as HTML email
# to the dedicated project Gmail account after each synthesis run.
# GMAIL_APP_PASSWORD must be a 16-char app password, NOT your regular password.
# Generate at: myaccount.google.com → Security → 2-Step Verification → App passwords
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# ── Gemini ────────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_MAX_TOKENS = 8192
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ── Google Cloud TTS ──────────────────────────────────────────────────────
TTS_VOICE_NAME = "en-US-Neural2-D"    # Male. Change to en-US-Neural2-F for female.
TTS_LANGUAGE_CODE = "en-US"
TTS_SPEAKING_RATE = 1.08              # Slightly faster than default
TTS_PITCH = 0.0
TTS_CHUNK_SIZE = 4800                 # Characters per TTS call (hard limit is 5000)

# ── Podcast metadata ──────────────────────────────────────────────────────
PODCAST_TITLE = "Morning Audio Brief"
PODCAST_DESCRIPTION = (
    "Daily market briefing — macro, rates, credit, and IG new issues. "
    "Calibrated for a senior institutional systematic credit PM."
)
PODCAST_AUTHOR = "Morning Brief"
PODCAST_LANGUAGE = "en-us"

# ── File paths ────────────────────────────────────────────────────────────
DOCS_DIR = "docs"
EPISODES_DIR = "docs/episodes"
FEED_PATH = "docs/feed.xml"
PROMPTS_DIR = "prompts"
LOGS_DIR = "logs"

GITHUB_PAGES_BASE_URL = os.getenv("GITHUB_PAGES_BASE_URL", "")
