# Morning Audio Brief

Automated daily podcast generated every weekday morning with enough buffer to be available by 5:30am PT. Covers macro and credit market news, Treasury yield curve analysis, and a detailed prior-day investment grade bond new issues report.

Runs on GitHub Actions. Hosted on GitHub Pages. Cost: $0.

## Podcast Feed

`https://xz35.github.io/daily_brief/feed.xml`

## Production Schedule

GitHub's `schedule` events are best-effort (routinely 15-60+ min late, occasionally dropped), so the primary trigger is external:

- **Primary: cron-job.org** fires `workflow_dispatch` via the GitHub API at 4:00am PT, Monday-Friday — exact-time triggering.
- **Backups: GitHub Actions schedules** at 4:30am PT and 5:00am PT, Monday-Friday, in the `America/Los_Angeles` timezone. These only generate when the primary run failed (e.g. sustained Gemini overload) — otherwise they see the committed episode and skip.

All runs check for `docs/episodes/YYYY-MM-DD.mp3` and skip when today's episode is already committed, so the backups are harmless when the primary succeeded. A failed synthesis aborts before publishing (no junk MP3 is committed), which lets the next backup run retry. To force a regeneration, dispatch the workflow manually with `force: true`.

### cron-job.org trigger setup

1. Create a fine-grained GitHub PAT (Settings → Developer settings → Fine-grained tokens) scoped to the `daily_brief` repo only, with **Actions: Read and write** permission.
2. On cron-job.org, create a job with schedule `Mon-Fri 4:00am` (America/Los_Angeles):
   - URL: `https://api.github.com/repos/xz35/daily_brief/actions/workflows/morning_brief.yml/dispatches`
   - Method: `POST`
   - Headers: `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`
   - Body: `{"ref": "main"}`
3. A successful trigger returns HTTP 204 with an empty body.

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
