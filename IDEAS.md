# Morning Audio Brief — Ideas, Decisions, and Future Enhancements

This file tracks feature ideas, architectural decisions, and things considered but deferred.
It is not a to-do list — see PROGRESS.md for active work.

---

## Implemented

### EDGAR Company Facts API (implemented March 2026)
Balance sheet enrichment for new issue deals: fetches revenue and total debt from
`data.sec.gov/api/xbrl/companyfacts/CIK{}.json` and attaches to each deal dict.
Passed to the LLM as concrete numbers rather than adjectives. Makes the credit
commentary substantially more grounded.

### Cross-Episode Continuity (implemented March 2026)
Two persistent stores:
- `docs/deal_history.json` — prior issuer deals, used for "this is their third deal
  this year" context in Segment 3.
- `docs/market_context.json` — rolling 5-day market themes extracted from each script,
  used to give the LLM continuity across sessions ("continuing the steepening theme
  from earlier this week...").

### Full Treasury Yield Curve + Historical Analytics (implemented March 2026)
Fetches 11 curve points from FRED (1M through 30Y). Stores daily snapshots in
`docs/curve_history.json` (90-day rolling window). On each run, `curve_history.py`
pre-computes a narrative-ready analytics block: shape characterization, key spreads
(2s10s, 3m-10y, 5s30s), and 1-week / 1-month trend changes with bear/bull
steepening/flattening classification.

Design principle: raw numbers go in, narrative analysis comes out. The LLM synthesizes
commentary from the analytics block — it never sees a raw yield table. This prevents
the brief from becoming a recitation of numbers; it produces genuinely interesting
curve commentary with historical context.

### Substack Feeds (implemented March 2026)
Added Net Interest (Marc Rubinstein — banking/credit/financials) and Fed Guy
(Joseph Wang — Fed operations, repo, Treasury market plumbing). Both publish
weekly so expect 0 articles on most days — that's normal.

### Voice: en-US-Neural2-F (implemented March 2026)
Switched from Neural2-D (male) to Neural2-F (female). The Journey-* voices are
more natural but newer and less stable — stick with Neural2 for production.

---

## Considered and Deferred

### Rating Agency RSS Feeds (Moody's / S&P / Fitch)
**Decision: not worth pursuing on free tier.**

The major rating agencies don't publish free RSS feeds for rating actions. Their
press release pages are either paywalled or structured in ways that make scraping
fragile and high-maintenance.

What we already catch: the EDGAR deal documents include current ratings for each
issuer, so new issue commentary naturally incorporates ratings context. For
sector-wide downgrade/upgrade waves, these typically surface in the RSS news feeds
(Bloomberg, FT, CNBC) anyway.

Revisit if a reliable free source appears, or if the pipeline moves to a paid tier.

### 144A Bond Coverage
**Decision: known gap, no easy free solution.**

EDGAR only covers SEC-registered offerings (FWP / 424B5). 144A deals don't file
on EDGAR. The PR scraper (Business Wire / GlobeNewswire) is intended to catch some
of these, but it's unreliable and frequently returns nothing useful.

144A deals make up a meaningful portion of IG supply, especially for financial
issuers and high-frequency borrowers. This is a genuine gap in coverage.

Potential future approaches:
- TRACE/FINRA data (requires institutional access)
- Sell-side syndicate feeds (requires email integration, planned for v1.5)
- More aggressive Business Wire scraping (fragile, not recommended)

### Gmail / Sell-Side Research Ingestion (planned v1.5)
Corporate email forwarding unavailable in v1.0. Sell-side morning notes and
syndicate runs would significantly improve new issue coverage and depth. Requires
Gmail API setup and an email forwarding rule from the primary work account.

### Claude API vs Gemini
Gemini Flash free tier (1,500 req/day) is the right call for v1.0 — zero cost.
Switching to Claude API is ~10 lines of code in synthesizer.py and would cost
roughly $1–2/month at 2 calls/day. Worth revisiting when the free tier becomes
a constraint or when Claude's synthesis quality is noticeably better for this
specific use case.

### Additional Substack Feeds
Candidates worth considering if RSS feed roster needs expansion:
- Doomberg (energy/commodities, but paywalled)
- Kyla Scanlon (accessible macro, good at narrative)
- The Transcript (earnings call excerpts — useful for credit channel checks)
- Alfonso Peccatiello / Macro Compass (rates/macro, sometimes gated)

---

## Under Consideration (Not Yet Implemented)

### TTS Text Normalization — Pronunciation Fixes (implemented March 2026)
**Problem:** The TTS engine mispronounces certain financial text patterns:
- Rating tiers: "BBB" is read as "B-B-B" rather than "triple-B". Similarly "AA" → "A-A", "CCC" → "C-C-C"
- Numbers before "basis points": "176 basis points" is sometimes read as "one-seven-six basis points" rather than "one hundred seventy-six basis points" (may be a chunking boundary artifact)
- Potentially others: ticker symbols, abbreviations like "WoW" (week-over-week), "YoY", "IG", "HY"

**Proposed approach:** A `_normalize_for_tts(text)` pre-processing step in `tts_converter.py` that runs regex substitutions on the script before chunking and sending to the TTS API. Examples:
- `BBB` → `triple-B` | `BB` → `double-B` | `CCC` → `triple-C` | `AAA` → `triple-A` | `AA` → `double-A`
- `(\d+) basis points` → spelled-out number + ` basis points` (using Python's `num2words` library or a hand-rolled mapping for common values)
- May also want: `bps` → `basis points`, `WoW` → `week over week`, etc.

**Complexity:** Low. Purely a text pre-processing function, no API changes, no new credentials. The main work is building a comprehensive substitution list and testing edge cases (e.g., "A-rated" should not become "triple-A-rated").

**Decision (locked in):** Normalization layer in `tts_converter.py`, applied before chunking. The LLM approach (instructing Gemini to write TTS-friendly text) is less reliable — the LLM will occasionally revert to abbreviations regardless. The pre-processing layer is the guaranteed fix. Order matters: apply longer patterns first (e.g., `BBB+` before `BBB`) to avoid partial matches.

---

### Manual Research Email Forwarding — Research Digest Segment (implemented March 2026)
**Concept:** When you come across an interesting sell-side research report you don't have time to read, forward it to `xdailybrief@gmail.com`. The daily pipeline picks it up, summarizes the content, and adds it as a dedicated segment in that morning's brief.

**Why this version is different from what was removed:** The original idea was auto-forwarding a firehose of sell-side research. This is manually curated — you forward only what you actually want summarized, on an ad-hoc basis. Much higher signal-to-noise, much lower implementation risk.

**Design decisions (locked in):**
- Dedicated segment (Segment 5 — Research Digest), placed after Themes/So What (Segment 4) and before the outro. Omitted entirely on days with no forwarded emails.
- Multiple forwarded reports → multiple entries within the segment, each with its own summary and citation.
- Source attribution: Gemini infers the bank/firm name from the email body (branding, headers, disclaimers). If it cannot identify the source, graceful degradation — cite as "from an unnamed research source" or omit attribution. Do not attempt to parse subject line for source name.
- 24h window for email pickup. Friday forwards will not carry over to Monday — acceptable given research on Fridays is rare.
- Text-only extraction. Charts/images embedded in HTML emails are lost. This is acceptable.

**Architecture (fully planned, ready to build):**

1. **New module: `email_fetcher.py`**
   - Connects to `xdailybrief@gmail.com` via IMAP using existing `GMAIL_APP_PASSWORD` (app passwords work for IMAP reads — no new credentials needed)
   - Filters to unread emails from whitelisted senders in the last 24h
   - Parses HTML body: strip tags, extract readable text
   - Marks each email as read after processing (prevents re-processing on next run)
   - Returns list of `{subject, body_text}` dicts

2. **Sender filtering**
   - New env var / GitHub Secret: `RESEARCH_SENDER_WHITELIST` — comma-separated list of your forwarding email addresses (e.g., `xiaoyu.zheng@gmail.com,work@example.com`)
   - Daily transcript emails (sent from `xdailybrief@gmail.com` to itself) excluded automatically since that address won't be on the whitelist
   - Unread filter ensures each email is processed exactly once

3. **New Gemini call (Gemini call 3 — optional)**
   - Only fires if `email_fetcher.py` returns at least one email
   - New prompt: `research_digest_prompt.txt` (stored as GitHub Secret `RESEARCH_DIGEST_PROMPT`)
   - Prompt instructs Gemini to: identify the source firm from the content, write 150–250 words per report in podcast-style spoken prose, lead with "In a note from [Firm]..." or equivalent, cover the key thesis and any concrete data cited, and close with one implication for credit markets
   - All reports passed in a single Gemini call; each gets a separate entry

4. **`synthesizer.py` changes**
   - `synthesize()` gains a `research_emails` parameter
   - New `_call_research_digest()` function — third Gemini call, returns segment text or empty string
   - `_assemble_script()` appends research segment before outro if non-empty

5. **`main.py` changes**
   - New Step 1d: `from email_fetcher import fetch_research_emails` — runs after FRED, independent of other steps
   - `research_emails` passed into `synthesize()`
   - `run_log` tracks `research_reports_found`

6. **New GitHub Secret: `RESEARCH_SENDER_WHITELIST`**

**Complexity:** Medium. IMAP + `email` stdlib is well-understood. Main risks: HTML email parsing quality varies by sender (some research reports have complex layouts), and correctly handling the case where the inbox has unexpected content. No new credentials beyond the whitelist secret.

**Testing note:** Will need careful testing against the live inbox. Should run with a `--skip-tts` dry run first to verify email pickup and summarization before committing to a full production run.

---

## Open Questions / Future Ideas

- **TTS voice options**: Journey-F is more natural than Neural2-F but was less
  stable at time of v1.0 build. Re-evaluate periodically.
- **Episode length calibration**: Target 8–12 minutes. If deals are sparse,
  the episode runs short. Consider adding a "slow news day" padding strategy
  or an explicit minimum word count signal to the prompt.
- **Prompt versioning**: Currently prompts are in GitHub Secrets (MARKET_NEWS_PROMPT,
  NEW_ISSUES_PROMPT). As prompts mature, consider a versioning/changelog approach
  so it's clear what changed and when.
- **Weekend edition**: A Saturday brief covering the full week might be valuable.
  Requires changing the cron schedule and adjusting lookback windows.
