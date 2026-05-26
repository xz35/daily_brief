# Morning Audio Brief

Automated daily podcast generated every weekday morning with enough buffer to be available by 5:30am PT. Covers macro and credit market news, Treasury yield curve analysis, and a detailed prior-day investment grade bond new issues report.

Runs on GitHub Actions. Hosted on GitHub Pages. Cost: $0.

## Podcast Feed

`https://xz35.github.io/daily_brief/feed.xml`

## Production Schedule

GitHub Actions runs two weekday schedules in the `America/Los_Angeles` timezone:

- Primary run: 2:15am PT, Monday-Friday
- Backup run: 3:15am PT, Monday-Friday

The early primary run leaves room for GitHub Actions scheduled-workflow delays and Apple Podcasts polling before the 5:30am PT listening target. The backup run is harmless if the primary run already published: scheduled runs check for `docs/episodes/YYYY-MM-DD.mp3` and skip generation when today's episode is already committed.

Apple Podcasts background downloads still depend on the phone being charged, connected to Wi-Fi unless cellular downloads are allowed, and not in Low Power Mode.

## GitHub Secrets Required

Add these in repo Settings → Secrets and variables → Actions:

| Secret | Description |
|--------|-------------|
| `GEMINI_API_KEY` | From billing-free Google AI Studio (aistudio.google.com) |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Full JSON of TTS service account key |
| `PAGES_BASE_URL` | `https://xz35.github.io/daily_brief` |
| `FRED_API_KEY` | Free key from fred.stlouisfed.org |
| `GMAIL_ADDRESS` | xdailybrief@gmail.com |
| `GMAIL_APP_PASSWORD` | 16-char Gmail app password |
| `MARKET_NEWS_PROMPT` | Full text of market news prompt (kept private) |
| `NEW_ISSUES_PROMPT` | Full text of new issues prompt (kept private) |

## Local Development

```bash
cp .env.example .env
# Fill in credentials in .env

pip install -r requirements.txt

# Test individual modules
python tests/test_rss.py
python tests/test_edgar.py
python tests/test_synthesizer.py

# Full run (synthesis only, no audio)
python main.py --skip-tts

# Full run
python main.py
```

## Notes

- Two Google accounts required: billing-free AI Studio for Gemini, billing-enabled GCP for TTS
- Prompts are gitignored and stored as GitHub Secrets — update the Secrets when changing prompts
- See PROGRESS.md and IDEAS.md (parent directory) for full project context
